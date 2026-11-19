/* emgcore.js — signal maths for the viewer. Pure functions, no DOM: runs in the browser and in Node
 * (tests/test_viewer_js.py). Definitions match emgmirror/viewer/analysis.py.
 */
(function (root) {
  "use strict";

  // ---------------------------------------------------------------- filters (RBJ biquads)
  function biquad(type, f0, fs, Q) {
    const w = 2 * Math.PI * f0 / fs, c = Math.cos(w), s = Math.sin(w), a = s / (2 * Q);
    let b0, b1, b2, a0, a1, a2;
    if (type === "lp") { b0 = (1 - c) / 2; b1 = 1 - c; b2 = (1 - c) / 2; }
    else if (type === "hp") { b0 = (1 + c) / 2; b1 = -(1 + c); b2 = (1 + c) / 2; }
    else if (type === "notch") { b0 = 1; b1 = -2 * c; b2 = 1; }
    else throw new Error("biquad type " + type);
    a0 = 1 + a; a1 = -2 * c; a2 = 1 - a;
    return { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0, z1: 0, z2: 0 };
  }

  // 4th-order Butterworth = two biquads with these Qs
  const BW4_Q = [0.5411961, 1.3065630];

  /** Causal live filter: 4th-order Butterworth band-pass + notches at mains and its harmonics. */
  function liveFilter(fs, band, mainsHz, notchQ) {
    const secs = [];
    for (const q of BW4_Q) secs.push(biquad("hp", band[0], fs, q));
    for (const q of BW4_Q) secs.push(biquad("lp", band[1], fs, q));
    if (mainsHz) for (let h = mainsHz; h <= Math.min(band[1] * 1.05, fs * 0.45); h += mainsHz) secs.push(biquad("notch", h, fs, notchQ || 30));
    return {
      sections: secs,
      reset() { for (const s of secs) { s.z1 = 0; s.z2 = 0; } },
      /** x -> new Float32Array; NaN in -> NaN out (filter state sees 0 there) */
      process(x) {
        const out = new Float32Array(x.length);
        for (let i = 0; i < x.length; i++) {
          const nan = !(x[i] === x[i]);
          let v = nan ? 0 : x[i];
          for (const s of secs) {           // transposed direct form II
            const y = s.b0 * v + s.z1;
            s.z1 = s.b1 * v - s.a1 * y + s.z2;
            s.z2 = s.b2 * v - s.a2 * y;
            v = y;
          }
          out[i] = nan ? NaN : v;
        }
        return out;
      },
    };
  }

  /** Magnitude response of a section list at f (for tests and the filter readout). */
  function gainAt(sections, f, fs) {
    const w = 2 * Math.PI * f / fs;
    let re = 1, im = 0;
    for (const s of sections) {
      const nr = s.b0 + s.b1 * Math.cos(w) + s.b2 * Math.cos(2 * w), ni = -(s.b1 * Math.sin(w) + s.b2 * Math.sin(2 * w));
      const dr = 1 + s.a1 * Math.cos(w) + s.a2 * Math.cos(2 * w), di = -(s.a1 * Math.sin(w) + s.a2 * Math.sin(2 * w));
      const d2 = dr * dr + di * di, hr = (nr * dr + ni * di) / d2, hi = (ni * dr - nr * di) / d2;
      const r2 = re * hr - im * hi; im = re * hi + im * hr; re = r2;
    }
    return Math.hypot(re, im);
  }

  // ---------------------------------------------------------------- envelope
  /** Moving RMS over `win` samples; NaN samples left out. centered (offline) or trailing (live). */
  function envelope(y, win, centered, n) {
    n = n === undefined ? y.length : n;
    const cs = new Float64Array(n + 1), cn = new Int32Array(n + 1);
    for (let i = 0; i < n; i++) {
      const v = y[i], ok = v === v;
      cs[i + 1] = cs[i] + (ok ? v * v : 0);
      cn[i + 1] = cn[i] + (ok ? 1 : 0);
    }
    const out = new Float32Array(n), h = Math.floor(win / 2);
    for (let i = 0; i < n; i++) {
      let lo, hi;
      if (centered) { lo = Math.min(Math.max(i - h, 0), n); hi = Math.min(Math.max(i - h + win, 0), n); }
      else { lo = Math.max(i - win + 1, 0); hi = i + 1; }
      const c = cn[hi] - cn[lo];
      out[i] = c ? Math.sqrt((cs[hi] - cs[lo]) / c) : NaN;
    }
    return out;
  }

  // ---------------------------------------------------------------- activations
  function activations(ratio, fs, o, n) {
    n = n === undefined ? ratio.length : n;
    const segs = [];
    let on = false, start = 0;
    for (let i = 0; i < n; i++) {
      const v = ratio[i], ok = v === v;
      if (!on && ok && v >= o.on_x) { on = true; start = i; }
      else if (on && !(ok && v >= o.off_x)) { on = false; segs.push([start, i]); }
    }
    if (on) segs.push([start, n]);
    const merged = [], gap = Math.round(o.merge_s * fs), keep = Math.round(o.min_s * fs);
    for (const s of segs) {
      if (merged.length && s[0] - merged[merged.length - 1][1] < gap) merged[merged.length - 1][1] = s[1];
      else merged.push(s.slice());
    }
    return merged.filter(s => s[1] - s[0] >= keep);
  }

  // ---------------------------------------------------------------- FFT / spectra
  function fft(re, im) {          // in place, length = power of two
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = a + len / 2;
          const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
          re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
          const nr = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = nr;
        }
      }
    }
  }

  const _hann = {};
  function hann(n) {
    if (!_hann[n]) { const w = new Float64Array(n); for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / n); _hann[n] = w; }
    return _hann[n];
  }

  /** One-sided PSD of y[i0:i1] (Welch, Hann, 50 % overlap), segments with NaN skipped. */
  function welch(y, i0, i1, fs, nfft) {
    nfft = nfft || 256;
    const w = hann(nfft), half = nfft / 2 + 1, p = new Float64Array(half);
    let wss = 0; for (let i = 0; i < nfft; i++) wss += w[i] * w[i];
    let segs = 0;
    const re = new Float64Array(nfft), im = new Float64Array(nfft);
    for (let s = i0; s + nfft <= i1; s += nfft / 2) {
      let mean = 0, bad = false;
      for (let i = 0; i < nfft; i++) { const v = y[s + i]; if (!(v === v)) { bad = true; break; } mean += v; }
      if (bad) continue;
      mean /= nfft;
      for (let i = 0; i < nfft; i++) { re[i] = (y[s + i] - mean) * w[i]; im[i] = 0; }
      fft(re, im);
      for (let k = 0; k < half; k++) p[k] += re[k] * re[k] + im[k] * im[k];
      segs++;
    }
    const f = new Float64Array(half);
    for (let k = 0; k < half; k++) {
      f[k] = k * fs / nfft;
      p[k] = segs ? p[k] / (segs * fs * wss) * (k && k < half - 1 ? 2 : 1) : NaN;
    }
    return { f, p, segments: segs };
  }

  function spectralStats(f, p, lo, hi) {
    let tot = 0, mom = 0;
    for (let k = 0; k < f.length; k++) if (f[k] >= lo && f[k] <= hi && p[k] === p[k]) { tot += p[k]; mom += f[k] * p[k]; }
    if (!(tot > 0)) return { median_hz: null, mean_hz: null };
    let c = 0, med = null;
    for (let k = 0; k < f.length; k++) if (f[k] >= lo && f[k] <= hi) { c += p[k]; if (c >= tot / 2) { med = f[k]; break; } }
    return { median_hz: med, mean_hz: mom / tot };
  }

  /** Columns of dB power, nfft-point Hann windows every `hop` samples over [i0, i1). */
  function spectrogram(y, i0, i1, fs, nfft, hop, fmax) {
    const w = hann(nfft), nb = Math.min(nfft / 2 + 1, Math.floor(fmax * nfft / fs) + 1);
    const cols = Math.max(0, Math.floor((i1 - i0 - nfft) / hop) + 1);
    const out = new Float32Array(cols * nb), re = new Float64Array(nfft), im = new Float64Array(nfft);
    for (let c = 0; c < cols; c++) {
      const s = i0 + c * hop;
      let bad = false;
      for (let i = 0; i < nfft; i++) { const v = y[s + i]; if (!(v === v)) { bad = true; break; } re[i] = v * w[i]; im[i] = 0; }
      if (bad) { for (let k = 0; k < nb; k++) out[c * nb + k] = NaN; continue; }
      fft(re, im);
      for (let k = 0; k < nb; k++) out[c * nb + k] = 10 * Math.log10(re[k] * re[k] + im[k] * im[k] + 1e-12);
    }
    return { cols, bins: nb, db: out, t0: i0 + nfft / 2, hop };
  }

  // ---------------------------------------------------------------- window stats
  function stats(y, i0, i1, fs, floor) {
    let n = 0, ss = 0, sa = 0, peak = 0, mn = Infinity, mx = -Infinity;
    for (let i = i0; i < i1; i++) {
      const v = y[i]; if (!(v === v)) continue;
      n++; ss += v * v; sa += Math.abs(v); if (Math.abs(v) > peak) peak = Math.abs(v); if (v < mn) mn = v; if (v > mx) mx = v;
    }
    if (n < 16) return { n };
    const rms = Math.sqrt(ss / n);
    const out = { n, seconds: n / fs, rms, mav: sa / n, peak, p2p: mx - mn };
    const nf = n >= 256 ? 256 : 1 << Math.floor(Math.log2(n));
    const w = welch(y, i0, i1, fs, nf);
    Object.assign(out, spectralStats(w.f, w.p, 20, 450));
    if (floor) { out.snr_db = rms > 0 ? 20 * Math.log10(rms / floor) : null; out.net_rms = Math.sqrt(Math.max(rms * rms - floor * floor, 0)); }
    return out;
  }

  /** Min and max per bucket over [i0, i1): fast drawing of millions of samples. */
  function minmax(y, i0, i1, buckets) {
    const mn = new Float32Array(buckets), mx = new Float32Array(buckets), span = (i1 - i0) / buckets;
    for (let b = 0; b < buckets; b++) {
      const a = Math.floor(i0 + b * span), e = Math.max(a + 1, Math.floor(i0 + (b + 1) * span));
      let lo = Infinity, hi = -Infinity;
      for (let i = a; i < e; i++) { const v = y[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      mn[b] = lo === Infinity ? NaN : lo; mx[b] = hi === -Infinity ? NaN : hi;
    }
    return { mn, mx };
  }

  /** Growable float buffer (live data arrives in blocks). */
  function Series(cap) {
    this.data = new Float32Array(cap || 1 << 16); this.length = 0;
  }
  Series.prototype.push = function (block, stride, offset) {
    const n = Math.floor(block.length / stride);
    if (this.length + n > this.data.length) {
      let cap = this.data.length; while (cap < this.length + n) cap *= 2;
      const d = new Float32Array(cap); d.set(this.data.subarray(0, this.length)); this.data = d;
    }
    for (let i = 0; i < n; i++) this.data[this.length + i] = block[i * stride + offset];
    this.length += n;
  };
  Series.prototype.view = function () { return this.data.subarray(0, this.length); };

  const EMG = { biquad, liveFilter, gainAt, envelope, activations, fft, welch, spectralStats, spectrogram, stats, minmax, Series };
  if (typeof module !== "undefined" && module.exports) module.exports = EMG; else root.EMG = EMG;
})(typeof self !== "undefined" ? self : this);
