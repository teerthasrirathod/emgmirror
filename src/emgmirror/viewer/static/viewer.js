/* viewer.js — EMG signal viewer: recordings (closed or still being written) on a scrollable, zoomable
 * timeline, with envelope, detected activity, spectrum, statistics and the CT forearm in sync.
 * Signal maths lives in emgcore.js (window.EMG); this file is UI only.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const COL = ["#ff6b6b", "#4ea1ff", "#3fb950", "#d2a8ff"];
  const GREY = [128, 134, 144];
  const CLIENT = Math.random().toString(36).slice(2, 10);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("emgv." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("emgv." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  const S = {
    rid: null, meta: null, fs: 2000, nch: 0, n: 0, status: "", nextRow: 0, polling: false, lagMs: null, pollTimer: null, metaTimer: null,
    pin: [], live: [], liveF: [], clean: null,
    env: [], ratio: [], acts: [], envMax: [],
    t0: 0, t1: 10, cursor: 0, sel: null, playing: false, speed: 1, loop: false,
    follow: store.get("follow", true), mode: store.get("mode", "clean"), units: store.get("units", "pin"),
    opts: { env_window_s: 0.1, on_x: 3, off_x: 2, min_s: 0.3, merge_s: 0.25 },
    floor: [], floorDefault: [], floorSrc: "", gainY: [], specOn: false, specCh: 0, selMode: false,
    dirty: true, derivedDirty: true, lastDerive: 0, statsTimer: null, specCache: null,
  };

  // ------------------------------------------------------------------ helpers
  const fmt = (v, d = 1) => (v === null || v === undefined || !isFinite(v)) ? "–" : Number(v).toFixed(d);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const dur = () => S.n / S.fs;
  function unitK() { return S.units === "skin" ? 1000 / (S.meta ? S.meta.gain_nominal : 1) : 1; }
  function unitName() { return S.units === "skin" ? "µV at skin (est.)" : "mV at pin"; }
  function procOf(c) { return S.clean ? S.clean[c] : (S.live[c] ? S.live[c].view() : new Float32Array(0)); }
  function sigOf(c) { return S.mode === "raw" ? S.pin[c].view() : procOf(c); }
  function chLabel(c) {
    const ch = S.meta.channels[c] || {};
    return `CH${c + 1}` + (ch.header ? ` · ${ch.header}` : ch.gpio ? ` · GPIO${ch.gpio}` : "") + (ch.muscle ? ` · ${ch.muscle}` : "");
  }
  function hexRgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  function mix(a, b, k) { return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`; }
  function ratioColor(c, r) {
    if (!(r === r)) return mix(GREY, GREY, 0);
    const k = clamp(Math.log10(Math.max(r, 1)) / Math.log10(10), 0, 1);
    return mix(GREY, hexRgb(COL[c % COL.length]), k);
  }
  function niceStep(span, target) {
    const raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
  }
  function fmtT(t, step) {
    const d = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
    if (t >= 60 && step >= 1) { const m = Math.floor(t / 60), s = t - m * 60; return `${m}:${s.toFixed(d).padStart(d ? 3 + d : 2, "0")}`; }
    return t.toFixed(d) + " s";
  }

  // ------------------------------------------------------------------ data
  async function listRecordings(keep) {
    const r = await fetch("/api/recordings"); const list = await r.json();
    const sel = $("rec"), cur = keep || sel.value;
    const sig = list.map(x => x.recording_id + x.status).join("|");
    if (sig === listRecordings.sig) return list;   // unchanged: leave the dropdown alone
    listRecordings.sig = sig;
    sel.innerHTML = "";
    for (const x of list) {
      const o = document.createElement("option");
      o.value = x.recording_id;
      o.textContent = (x.status === "recording" ? "● LIVE  " : "") + x.recording_id + (x.samples && x.fs ? `  (${(x.samples / x.fs).toFixed(0)} s)` : "");
      sel.appendChild(o);
    }
    if (cur && list.some(x => x.recording_id === cur)) sel.value = cur;
    return list;
  }

  function resetData() {
    clearInterval(S.pollTimer); clearInterval(S.metaTimer);
    Object.assign(S, { meta: null, n: 0, nextRow: 0, status: "", pin: [], live: [], liveF: [], dc0: [], clean: null, env: [], ratio: [], acts: [], envMax: [], sel: null, lagMs: null, specCache: null });
  }

  async function openRec(rid, keepView) {
    resetData();
    S.rid = rid;
    $("rec").value = rid;
    let meta;
    for (let tries = 0; ; tries++) {
      const r = await fetch(`/api/rec/${encodeURIComponent(rid)}/meta`);
      meta = await r.json();
      if (S.rid !== rid) return;
      if (r.ok) break;
      // no samples yet: keep waiting while the recording is open (a board may take a while to send data)
      if (r.status === 409 && (meta.status === "recording" || tries < 30)) { setBadge("waiting for first samples"); await new Promise(f => setTimeout(f, 500)); continue; }
      setBadge(meta.error || "error"); return;
    }
    if (S.rid !== rid) return;
    S.meta = meta; S.fs = meta.fs; S.nch = meta.nch;
    S.floorDefault = (meta.floor_pin_mv || []).map(v => v || null);
    S.floor = S.floorDefault.slice(); S.floorSrc = meta.floor_source || "no floor known: set one from a rest selection";
    S.gainY = Array(S.nch).fill(1);
    for (let c = 0; c < S.nch; c++) {
      S.pin.push(new EMG.Series()); S.live.push(new EMG.Series());
      S.liveF.push(EMG.liveFilter(S.fs, [75, 450], meta.mains_hz, 30));
    }
    fillSpecCh();
    // catch up; a recording that is still growing always has more, so it is read once and then polled
    let got;
    do { got = await pull(); } while (got && S.status !== "recording" && S.rid === rid);
    if (S.rid !== rid) return;
    if (S.status !== "recording") await loadClean(rid);
    else {
      S.pollTimer = setInterval(() => pull().then(async (got) => {
        if (S.status !== "recording" && !S.clean && S.rid === rid) { clearInterval(S.pollTimer); await loadClean(rid); refreshMeta(); }
      }), 100);
      S.metaTimer = setInterval(refreshMeta, 2000);
    }
    derive(true);
    const h = parseHash();
    if (keepView && h.rid === rid && h.t1 > h.t0 && S.status !== "recording") { S.t0 = h.t0; S.t1 = h.t1; }
    else if (S.status === "recording") setWindow(10);
    else { S.t0 = 0; S.t1 = Math.max(dur(), 1); }
    S.cursor = S.status === "recording" ? dur() : S.t0;
    renderChips(); renderTables(); renderAbout(); scheduleStats(); updateCT(true);
    S.dirty = true;
  }

  async function refreshMeta() {
    if (!S.rid) return;
    const r = await fetch(`/api/rec/${encodeURIComponent(S.rid)}/meta`);
    if (!r.ok) return;
    const m = await r.json();
    if (m.recording_id !== S.rid) return;
    S.meta = Object.assign(S.meta || {}, m);
    renderChips(); renderTables();
  }

  async function pull() {
    if (S.polling || !S.rid) return false;
    S.polling = true;
    const rid = S.rid;
    try {
      const r = await fetch(`/api/rec/${encodeURIComponent(rid)}/data?row=${S.nextRow}&client=${CLIENT}`);
      if (!r.ok || S.rid !== rid) return false;
      const buf = await r.arrayBuffer();
      const dv = new DataView(buf);
      const first = Number(dv.getBigInt64(0, true)), np = dv.getInt32(8, true), nc = dv.getInt32(12, true);
      S.status = r.headers.get("X-Status") || "";
      const nxt = +r.headers.get("X-Next-Row");
      // the server restarted the reader (e.g. the viewer server itself restarted) and sends from the top again
      if (nxt < S.nextRow || first < S.n) { openRec(rid); return false; }
      S.nextRow = nxt;
      const host = r.headers.get("X-Last-Host-Ns");
      if (host) S.lagMs = Date.now() - Number(BigInt(host) / 1000000n);
      if (np === 0) return false;
      const block = new Float32Array(buf, 16, np * nc);
      if (first > S.n) {                                    // samples missing between two blocks
        const gap = new Float32Array((first - S.n) * nc).fill(NaN);
        for (let c = 0; c < nc; c++) { S.pin[c].push(gap, nc, c); S.live[c].push(S.liveF[c].process(gap.subarray(0, first - S.n)), 1, 0); }
      }
      for (let c = 0; c < nc; c++) {
        S.pin[c].push(block, nc, c);
        const col = new Float32Array(np); for (let i = 0; i < np; i++) col[i] = block[i * nc + c];
        // start the causal filter from the first level it sees, not from 0 V (no start-up spike)
        if (S.dc0[c] === undefined) { for (const v of col) if (v === v) { S.dc0[c] = v; break; } }
        const d = S.dc0[c] || 0; for (let i = 0; i < np; i++) col[i] -= d;
        S.live[c].push(S.liveF[c].process(col), 1, 0);
      }
      S.n = S.pin[0].length;
      S.derivedDirty = true; S.dirty = true;
      if (S.status === "recording" && S.follow) { const w = S.t1 - S.t0; S.t1 = Math.max(dur(), w); S.t0 = S.t1 - w; S.cursor = dur(); }
      return np > 0;
    } catch (e) { return false; } finally { S.polling = false; }
  }

  async function loadClean(rid) {
    const r = await fetch(`/api/rec/${encodeURIComponent(rid)}/clean`);
    if (!r.ok || S.rid !== rid) return;
    const buf = await r.arrayBuffer(), dv = new DataView(buf);
    const np = dv.getInt32(8, true), nc = dv.getInt32(12, true), all = new Float32Array(buf, 16, np * nc);
    S.clean = [];
    for (let c = 0; c < nc; c++) { const a = new Float32Array(S.n).fill(NaN); for (let i = 0; i < Math.min(np, S.n); i++) a[i] = all[i * nc + c]; S.clean.push(a); }
    S.derivedDirty = true; S.dirty = true; derive(true); renderChips(); renderAbout();
  }

  function derive(force) {
    if (!S.meta || !S.n) return;
    const now = performance.now();
    if (!force && (!S.derivedDirty || now - S.lastDerive < 250)) return;
    S.lastDerive = now; S.derivedDirty = false;
    const win = Math.max(1, Math.round(S.opts.env_window_s * S.fs));
    for (let c = 0; c < S.nch; c++) {
      const y = procOf(c);
      const env = EMG.envelope(y, win, !!S.clean, S.n);
      const f = S.floor[c];
      const ratio = new Float32Array(S.n);
      let mx = 0;
      for (let i = 0; i < S.n; i++) { ratio[i] = f ? env[i] / f : NaN; if (env[i] > mx) mx = env[i]; }
      S.env[c] = env; S.ratio[c] = ratio; S.envMax[c] = mx;
      S.acts[c] = f ? EMG.activations(ratio, S.fs, S.opts, S.n) : [];
    }
    renderActs(); scheduleStats(); S.specCache = null; S.dirty = true;
  }

  // ------------------------------------------------------------------ view control
  function setWindow(w) {
    if (w === "all") { S.t0 = 0; S.t1 = Math.max(dur(), 0.5); }
    else {
      const c = (S.status === "recording" && S.follow) ? dur() - w / 2 : (S.cursor >= S.t0 && S.cursor <= S.t1 ? S.cursor : (S.t0 + S.t1) / 2);
      S.t0 = c - w / 2; S.t1 = c + w / 2; clampView();
    }
    S.dirty = true; scheduleStats(); writeHash();
  }
  function clampView() {
    // while a recording grows, the window may be wider than what has arrived so far (it fills up)
    const live = S.status === "recording", D = Math.max(dur(), 0.05, live ? S.t1 - S.t0 : 0);
    const w = clamp(S.t1 - S.t0, 0.02, Math.max(D, 0.02));
    S.t0 = clamp(S.t0, 0, Math.max(0, D - w)); S.t1 = S.t0 + w;
  }
  function zoomAt(t, k) { const a = t - (t - S.t0) * k, b = t + (S.t1 - t) * k; S.t0 = a; S.t1 = b; clampView(); S.dirty = true; scheduleStats(); writeHash(); }
  function panBy(dt) { S.t0 += dt; S.t1 += dt; clampView(); if (S.status === "recording" && dt < 0) setFollow(false); S.dirty = true; scheduleStats(); writeHash(); }
  function setFollow(v) { S.follow = v; $("follow").checked = v; store.set("follow", v); }
  function parseHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    return { rid: h.get("rid"), t0: parseFloat(h.get("t0")), t1: parseFloat(h.get("t1")) };
  }
  let hashTimer = null;
  function writeHash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(() => { try { history.replaceState(null, "", `#rid=${S.rid}&t0=${S.t0.toFixed(3)}&t1=${S.t1.toFixed(3)}`); } catch (e) { /* ignore */ } }, 300);
  }

  // ------------------------------------------------------------------ timeline drawing
  const tl = $("timeline"), ov = $("overview");
  const G = { gutter: 92, top: 18, axis: 24 };
  function fitCanvas(cv) {
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); return { ctx, w, h, dpr };
  }
  function lanes(h) {
    const L = [];
    for (let c = 0; c < S.nch; c++) { L.push({ type: "sig", c, wt: 3 }); L.push({ type: "env", c, wt: 1.5 }); }
    if (S.specOn && S.nch) L.push({ type: "spec", c: S.specCh, wt: 2.2 });
    const gap = 8, avail = h - G.top - G.axis - gap * (L.length - 1), tot = L.reduce((a, l) => a + l.wt, 0);
    let y = G.top;
    for (const l of L) { l.y = y; l.h = avail * l.wt / tot; y += l.h + gap; }
    return L;
  }
  const xOf = (t, w) => G.gutter + (t - S.t0) / (S.t1 - S.t0) * (w - G.gutter - 8);
  const tOf = (x, w) => S.t0 + (x - G.gutter) / (w - G.gutter - 8) * (S.t1 - S.t0);

  function drawTimeline() {
    const { ctx, w, h } = fitCanvas(tl);
    ctx.clearRect(0, 0, w, h);
    if (!S.meta || !S.n) { ctx.fillStyle = "#8b97a6"; ctx.font = "14px system-ui"; ctx.fillText(S.rid ? "loading…" : "Pick a recording", 20, 40); return; }
    const L = lanes(h), pw = w - G.gutter - 8, fs = S.fs;
    S._lanes = L;
    const i0 = clamp(Math.floor(S.t0 * fs), 0, S.n), i1 = clamp(Math.ceil(S.t1 * fs) + 1, 0, S.n);
    const bandTop = L[0].y, bandBot = L[L.length - 1].y + L[L.length - 1].h;
    // lane backgrounds
    for (const l of L) { ctx.fillStyle = "#11161d"; ctx.fillRect(G.gutter, l.y, pw, l.h); }
    // cues
    ctx.font = "11px system-ui";
    for (const cu of (S.meta.cues || [])) {
      if (cu.end_s < S.t0 || cu.start_s > S.t1) continue;
      const a = xOf(cu.start_s, w), b = xOf(cu.end_s, w);
      ctx.fillStyle = cu.level > 0 ? "rgba(227,179,65,.10)" : "rgba(139,151,166,.05)";
      ctx.fillRect(Math.max(a, G.gutter), bandTop, Math.min(b, w - 8) - Math.max(a, G.gutter), bandBot - bandTop);
      if (b - a > 30) { ctx.fillStyle = cu.level > 0 ? "#e3b341" : "#8b97a6"; ctx.fillText(cu.task.replace("grip_", ""), Math.max(a, G.gutter) + 3, 12); }
    }
    // detected activity
    for (const l of L) {
      if (l.type === "spec") continue;
      ctx.fillStyle = COL[l.c] + "1f";
      for (const [a, b] of (S.acts[l.c] || [])) {
        const ta = a / fs, tb = b / fs; if (tb < S.t0 || ta > S.t1) continue;
        const xa = Math.max(xOf(ta, w), G.gutter), xb = Math.min(xOf(tb, w), w - 8);
        ctx.fillRect(xa, l.y, xb - xa, l.h);
      }
    }
    for (const l of L) {
      ctx.save(); ctx.beginPath(); ctx.rect(G.gutter, l.y, pw, l.h); ctx.clip();
      if (l.type === "sig") drawSig(ctx, l, i0, i1, w, pw);
      else if (l.type === "env") drawEnv(ctx, l, i0, i1, w, pw);
      else drawSpec(ctx, l, i0, i1, w, pw);
      ctx.restore();
      drawGutter(ctx, l);
    }
    // selection
    if (S.sel) {
      const a = xOf(Math.min(S.sel.a, S.sel.b), w), b = xOf(Math.max(S.sel.a, S.sel.b), w);
      ctx.fillStyle = "rgba(78,161,255,.13)"; ctx.fillRect(Math.max(a, G.gutter), bandTop, Math.min(b, w - 8) - Math.max(a, G.gutter), bandBot - bandTop);
      ctx.strokeStyle = "rgba(78,161,255,.7)"; ctx.lineWidth = 1;
      for (const x of [a, b]) if (x >= G.gutter && x <= w - 8) { ctx.beginPath(); ctx.moveTo(x, bandTop); ctx.lineTo(x, bandBot); ctx.stroke(); }
    }
    // cursor
    if (S.cursor >= S.t0 && S.cursor <= S.t1) {
      const x = xOf(S.cursor, w);
      ctx.strokeStyle = "rgba(255,255,255,.75)"; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(x, bandTop - 4); ctx.lineTo(x, bandBot); ctx.stroke();
    }
    // time axis
    const span = S.t1 - S.t0, step = niceStep(span, Math.max(3, pw / 110));
    ctx.fillStyle = "#8b97a6"; ctx.strokeStyle = "#2a3340"; ctx.font = "11px system-ui"; ctx.textAlign = "center";
    for (let t = Math.ceil(S.t0 / step) * step; t <= S.t1 + 1e-9; t += step) {
      const x = xOf(t, w);
      ctx.beginPath(); ctx.moveTo(x, bandTop); ctx.lineTo(x, bandBot); ctx.globalAlpha = .35; ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillText(fmtT(t, step), x, h - 8);
    }
    ctx.textAlign = "left";
  }

  function drawGutter(ctx, l) {
    ctx.fillStyle = l.type === "spec" ? "#8b97a6" : COL[l.c];
    ctx.font = "600 11px system-ui";
    const name = l.type === "sig" ? chLabel(l.c).split(" · ").slice(0, 2).join(" · ") : l.type === "env" ? `CH${l.c + 1} envelope` : `CH${l.c + 1} spectrum`;
    ctx.fillText(name, 6, l.y + 13);
    ctx.font = "10px system-ui"; ctx.fillStyle = "#8b97a6";
    const sub = l.type === "sig" ? (S.mode === "raw" ? "raw, " : "filtered, ") + (S.units === "skin" ? "µV est." : "mV pin") : l.type === "env" ? "× floor" : "0–500 Hz";
    ctx.fillText(sub, 6, l.y + 26);
    if (l.ticks) for (const [y, txt] of l.ticks) { ctx.fillText(txt, G.gutter - 4 - ctx.measureText(txt).width, y + 3); }
  }

  function drawTrace(ctx, y, i0, i1, w, pw, yOf, color, fill0) {
    const n = i1 - i0; if (n < 2) return;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
    const x0 = xOf(i0 / S.fs, w), x1 = xOf((i1 - 1) / S.fs, w), px = Math.max(1, Math.round(x1 - x0));
    if (n > px * 2) {
      const { mn, mx } = EMG.minmax(y, i0, i1, px);
      ctx.beginPath();
      for (let b = 0; b < px; b++) {
        if (!(mn[b] === mn[b])) { ctx.save(); ctx.fillStyle = "rgba(248,81,73,.6)"; ctx.fillRect(x0 + b, yOf(0) - 1, 1, 3); ctx.restore(); continue; }
        const lo = yOf(fill0 !== undefined ? fill0 : mn[b]), hi = yOf(mx[b]);
        ctx.rect(x0 + b, Math.min(lo, hi), 1, Math.max(1, Math.abs(lo - hi)));
      }
      ctx.fill();
    } else {
      ctx.beginPath(); let pen = false;
      for (let i = i0; i < i1; i++) {
        const v = y[i];
        if (!(v === v)) { pen = false; continue; }
        const x = xOf(i / S.fs, w), yy = yOf(v);
        if (pen) ctx.lineTo(x, yy); else { ctx.moveTo(x, yy); pen = true; }
      }
      ctx.stroke();
      if (n < pw / 4) { for (let i = i0; i < i1; i++) { const v = y[i]; if (v === v) { ctx.fillRect(xOf(i / S.fs, w) - 1.5, yOf(v) - 1.5, 3, 3); } } }
    }
  }

  function drawSig(ctx, l, i0, i1, w, pw) {
    const y = sigOf(l.c), k = unitK();
    if (i1 - i0 < 2) return;
    const { mn, mx } = EMG.minmax(y, i0, i1, Math.min(400, i1 - i0));
    let lo = Infinity, hi = -Infinity;
    for (let b = 0; b < mn.length; b++) { if (mn[b] < lo) lo = mn[b]; if (mx[b] > hi) hi = mx[b]; }
    if (!isFinite(lo)) return;
    let c0, half;
    if (S.mode === "raw") { c0 = (lo + hi) / 2; half = Math.max((hi - lo) / 2 * 1.08, 1); }
    else { c0 = 0; half = Math.max(Math.abs(lo), Math.abs(hi), 1e-3) * 1.08; }
    half /= S.gainY[l.c];
    const yOf = (v) => l.y + l.h / 2 - (v - c0) / half * (l.h / 2);
    ctx.strokeStyle = "#2a3340"; ctx.beginPath(); ctx.moveTo(G.gutter, yOf(c0)); ctx.lineTo(G.gutter + pw, yOf(c0)); ctx.stroke();
    if (S.mode !== "raw") {
      const f = S.floor[l.c];
      if (f && f < half) { ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(139,151,166,.45)"; for (const s of [1, -1]) { ctx.beginPath(); ctx.moveTo(G.gutter, yOf(s * f)); ctx.lineTo(G.gutter + pw, yOf(s * f)); ctx.stroke(); } ctx.setLineDash([]); }
    }
    drawTrace(ctx, y, i0, i1, w, pw, yOf, COL[l.c]);
    l.ticks = [[yOf(c0 + half * 0.8), fmt((c0 + half * 0.8) * k, Math.abs(half * k) < 10 ? 1 : 0)], [yOf(c0), fmt(c0 * k, 0)], [yOf(c0 - half * 0.8), fmt((c0 - half * 0.8) * k, Math.abs(half * k) < 10 ? 1 : 0)]];
  }

  function drawEnv(ctx, l, i0, i1, w, pw) {
    const r = S.ratio[l.c]; if (!r) return;
    if (!S.floor[l.c]) { ctx.fillStyle = "#8b97a6"; ctx.font = "11px system-ui"; ctx.fillText("no floor known: select a rest stretch and press 'Floor from selection'", G.gutter + 8, l.y + l.h / 2); l.ticks = []; return; }
    const { mx } = EMG.minmax(r, i0, i1, Math.min(400, Math.max(1, i1 - i0)));
    let top = S.opts.on_x * 1.4; for (const v of mx) if (v > top) top = v;
    top *= 1.08;
    const yOf = (v) => l.y + l.h - 2 - v / top * (l.h - 4);
    ctx.setLineDash([3, 4]);
    for (const [v, c] of [[1, "rgba(139,151,166,.6)"], [S.opts.off_x, COL[l.c] + "66"], [S.opts.on_x, COL[l.c] + "bb"]]) { ctx.strokeStyle = c; ctx.beginPath(); ctx.moveTo(G.gutter, yOf(v)); ctx.lineTo(G.gutter + pw, yOf(v)); ctx.stroke(); }
    ctx.setLineDash([]);
    drawTrace(ctx, r, i0, i1, w, pw, yOf, COL[l.c] + "cc", 0);
    l.ticks = [[yOf(top * 0.85), fmt(top * 0.85, 0) + "×"], [yOf(S.opts.on_x), S.opts.on_x + "×"], [yOf(1), "1×"]];
  }

  const VIR = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  function vir(k) { k = (k === k ? clamp(k, 0, 1) : 0) * 4; const i = Math.min(3, Math.floor(k)), f = k - i; return VIR[i].map((v, j) => v + (VIR[i + 1][j] - v) * f); }
  function drawSpec(ctx, l, i0, i1, w, pw) {
    l.ticks = [[l.y + 8, "500"], [l.y + l.h / 2, "250"], [l.y + l.h - 4, "0 Hz"]];
    const span = (i1 - i0) / S.fs;
    if (span > 120) { ctx.fillStyle = "#8b97a6"; ctx.font = "11px system-ui"; ctx.fillText("zoom in to 2 minutes or less to see the spectrogram", G.gutter + 8, l.y + l.h / 2); return; }
    const key = [S.specCh, i0, i1, Math.round(pw), S.mode].join(",");
    if (!S.specCache || S.specCache.key !== key) {
      const nfft = 256, hop = Math.max(16, Math.floor((i1 - i0) / Math.max(pw, 1)));
      const sp = EMG.spectrogram(sigOf(l.c), Math.max(0, i0 - nfft / 2), Math.min(S.n, i1 + nfft / 2), S.fs, nfft, hop, 500);
      const vals = Array.from(sp.db).filter(v => v === v).sort((a, b) => a - b);
      const lo = vals[Math.floor(vals.length * 0.05)] || 0; let hi = vals[Math.floor(vals.length * 0.995)] || 1;
      if (!(hi > lo)) hi = lo + 1;   // a flat channel (exactly 0 after filtering) has one dB value everywhere
      const img = new ImageData(Math.max(1, sp.cols), sp.bins);
      for (let c = 0; c < sp.cols; c++) for (let b = 0; b < sp.bins; b++) {
        const v = sp.db[c * sp.bins + b], p = ((sp.bins - 1 - b) * sp.cols + c) * 4;
        const rgb = v === v ? vir((v - lo) / (hi - lo)) : [40, 0, 0];
        img.data[p] = rgb[0]; img.data[p + 1] = rgb[1]; img.data[p + 2] = rgb[2]; img.data[p + 3] = 255;
      }
      const off = document.createElement("canvas"); off.width = img.width; off.height = img.height; off.getContext("2d").putImageData(img, 0, 0);
      S.specCache = { key, off, t0: (sp.t0) / S.fs, t1: (sp.t0 + sp.cols * sp.hop) / S.fs };
    }
    const sc = S.specCache;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(sc.off, xOf(sc.t0, w), l.y, xOf(sc.t1, w) - xOf(sc.t0, w), l.h);
  }

  function drawOverview() {
    const { ctx, w, h } = fitCanvas(ov);
    ctx.clearRect(0, 0, w, h);
    if (!S.meta || !S.n) return;
    const D = dur(), xo = (t) => 6 + t / D * (w - 12), half = (h - 8) / Math.max(S.nch, 1);
    for (const cu of (S.meta.cues || [])) if (cu.level > 0) { ctx.fillStyle = "rgba(227,179,65,.18)"; ctx.fillRect(xo(cu.start_s), 2, xo(cu.end_s) - xo(cu.start_s), h - 4); }
    for (let c = 0; c < S.nch; c++) {
      const r = S.ratio[c] && S.floor[c] ? S.ratio[c] : S.env[c]; if (!r) continue;
      const px = Math.max(1, Math.round(w - 12)), { mx } = EMG.minmax(r, 0, S.n, px);
      let top = 1e-9; for (const v of mx) if (v > top) top = v;
      ctx.fillStyle = COL[c] + "cc";
      const base = 4 + half * (c + 1);
      for (let b = 0; b < px; b++) { const v = mx[b]; if (v === v) { const hh = Math.max(1, v / top * (half - 2)); ctx.fillRect(6 + b, base - hh, 1, hh); } }
    }
    const a = xo(S.t0), b = xo(S.t1);
    ctx.fillStyle = "rgba(78,161,255,.14)"; ctx.fillRect(a, 1, Math.max(2, b - a), h - 2);
    ctx.strokeStyle = "#4ea1ff"; ctx.lineWidth = 1.5; ctx.strokeRect(a, 1, Math.max(2, b - a), h - 2);
    ctx.strokeStyle = "rgba(255,255,255,.8)"; ctx.lineWidth = 1; const xc = xo(S.cursor); ctx.beginPath(); ctx.moveTo(xc, 0); ctx.lineTo(xc, h); ctx.stroke();
    S._ov = { xo, D, w };
  }

  // ------------------------------------------------------------------ interaction
  const ptrs = new Map(); let drag = null, pinch = null;
  tl.addEventListener("wheel", (e) => {
    if (!S.meta) return;
    e.preventDefault();
    const rect = tl.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top, w = rect.width;
    if (e.ctrlKey || e.metaKey) { zoomAt(tOf(Math.max(x, G.gutter), w), Math.exp(e.deltaY * 0.01)); return; }
    if (x < G.gutter && S._lanes) {
      const l = S._lanes.find(l => y >= l.y && y <= l.y + l.h);
      if (l && l.type === "sig") { S.gainY[l.c] = clamp(S.gainY[l.c] * Math.exp(-e.deltaY * 0.002), 0.05, 200); S.dirty = true; }
      return;
    }
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    panBy(d / (w - G.gutter) * (S.t1 - S.t0) * (e.deltaMode === 1 ? 16 : 1));
  }, { passive: false });

  tl.addEventListener("pointerdown", (e) => {
    if (!S.meta) return;
    tl.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const rect = tl.getBoundingClientRect(), x = e.clientX - rect.left;
    if (ptrs.size === 2) {
      const [p, q] = [...ptrs.values()];
      pinch = { d: Math.abs(p.x - q.x) || 1, t0: S.t0, t1: S.t1, mid: tOf((p.x + q.x) / 2 - rect.left, rect.width) };
      drag = null; return;
    }
    const sel = e.shiftKey || S.selMode;
    drag = { x0: x, t0: S.t0, t1: S.t1, tStart: tOf(x, rect.width), sel, moved: false };
    if (sel) S.sel = { a: drag.tStart, b: drag.tStart };
    tl.classList.add("dragging");
  });
  tl.addEventListener("pointermove", (e) => {
    const rect = tl.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && ptrs.size === 2) {
      const [p, q] = [...ptrs.values()], d = Math.abs(p.x - q.x) || 1, k = pinch.d / d;
      S.t0 = pinch.mid - (pinch.mid - pinch.t0) * k; S.t1 = pinch.mid + (pinch.t1 - pinch.mid) * k; clampView(); S.dirty = true; scheduleStats();
      return;
    }
    if (drag) {
      if (Math.abs(x - drag.x0) > 3) drag.moved = true;
      if (drag.sel) { S.sel.b = clamp(tOf(x, rect.width), 0, dur()); scheduleStats(); }
      else { const dt = (x - drag.x0) / (rect.width - G.gutter - 8) * (drag.t1 - drag.t0); S.t0 = drag.t0 - dt; S.t1 = drag.t1 - dt; clampView(); if (S.status === "recording" && drag.moved) setFollow(false); scheduleStats(); }
      S.dirty = true;
    }
    showTip(x, y, rect.width);
  });
  function endPtr(e) {
    ptrs.delete(e.pointerId);
    if (pinch && ptrs.size < 2) { pinch = null; writeHash(); return; }
    if (!drag) return;
    const rect = tl.getBoundingClientRect();
    if (!drag.moved && !drag.sel) { S.cursor = clamp(tOf(drag.x0, rect.width), 0, dur()); S.sel = null; updateCT(true); scheduleStats(); }
    if (drag.sel && Math.abs(S.sel.b - S.sel.a) < 0.01) S.sel = null;
    drag = null; tl.classList.remove("dragging"); S.dirty = true; writeHash(); scheduleStats();
  }
  tl.addEventListener("pointerup", endPtr); tl.addEventListener("pointercancel", endPtr);
  tl.addEventListener("pointerleave", () => { $("tip").hidden = true; });
  tl.addEventListener("dblclick", () => setWindow("all"));

  function showTip(x, y, w) {
    const tip = $("tip");
    if (!S.meta || x < G.gutter || !S._lanes) { tip.hidden = true; return; }
    const t = tOf(x, w), i = Math.round(t * S.fs);
    if (i < 0 || i >= S.n) { tip.hidden = true; return; }
    const k = unitK(), parts = [`<b>${t.toFixed(3)} s</b>`];
    for (let c = 0; c < S.nch; c++) {
      const v = sigOf(c)[i], r = S.ratio[c] ? S.ratio[c][i] : NaN;
      parts.push(`<span class="c${c + 1}">CH${c + 1}</span> ${fmt(v * k, 2)} ${S.units === "skin" ? "µV" : "mV"} · env ${fmt(r, 1)}×`);
    }
    const cue = (S.meta.cues || []).find(cu => t >= cu.start_s && t < cu.end_s);
    if (cue) parts.push(`cue: ${cue.task}`);
    tip.innerHTML = parts.join("<br>"); tip.hidden = false;
    tip.style.left = Math.min(x + 14, w - 230) + "px"; tip.style.top = (y + 14) + "px";
  }

  let ovDrag = null;
  ov.addEventListener("pointerdown", (e) => {
    if (!S._ov) return;
    ov.setPointerCapture(e.pointerId);
    const x = e.clientX - ov.getBoundingClientRect().left, t = (x - 6) / (S._ov.w - 12) * S._ov.D, wdt = S.t1 - S.t0;
    if (t < S.t0 || t > S.t1) { S.t0 = t - wdt / 2; S.t1 = t + wdt / 2; clampView(); }
    ovDrag = { x, t0: S.t0, t1: S.t1 }; if (S.status === "recording") setFollow(false); S.dirty = true; scheduleStats();
  });
  ov.addEventListener("pointermove", (e) => {
    if (!ovDrag) return;
    const x = e.clientX - ov.getBoundingClientRect().left, dt = (x - ovDrag.x) / (S._ov.w - 12) * S._ov.D;
    S.t0 = ovDrag.t0 + dt; S.t1 = ovDrag.t1 + dt; clampView(); S.dirty = true; scheduleStats();
  });
  ov.addEventListener("pointerup", () => { ovDrag = null; writeHash(); });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    const w = S.t1 - S.t0;
    if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowRight") panBy(w * (e.shiftKey ? 0.5 : 0.1));
    else if (e.key === "ArrowLeft") panBy(-w * (e.shiftKey ? 0.5 : 0.1));
    else if (e.key === "+" || e.key === "=") zoomAt(S.cursor >= S.t0 && S.cursor <= S.t1 ? S.cursor : (S.t0 + S.t1) / 2, 0.7);
    else if (e.key === "-" || e.key === "_") zoomAt((S.t0 + S.t1) / 2, 1 / 0.7);
    else if (e.key === "Home") { S.t1 -= S.t0; S.t0 = 0; S.dirty = true; scheduleStats(); }
    else if (e.key === "End") { S.t0 = dur() - w; S.t1 = dur(); clampView(); S.dirty = true; scheduleStats(); }
    else if (e.key === "Escape") { S.sel = null; S.dirty = true; scheduleStats(); }
  });

  // ------------------------------------------------------------------ playback + loop
  function togglePlay() {
    S.playing = !S.playing;
    if (S.playing && (S.cursor >= dur() - 0.01)) S.cursor = S.sel ? Math.min(S.sel.a, S.sel.b) : 0;
    $("btnPlay").textContent = S.playing ? "❚❚ Pause" : "▶ Play";
  }
  let last = performance.now();
  function frame(now) {
    // whatever a draw throws, the next frame is still scheduled: the viewer must not freeze until a reload
    try { frameBody(now); } finally { requestAnimationFrame(frame); }
  }
  function frameBody(now) {
    const dt = (now - last) / 1000; last = now;
    derive(false);
    if (S.playing && S.meta) {
      S.cursor += dt * S.speed;
      const a = S.sel ? Math.min(S.sel.a, S.sel.b) : 0, b = S.sel ? Math.max(S.sel.a, S.sel.b) : dur();
      if (S.cursor >= b) { if (S.loop && S.sel) S.cursor = a; else { S.cursor = b; S.playing = false; $("btnPlay").textContent = "▶ Play"; } }
      if (S.cursor > S.t1 || S.cursor < S.t0) { const w = S.t1 - S.t0; S.t0 = S.cursor - w * 0.1; S.t1 = S.t0 + w; clampView(); scheduleStats(); }
      S.dirty = true; updateCT(false);
    } else if (S.status === "recording" && S.follow) { updateCT(false); }
    if (S.dirty) { S.dirty = false; drawTimeline(); drawOverview(); renderLiveChip(); }
  }

  // ------------------------------------------------------------------ CT
  const CT = { ready: false, regionIdx: {}, lastColor: {}, lastT: 0 };
  async function initCT() {
    const el = $("ct");
    let sc;
    try { const r = await fetch("/api/scene"); sc = await r.json(); if (!r.ok) throw new Error(sc.error); }
    catch (e) { el.innerHTML = `<p class="muted" style="padding:12px">${e.message || "No CT scene"}</p>`; return; }
    const traces = [];
    for (const m of sc.meshes) traces.push({ type: "mesh3d", x: m.x, y: m.y, z: m.z, i: m.i, j: m.j, k: m.k, color: m.color, opacity: m.opacity, name: m.name, hoverinfo: "name", flatshading: false, lighting: { ambient: 0.55, diffuse: 0.7, specular: 0.15 }, showlegend: false });
    for (const r of sc.regions) {
      CT.regionIdx[r.ch] = traces.length;
      traces.push({ type: "mesh3d", x: r.x, y: r.y, z: r.z, i: r.i, j: r.j, k: r.k, color: mix(GREY, GREY, 0), opacity: 0.95, name: `${r.muscle_group} region (CH${r.ch})`, hoverinfo: "name", lighting: { ambient: 0.6, diffuse: 0.7 }, showlegend: false });
    }
    for (const s of sc.sites) traces.push({ type: "scatter3d", mode: "markers+text", x: [s.point[0]], y: [s.point[1]], z: [s.point[2]], text: [`CH${s.ch}`], textfont: { color: COL[s.ch - 1], size: 12 }, marker: { size: 6, color: COL[s.ch - 1], line: { color: "#fff", width: 1 } }, hovertext: `CH${s.ch} ${s.muscle_group}: fat ${s.fat_mm} mm, muscle ${s.muscle_mm} mm, skin to bone ${s.skin_to_bone_mm} mm`, hoverinfo: "text", showlegend: false });
    const ax = { visible: false };
    Plotly.newPlot(el, traces, { paper_bgcolor: "rgba(0,0,0,0)", margin: { l: 0, r: 0, t: 0, b: 0 }, scene: { aspectmode: "data", xaxis: ax, yaxis: ax, zaxis: ax, bgcolor: "rgba(0,0,0,0)" }, font: { color: "#d6dde6" } }, { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["toImage"] });
    const vc = sc.summary && sc.summary.frame && sc.summary.frame.volar_confirmed;
    $("ctnote").textContent = (sc.mirrored ? "mirrored left-arm CT · " : "") + (vc ? "palm side confirmed" : "palm side not yet confirmed");
    CT.ready = true; updateCT(true);
  }
  function updateCT(force) {
    if (!CT.ready || !S.meta) return;
    const now = performance.now(); if (!force && now - CT.lastT < 120) return; CT.lastT = now;
    const i = clamp(Math.round(S.cursor * S.fs), 0, Math.max(0, S.n - 1));
    for (const ch in CT.regionIdx) {
      const c = ch - 1, r = S.ratio[c] ? S.ratio[c][i] : NaN, col = ratioColor(c, r);
      if (CT.lastColor[ch] === col) continue;
      CT.lastColor[ch] = col;
      Plotly.restyle($("ct"), { color: [col] }, [CT.regionIdx[ch]]);
    }
  }

  // ------------------------------------------------------------------ stats + spectrum
  function range() {
    if (S.sel) { const a = Math.min(S.sel.a, S.sel.b), b = Math.max(S.sel.a, S.sel.b); return { a, b, what: "Selection" }; }
    return { a: Math.max(0, S.t0), b: Math.min(dur(), S.t1), what: "Visible window" };
  }
  function scheduleStats() { clearTimeout(S.statsTimer); S.statsTimer = setTimeout(renderStats, 120); }
  function renderStats() {
    if (!S.meta || !S.n) return;
    const R = range(), i0 = clamp(Math.floor(R.a * S.fs), 0, S.n), i1 = clamp(Math.ceil(R.b * S.fs), 0, S.n);
    $("statsTitle").textContent = R.what; $("statsRange").textContent = `${R.a.toFixed(2)}–${R.b.toFixed(2)} s (${(R.b - R.a).toFixed(2)} s)`;
    const k = unitK(), u = S.units === "skin" ? "µV" : "mV", rows = [];
    const st = [];
    for (let c = 0; c < S.nch; c++) {
      const s = EMG.stats(procOf(c), i0, i1, S.fs, S.floor[c]);
      let act = 0; for (const [a, b] of (S.acts[c] || [])) act += Math.max(0, Math.min(b, i1) - Math.max(a, i0));
      let emax = 0; const env = S.env[c]; if (env) for (let i = i0; i < i1; i++) if (env[i] > emax) emax = env[i];
      s.active = i1 > i0 ? act / (i1 - i0) : null; s.pmax = S.envMax[c] ? emax / S.envMax[c] : null; st.push(s);
    }
    const head = `<tr><th></th>${st.map((_, c) => `<th class="c${c + 1}">CH${c + 1}</th>`).join("")}</tr>`;
    const line = (name, f) => `<tr><td>${name}</td>${st.map(s => `<td>${f(s)}</td>`).join("")}</tr>`;
    rows.push(line(`RMS (${u})`, s => fmt(s.rms * k, 2)), line(`Net RMS above floor`, s => fmt(s.net_rms * k, 2)), line("SNR vs floor (dB)", s => fmt(s.snr_db, 1)),
      line(`Mean absolute (${u})`, s => fmt(s.mav * k, 2)), line(`Peak (${u})`, s => fmt(s.peak * k, 1)), line(`Peak-to-peak (${u})`, s => fmt(s.p2p * k, 1)),
      line("Median frequency (Hz)", s => fmt(s.median_hz, 0)), line("Mean frequency (Hz)", s => fmt(s.mean_hz, 0)),
      line("Detected active (%)", s => fmt(s.active === null ? null : s.active * 100, 0)), line("Peak envelope, % of recording max", s => fmt(s.pmax === null ? null : s.pmax * 100, 0)),
      line(`Floor used (${u})`, (s, i) => ""));
    $("stats").innerHTML = head + rows.join("");
    const fl = $("stats").rows[$("stats").rows.length - 1];
    for (let c = 0; c < S.nch; c++) fl.cells[c + 1].textContent = fmt(S.floor[c] ? S.floor[c] * k : null, 2);
    drawPSD(i0, i1);
  }

  function drawPSD(i0, i1) {
    const cv = $("psd"), { ctx, w, h } = fitCanvas(cv);
    ctx.clearRect(0, 0, w, h);
    const maxN = 120 * S.fs; if (i1 - i0 > maxN) { const m = (i0 + i1) / 2; i0 = Math.floor(m - maxN / 2); i1 = i0 + maxN; }
    const L = 34, B = 18, pw = w - L - 6, ph = h - B - 6, fmax = 500;
    const curves = [];
    let lo = Infinity, hi = -Infinity;
    for (let c = 0; c < S.nch; c++) {
      const wl = EMG.welch(S.mode === "raw" ? sigOf(c) : procOf(c), i0, i1, S.fs, 256);
      if (!wl.segments) continue;
      const db = Array.from(wl.p, v => 10 * Math.log10(v + 1e-12));
      wl.f.forEach((f, j) => { if (f > 5 && f <= fmax) { lo = Math.min(lo, db[j]); hi = Math.max(hi, db[j]); } });
      curves.push({ c, f: wl.f, db, st: EMG.spectralStats(wl.f, wl.p, 20, 450) });
    }
    $("specNote").textContent = (S.mode === "raw" ? "raw pin, " : "filtered, ") + "dB (mV²/Hz)";
    if (!curves.length) { ctx.fillStyle = "#8b97a6"; ctx.fillText("range too short", L + 8, 20); return; }
    lo = Math.floor(lo / 10) * 10; hi = Math.ceil(hi / 10) * 10;
    const X = (f) => L + f / fmax * pw, Y = (d) => 4 + (hi - d) / (hi - lo || 1) * ph;
    ctx.strokeStyle = "#2a3340"; ctx.fillStyle = "#8b97a6"; ctx.font = "10px system-ui";
    for (let f = 0; f <= fmax; f += 100) { ctx.beginPath(); ctx.moveTo(X(f), 4); ctx.lineTo(X(f), 4 + ph); ctx.stroke(); ctx.fillText(f + (f === fmax ? " Hz" : ""), X(f) - 8, h - 4); }
    for (let d = lo; d <= hi; d += 10) { ctx.beginPath(); ctx.moveTo(L, Y(d)); ctx.lineTo(L + pw, Y(d)); ctx.stroke(); ctx.fillText(d, 2, Y(d) + 3); }
    const mains = S.meta.mains_hz; ctx.setLineDash([2, 3]); ctx.strokeStyle = "rgba(227,179,65,.45)";
    for (let f = mains; f <= fmax; f += mains) { ctx.beginPath(); ctx.moveTo(X(f), 4); ctx.lineTo(X(f), 4 + ph); ctx.stroke(); }
    ctx.setLineDash([]);
    for (const cv2 of curves) {
      ctx.strokeStyle = COL[cv2.c]; ctx.lineWidth = 1.3; ctx.beginPath(); let pen = false;
      cv2.f.forEach((f, j) => { if (f > fmax || !(cv2.db[j] === cv2.db[j])) return; const x = X(f), y = Y(cv2.db[j]); if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; } });
      ctx.stroke();
      if (cv2.st.median_hz) { ctx.fillStyle = COL[cv2.c]; const x = X(cv2.st.median_hz); ctx.beginPath(); ctx.moveTo(x, 4 + ph); ctx.lineTo(x - 4, ph - 3); ctx.lineTo(x + 4, ph - 3); ctx.fill(); }
    }
    ctx.lineWidth = 1;
  }

  // ------------------------------------------------------------------ header, tables
  function setBadge(txt, cls) { const b = $("mode"); b.textContent = txt; b.className = "badge" + (cls ? " " + cls : ""); }
  function renderChips() {
    const m = S.meta; if (!m) return;
    const live = S.status === "recording";
    setBadge(live ? "LIVE" : (S.status === "complete" ? "Recorded" : S.status || "?"), live ? "live" : "");
    const miss = m.missing_samples || 0, chips = [
      [`${m.source.label}`, ""], [`<b>${m.fs}</b> Hz`, ""], [`<b>${(S.n / m.fs).toFixed(1)}</b> s`, ""],
      [`missing <b>${miss}</b>`, miss ? "warn" : "good"], [`invalid <b>${m.invalid_samples || 0}</b>`, m.invalid_samples ? "warn" : "good"],
      [`floor <b>${S.floor.map(f => fmt(f, 1)).join(" / ")}</b> mV`, S.floor.every(f => f) ? "" : "warn"],
    ];
    if (m.device && m.device.pins) chips.push([`pins <b>${m.device.pins}</b>`, ""]);
    $("chips").innerHTML = chips.map(([t, c]) => `<span class="chip ${c}">${t}</span>`).join("") + `<span class="chip" id="lagChip" hidden></span>`;
    renderLiveChip();
  }
  function renderLiveChip() {
    const el = $("lagChip"); if (!el) return;
    if (S.status === "recording" && S.lagMs !== null) { el.hidden = false; el.className = "chip " + (S.lagMs > 1000 ? "bad" : S.lagMs > 300 ? "warn" : "good"); el.innerHTML = `data age <b>${Math.max(0, S.lagMs)}</b> ms`; }
    else el.hidden = true;
  }

  function zoomTo(a, b) { S.t0 = a; S.t1 = b; clampView(); S.cursor = a; S.sel = null; setFollow(false); S.dirty = true; scheduleStats(); updateCT(true); writeHash(); }

  function renderTables() {
    const m = S.meta, el = $("tab-trials");
    if (!m) return;
    if (m.trials && m.trials.length) {
      const by = new Map();
      for (const t of m.trials) { const key = t.task + "|" + t.rep + "|" + t.start_s.toFixed(2); if (!by.has(key)) by.set(key, { ...t, ch: {} }); by.get(key).ch[t.ch] = t; }
      const rows = [...by.values()].sort((a, b) => a.start_s - b.start_s);
      el.innerHTML = `<table><tr><th>#</th><th>Task</th><th>Start</th><th>Length</th>${Array.from({ length: m.nch }, (_, c) => `<th class="c${c + 1}">CH${c + 1} SNR dB</th><th class="c${c + 1}">active %</th>`).join("")}<th>Quality</th></tr>` +
        rows.map((r, n) => `<tr class="click" data-a="${r.start_s}" data-b="${r.end_s}"><td>${n + 1}</td><td>${r.task}${r.rep ? " " + r.rep : ""}</td><td>${r.start_s.toFixed(1)} s</td><td>${(r.end_s - r.start_s).toFixed(1)} s</td>` +
          Array.from({ length: m.nch }, (_, c) => { const t = r.ch[c + 1] || {}; return `<td>${fmt(t.snr_db, 1)}</td><td>${fmt(t.active_fraction === undefined ? null : t.active_fraction * 100, 0)}</td>`; }).join("") +
          `<td>${Object.values(r.ch).map(t => t.quality).filter((v, i, a) => a.indexOf(v) === i).join(", ")}</td></tr>`).join("") + `</table><p class="muted">From the dataset session ${m.dataset_session} (same numbers as trials.csv). Click a row to zoom to it.</p>`;
    } else if (m.cues && m.cues.length) {
      el.innerHTML = `<table><tr><th>#</th><th>Cue</th><th>Start</th><th>Length</th></tr>` + m.cues.map((c, n) => `<tr class="click" data-a="${c.start_s}" data-b="${c.end_s}"><td>${n + 1}</td><td>${c.task}</td><td>${c.start_s.toFixed(1)} s</td><td>${(c.end_s - c.start_s).toFixed(1)} s</td></tr>`).join("") + `</table>`;
    } else el.innerHTML = `<p class="muted">No trials or cues in this recording (free-running recording).</p>`;
    const ev = $("tab-events");
    const evs = (m.events || []);
    ev.innerHTML = evs.length ? `<table><tr><th>Time</th><th>Event</th><th>Detail</th></tr>` + evs.map(e => `<tr class="${e.t_s !== null ? "click" : ""}" data-a="${e.t_s !== null ? e.t_s - 1 : ""}" data-b="${e.t_s !== null ? e.t_s + 1 : ""}"><td>${e.t_s === null ? "–" : e.t_s.toFixed(2) + " s"}</td><td>${e.event}</td><td>${e.label || ""}${e.missing ? ` (${e.missing} samples)` : ""}</td></tr>`).join("") + `</table>` : `<p class="muted">No markers, gaps or errors logged.</p>`;
    bindRows();
  }

  function renderActs() {
    const el = $("tab-acts"); if (!S.meta) return;
    const cues = (S.meta.cues || []).filter(c => c.level > 0), rows = [];
    for (let c = 0; c < S.nch; c++) for (const [a, b] of (S.acts[c] || [])) {
      const ta = a / S.fs, tb = b / S.fs;
      let peak = 0; for (let i = a; i < b; i++) if (S.ratio[c][i] > peak) peak = S.ratio[c][i];
      const cue = cues.filter(cu => cu.end_s > ta - 3 && cu.start_s < tb + 3).sort((p, q) => Math.abs(p.start_s - ta) - Math.abs(q.start_s - ta))[0];
      rows.push({ c, ta, tb, peak, on: cue ? ta - cue.start_s : null, off: cue ? tb - cue.end_s : null, cue: cue ? cue.task : "" });
    }
    rows.sort((p, q) => p.ta - q.ta);
    const leads = rows.filter(r => r.on !== null).map(r => r.on).sort((a, b) => a - b);
    const med = leads.length ? leads[Math.floor(leads.length / 2)] : null;
    el.innerHTML = rows.length ? `<p class="muted">Active = envelope above ${S.opts.on_x}× floor, ending below ${S.opts.off_x}×, at least ${S.opts.min_s} s. Onset vs cue: negative = you started before the cue.${med !== null ? ` Median onset vs cue: <b>${med.toFixed(2)} s</b>.` : ""}</p>` +
      `<table><tr><th>Ch</th><th>Start</th><th>End</th><th>Length</th><th>Peak × floor</th><th>Nearest cue</th><th>Onset vs cue</th><th>End vs cue end</th></tr>` +
      rows.map(r => `<tr class="click" data-a="${r.ta - 1}" data-b="${r.tb + 1}"><td class="c${r.c + 1}">CH${r.c + 1}</td><td>${r.ta.toFixed(2)} s</td><td>${r.tb.toFixed(2)} s</td><td>${(r.tb - r.ta).toFixed(2)} s</td><td>${fmt(r.peak, 1)}</td><td>${r.cue}</td><td>${r.on === null ? "–" : (r.on >= 0 ? "+" : "") + r.on.toFixed(2) + " s"}</td><td>${r.off === null ? "–" : (r.off >= 0 ? "+" : "") + r.off.toFixed(2) + " s"}</td></tr>`).join("") + `</table>`
      : `<p class="muted">${S.floor.some(f => f) ? "No activity above the threshold." : "No floor known yet: select a rest stretch, then 'Floor from selection'."}</p>`;
    bindRows();
  }

  function bindRows() {
    document.querySelectorAll(".tab tr.click").forEach(tr => {
      tr.onclick = () => { const a = parseFloat(tr.dataset.a), b = parseFloat(tr.dataset.b); if (isFinite(a) && isFinite(b)) zoomTo(Math.max(0, a - 1), b + 1); };
    });
  }

  function renderAbout() {
    const m = S.meta; if (!m) return;
    $("tab-about").innerHTML = `
      <p><b>Measured where:</b> every amplitude is the voltage at the ESP32 ADC pin (mV). "µV at skin" divides by the Candy's <b>nominal</b> gain (${m.gain_nominal}×), which has not been measured on these boards: treat it as an estimate.</p>
      <p><b>Filtered signal:</b> ${S.clean ? "offline, zero-phase band-pass 75–450 Hz plus mains removed by regression (50 Hz and harmonics fitted per 0.5 s block): the same cleaning as the dataset metrics in trials.csv." : "while a recording is still being written, the browser filters it causally (4th-order band-pass 75–450 Hz plus notches at " + m.mains_hz + " Hz and its harmonics). When the recording closes, the view switches to the offline cleaning used for the dataset."}</p>
      <p><b>Floor:</b> ${S.floorSrc}. Values ${S.floor.map(f => fmt(f, 2)).join(" / ")} mV. The envelope lane shows the envelope divided by this floor (1× = room noise at rest).</p>
      <p><b>Envelope:</b> moving RMS, ${S.opts.env_window_s * 1000} ms window (${S.clean ? "centred" : "trailing, as it would be live"}).</p>
      <p><b>Detected activity:</b> envelope above ${S.opts.on_x}× floor, ending when it falls below ${S.opts.off_x}×, gaps under ${S.opts.merge_s} s closed, periods under ${S.opts.min_s} s dropped. These are the person's measured activity windows; the orange cue bands are when they were asked to act.</p>
      <p><b>Frequencies:</b> Welch spectrum (256-point Hann windows, half overlap), median and mean between 20 and 450 Hz. Muscle signal usually sits around 80–150 Hz on the forearm; a median near the background's means little or no muscle signal.</p>
      <p><b>"% of recording max"</b> is relative to the largest envelope in this recording. It is not %MVC unless a maximum contraction was recorded.</p>
      <p><b>CT view:</b> two regions (palm side and back of the forearm muscle) coloured from grey (1× floor) to the channel colour (10× or more) at the cursor time. Two channels give two numbers per moment: not single muscles.</p>
      <p class="muted">Red ticks on the baseline mark missing samples. Research use only; not for diagnosis or treatment.</p>`;
  }

  // ------------------------------------------------------------------ export
  function exportCsv() {
    if (!S.meta || !S.n) return;
    const R = range(), i0 = clamp(Math.floor(R.a * S.fs), 0, S.n), i1 = clamp(Math.ceil(R.b * S.fs), 0, S.n);
    if (i1 - i0 > 2e6) { alert("Select 15 minutes or less to export."); return; }
    const cols = ["t_s"]; for (let c = 0; c < S.nch; c++) cols.push(`ch${c + 1}_pin_mv`, `ch${c + 1}_filtered_mv`, `ch${c + 1}_envelope_x_floor`);
    // missing values as NaN (not the display dash) so the numeric columns stay numeric in pandas / numpy
    const num = (v, d) => (v === null || v === undefined || !isFinite(v)) ? "NaN" : Number(v).toFixed(d);
    const lines = [cols.join(",")];
    for (let i = i0; i < i1; i++) {
      const row = [(i / S.fs).toFixed(4)];
      for (let c = 0; c < S.nch; c++) row.push(num(S.pin[c].data[i], 3), num(procOf(c)[i], 4), num(S.ratio[c] ? S.ratio[c][i] : NaN, 3));
      lines.push(row.join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" }), a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${S.rid}_${R.a.toFixed(1)}-${R.b.toFixed(1)}s.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  // ------------------------------------------------------------------ controls
  function seg(id, attr, cur, fn) {
    const el = $(id);
    const mark = (v) => el.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset[attr] === String(v)));
    el.querySelectorAll("button").forEach(b => b.onclick = () => { fn(b.dataset[attr]); mark(b.dataset[attr]); });
    mark(cur);
  }
  function fillSpecCh() {
    const s = $("specCh"); s.innerHTML = "";
    for (let c = 0; c < S.nch; c++) { const o = document.createElement("option"); o.value = c; o.textContent = `CH${c + 1}`; s.appendChild(o); }
    s.value = Math.min(S.specCh, Math.max(0, S.nch - 1));
  }
  function bindControls() {
    $("rec").onchange = () => openRec($("rec").value);
    $("btnLatest").onclick = async () => { const list = await listRecordings(); if (list.length) { setFollow(true); openRec(list[0].recording_id); } };
    $("btnPlay").onclick = togglePlay;
    $("speed").onchange = () => { S.speed = parseFloat($("speed").value); };
    $("loop").onchange = () => { S.loop = $("loop").checked; };
    $("follow").checked = S.follow;
    $("follow").onchange = () => { setFollow($("follow").checked); if (S.follow && S.status === "recording") setWindow(S.t1 - S.t0); };
    seg("win", "w", "", (v) => setWindow(v === "all" ? "all" : parseFloat(v)));
    $("zoomIn").onclick = () => zoomAt((S.t0 + S.t1) / 2, 0.6);
    $("zoomOut").onclick = () => zoomAt((S.t0 + S.t1) / 2, 1 / 0.6);
    seg("sigmode", "m", S.mode, (v) => { S.mode = v; store.set("mode", v); S.specCache = null; S.dirty = true; scheduleStats(); });
    seg("units", "u", S.units, (v) => { S.units = v; store.set("units", v); S.dirty = true; scheduleStats(); });
    $("specOn").onchange = () => { S.specOn = $("specOn").checked; S.dirty = true; };
    $("specCh").onchange = () => { S.specCh = +$("specCh").value; S.specCache = null; S.dirty = true; };
    $("selMode").onchange = () => { S.selMode = $("selMode").checked; };
    const det = () => {
      S.opts.env_window_s = parseFloat($("envWin").value); S.opts.on_x = parseFloat($("onX").value) || 3;
      S.opts.off_x = Math.min(parseFloat($("offX").value) || 2, S.opts.on_x); S.opts.min_s = parseFloat($("minS").value) || 0;
      derive(true); renderAbout();
    };
    ["envWin", "onX", "offX", "minS"].forEach(id => $(id).onchange = det);
    $("floorSel").onclick = () => {
      if (!S.sel) { alert("Select a rest stretch first (Shift+drag, or tick 'select (touch)')."); return; }
      const a = Math.floor(Math.min(S.sel.a, S.sel.b) * S.fs), b = Math.ceil(Math.max(S.sel.a, S.sel.b) * S.fs);
      S.floor = Array.from({ length: S.nch }, (_, c) => EMG.stats(procOf(c), a, b, S.fs).rms || null);
      S.floorSrc = `set from the selection ${(a / S.fs).toFixed(2)}–${(b / S.fs).toFixed(2)} s in this viewer`;
      derive(true); renderChips(); renderAbout();
    };
    $("floorReset").onclick = () => { S.floor = S.floorDefault.slice(); S.floorSrc = S.meta.floor_source || "no floor known"; derive(true); renderChips(); renderAbout(); };
    $("exportCsv").onclick = exportCsv;
    document.querySelectorAll(".tabbar button").forEach(b => b.onclick = () => {
      document.querySelectorAll(".tabbar button").forEach(x => x.classList.toggle("on", x === b));
      document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
    });
    const ramp = $("rampbar"); ramp.style.background = `linear-gradient(90deg, ${mix(GREY, GREY, 0)}, ${COL[0]}) top/100% 50% no-repeat, linear-gradient(90deg, ${mix(GREY, GREY, 0)}, ${COL[1]}) bottom/100% 50% no-repeat`;
    new ResizeObserver(() => { S.dirty = true; scheduleStats(); }).observe(tl);
  }

  // ------------------------------------------------------------------ start
  async function start() {
    bindControls();
    const list = await listRecordings();
    const h = parseHash();
    const rid = h.rid && list.some(x => x.recording_id === h.rid) ? h.rid : (list[0] && list[0].recording_id);
    initCT();
    if (rid) openRec(rid, true);
    // a link pasted into an open tab only changes the hash: follow it (writeHash uses replaceState, which
    // does not fire this event, so the viewer's own updates do not loop back here)
    window.addEventListener("hashchange", () => {
      const h = parseHash();
      if (h.rid && h.rid !== S.rid) openRec(h.rid, true);
      else if (h.rid === S.rid && h.t1 > h.t0 && S.status !== "recording") zoomTo(h.t0, h.t1);
    });
    setInterval(() => listRecordings(S.rid), 5000);
    requestAnimationFrame(frame);
  }
  window.__viewer = S;  // for debugging and the browser test
  start();
})();
