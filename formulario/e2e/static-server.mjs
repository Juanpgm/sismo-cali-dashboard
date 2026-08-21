// Zero-dependency static server for the ATC-20 form, used by Playwright's
// webServer during E2E. Serves formulario/ (the parent of this file's dir).
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, normalize, extname } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url))); // formulario/
const port = Number(process.env.PORT) || 4321;

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

createServer(async (req, res) => {
  try {
    let path = decodeURIComponent(req.url.split('?')[0]);
    if (path === '/') path = '/index.html';
    // Contain the path inside root (no traversal).
    const full = normalize(join(root, path));
    if (!full.startsWith(root)) { res.writeHead(403).end('Forbidden'); return; }
    const body = await readFile(full);
    res.writeHead(200, { 'Content-Type': TYPES[extname(full)] || 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end('Not found');
  }
}).listen(port, () => console.log(`static-server on http://localhost:${port}`));
