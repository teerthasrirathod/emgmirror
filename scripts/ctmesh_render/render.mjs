// node render.mjs SERVE_ROOT OUT.png GLB_URL_RELATIVE_TO_ROOT [query...]   (needs three@0.170 in SERVE_ROOT/node_modules)
import http from 'http'; import fs from 'fs'; import path from 'path';
import { createRequire } from 'module';
const { chromium } = createRequire(import.meta.url)('playwright');
const [root, out, glb, ...extra] = process.argv.slice(2);
const types = {'.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.glb': 'model/gltf-binary'};
const srv = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(req.url.split('?')[0]));
  if (!p.startsWith(path.resolve(root)) || !fs.existsSync(p)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {'Content-Type': types[path.extname(p)] || 'application/octet-stream'}); fs.createReadStream(p).pipe(res);
}).listen(0);
const port = srv.address().port;
const browser = await chromium.launch({args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport: {width: 900, height: 900}, deviceScaleFactor: 1});
const qs = new URLSearchParams([['glb', '/' + glb], ...extra.map(e => e.split('='))]).toString();
await page.goto(`http://127.0.0.1:${port}/render.html?${qs}`);
await page.waitForFunction(() => window.__done, null, {timeout: 120000});
const done = await page.evaluate(() => window.__done);
await page.screenshot({path: out});
console.log(JSON.stringify({out, ...done}));
await browser.close(); srv.close();
