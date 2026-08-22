// Tutorial 2 — the dashboard "Stickers" tab: monitor field evaluations and
// manage inspector accounts (create, enable, disable).
//
// Runs the REAL dashboard modules (web/js/stickers.js + evaluaciones.js +
// styles.css) against a stateful in-page stub of /api/stickers: creating or
// disabling an inspector on the production API would create real accounts,
// and the rendered UI is identical either way.
//
// Usage: node videos/tools/video-stickers.mjs <out-dir>
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { OVERLAY_INIT, runSteps, synthesize, mux } from './recorder.mjs';

const require = createRequire('C:/Users/User/Documents/workspace/seismic_disaster_data_analisys_cali/formulario/package.json');
const { chromium } = require('@playwright/test');

const outDir = process.argv[2];
if (!outDir) { console.error('usage: node video-stickers.mjs <out-dir>'); process.exit(1); }

const ROOT = 'C:/Users/User/Documents/workspace/seismic_disaster_data_analisys_cali/web';
const W = 1280;
const H = 800;

// ---- Demo page: real modules, stateful stubbed API --------------------------
const HARNESS = `<!doctype html><html lang="es" data-theme="dark"><head><meta charset="UTF-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><link rel="stylesheet" href="styles.css"></head>
<body><div class="app-shell asig-active"><div class="main-column">
<nav class="view-tabs" role="tablist" aria-label="Vistas">
  <button type="button" class="view-tab" data-view="panel" role="tab">Panel</button>
  <button type="button" class="view-tab" data-view="acciones" role="tab" aria-disabled="true" tabindex="-1">Acciones</button>
  <button type="button" class="view-tab is-active" data-view="stickers" role="tab" aria-selected="true">Stickers</button>
</nav>
<section id="view-stickers" data-view-panel="stickers"></section></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const mk=(id,cl,n,lat,lng,fotos,fecha)=>({id,codigo_edificacion:id,consecutivo:Number(id.slice(-4)),municipio:'76001',area:1,area_nombre:'Cabecera',clasificacion:cl,alcance:'exterior',coords:lat?{lat,lng,accuracy:9}:null,inspector:{uid:'u1',codigo:'103',nombre_completo:'Andrés Torres',identificacion:'1020735324',entidad:'SGRED'},descripcion:{nombre:n,direccion:'Cra 39 # 5-12'},restricciones:cl==='INSEGURO'?'Edificación evacuada.':'',acciones_posteriores:{barricadas:cl==='INSEGURO',evaluacion_detallada:cl!=='INSPECCIONADA'},comentarios:'Registrado en campo.',fotos:fotos||[],fecha});
const FOTO='https://placehold.co/640x480/8a99a8/ffffff.jpg';
const EVALS=[
  mk('76001-1-1030006','INSPECCIONADA','Edificio San Fernando',3.4372,-76.5225,[FOTO,FOTO],'2026-08-21T16:20:00Z'),
  mk('76001-1-1030005','USO_RESTRINGIDO','Casa Granada',3.4512,-76.5320,[FOTO],'2026-08-21T14:05:00Z'),
  mk('76001-1-1030004','INSEGURO','Bodega El Prado',3.4080,-76.5490,[FOTO],'2026-08-21T11:40:00Z'),
  mk('76001-1-1030003','INSPECCIONADA','Torre Norte',3.4650,-76.5150,[],'2026-08-20T15:10:00Z'),
  mk('76001-1-1030002','INSEGURO','Colegio Central',3.4280,-76.5380,[FOTO],'2026-08-20T10:02:00Z'),
  mk('76001-1-1030001','USO_RESTRINGIDO','Casa La Merced',3.4450,-76.5410,[],'2026-08-19T09:30:00Z'),
];
const state={inspectores:[
  {uid:'u1',email:'1020735324@sismocali.gov.co',cedula:'1020735324',nombre_completo:'Andrés Torres',codigo:'103',entidad:'SGRED',registros:6,registrado:true,disabled:false,activo:true},
  {uid:'u2',email:'1085270230@sismocali.gov.co',cedula:'1085270230',nombre_completo:'Leidy Guerrero',codigo:'104',entidad:'SGRED',registros:3,registrado:true,disabled:false,activo:true},
  {uid:'u3',email:'14139624@sismocali.gov.co',cedula:'14139624',nombre_completo:'Nelson Rodríguez',codigo:'101',entidad:'DAGMA',registros:0,registrado:true,disabled:true,activo:false},
]};
window.fetch=async(url,opts)=>{
  const b=JSON.parse(opts.body);
  await new Promise(r=>setTimeout(r,350)); // small realistic latency
  if(b.action==='list') return {ok:true,json:async()=>({ok:true,inspectores:state.inspectores})};
  if(b.action==='evaluaciones') return {ok:true,json:async()=>({ok:true,evaluaciones:EVALS})};
  if(b.action==='create'){
    const codigo='100'.slice(0,3-String(state.next||105).length)+String(state.next||105);
    state.inspectores.push({uid:'u'+(state.inspectores.length+1),email:b.cedula+'@sismocali.gov.co',cedula:b.cedula,nombre_completo:b.nombre_completo||'',codigo:'105',entidad:b.entidad||'',registros:0,registrado:true,disabled:false,activo:true});
    return {ok:true,json:async()=>({ok:true,codigo:'105'})};
  }
  if(b.action==='setEnabled'){
    const i=state.inspectores.find(x=>x.uid===b.uid);
    if(i){i.disabled=!(b.enabled===true||b.enabled==='true');i.activo=!i.disabled;}
    return {ok:true,json:async()=>({ok:true})};
  }
  return {ok:true,json:async()=>({ok:true})};
};
</script>
<script type="module">
import {initStickers} from './js/stickers.js';
initStickers(document.getElementById('view-stickers'),{getToken:async()=>'demo'});
</script></body></html>`;

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
const server = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/demo.html') { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(HARNESS); return; }
  fs.readFile(path.join(ROOT, p), (err, buf) => {
    if (err) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
    res.end(buf);
  });
});
await new Promise((r) => server.listen(4650, r));

const go = async (page, sel) => {
  const el = page.locator(sel).first();
  await el.scrollIntoViewIfNeeded();
  await el.hover();
  await page.waitForTimeout(400);
  await el.click();
};
const typeSlow = async (page, sel, text) => {
  await go(page, sel);
  await page.locator(sel).first().pressSequentially(text, { delay: 70 });
};

const STEPS = [
  {
    text: 'Bienvenido. En este video aprenderá a usar la pestaña Stickers del dashboard: aquí se monitorea la actividad de las brigadas y se administran los inspectores de campo.',
    action: async (page) => { await page.waitForSelector('.eval-row', { timeout: 20000 }); },
  },
  {
    text: 'Arriba están los indicadores: cuántas evaluaciones se han registrado y cuántas son inspeccionada, uso restringido o inseguro — los tres colores de la placa ATC-20.',
    action: async (page) => { await page.locator('#eval-kpis .kpi-tile').first().hover(); },
  },
  {
    text: 'El mapa muestra cada evaluación como un punto, con el color de su placa. Puede acercar, alejar, y tocar un punto para ver su resumen.',
    action: async (page) => {
      await page.locator('#eval-map path.leaflet-interactive').first().click();
      await page.waitForTimeout(800);
    },
  },
  {
    text: 'A la derecha está la lista de registros, del más reciente al más antiguo. Al pasar el mouse por un registro, su punto se resalta en el mapa.',
    action: async (page) => {
      await page.keyboard.press('Escape');
      for (const i of [0, 1, 2]) {
        await page.locator('.eval-row').nth(i).hover();
        await page.waitForTimeout(900);
      }
    },
  },
  {
    text: 'Toque cualquier registro para abrir el detalle completo: la clasificación, el código, el mini mapa, las fotos tomadas en campo y todos los datos del inspector.',
    action: async (page) => {
      await go(page, '.eval-row');
      await page.waitForSelector('#eval-modal.is-open', { timeout: 10000 });
      await page.waitForTimeout(1200);
      await page.locator('#eval-modal .modal-body').evaluate((el) => el.scrollTo({ top: 400, behavior: 'smooth' }));
    },
  },
  {
    text: 'Las fotos se pueden ampliar tocándolas. Para cerrar el detalle, use la equis o la tecla Escape.',
    action: async (page) => {
      await go(page, '#eval-modal [data-eval-close][aria-label="Cerrar"]');
      await page.waitForTimeout(400);
    },
  },
  {
    text: 'Más abajo está la sección de inspectores de campo: quiénes pueden registrar evaluaciones, cuántos registros lleva cada uno, y su estado.',
    action: async (page) => {
      await page.locator('.sticker-roster').scrollIntoViewIfNeeded();
      await page.waitForTimeout(600);
    },
  },
  {
    text: 'Para crear un inspector nuevo, pulse "Nuevo inspector".',
    action: async (page) => {
      await go(page, '#sticker-new');
      await page.waitForSelector('#sticker-modal.is-open', { timeout: 5000 });
    },
  },
  {
    text: 'Escriba la cédula del inspector — será su usuario para entrar al formulario — su nombre y su entidad.',
    action: async (page) => {
      await typeSlow(page, '#sticker-form [name="cedula"]', '94534166');
      await typeSlow(page, '#sticker-form [name="nombre_completo"]', 'César Rojas');
      await typeSlow(page, '#sticker-form [name="entidad"]', 'SGRED');
    },
  },
  {
    text: 'Asigne la contraseña. La clave por defecto que usamos es: Cali2026+- — la palabra Cali, el año 2026, el signo más y el signo menos.',
    action: async (page) => { await typeSlow(page, '#sticker-form [name="password"]', 'Cali2026+-'); },
  },
  {
    text: 'El código de brigada no se escribe: el sistema lo asigna automáticamente con el número libre más bajo, sin repetir nunca uno ya entregado.',
    action: async (page) => { await page.locator('.sticker-note').hover(); },
  },
  {
    text: 'Pulse "Crear inspector". El nuevo inspector aparece en la lista y arriba se confirma el código de brigada que recibió.',
    action: async (page) => {
      await go(page, '#sticker-submit');
      await page.waitForSelector('#sticker-ok:not([hidden])', { timeout: 10000 });
      await page.locator('#sticker-ok').scrollIntoViewIfNeeded();
      await page.waitForTimeout(800);
    },
  },
  {
    text: 'Para suspender el acceso de un inspector, pulse "Inhabilitar": no podrá registrar más evaluaciones hasta que se le habilite de nuevo.',
    action: async (page) => {
      await go(page, '.sticker-row .sticker-action[data-enable="false"]');
      await page.waitForTimeout(1500);
    },
  },
  {
    text: 'Y para devolverle el acceso, pulse "Habilitar". El cambio es inmediato.',
    action: async (page) => {
      await go(page, '.sticker-row .sticker-action[data-enable="true"]');
      await page.waitForTimeout(1500);
    },
  },
  {
    text: 'Recuerde: la pestaña se actualiza sola cada pocos minutos, y con el botón Actualizar puede traer los datos al instante. Eso es todo — ¡buen monitoreo!',
    action: async (page) => {
      await page.locator('#eval-reload').scrollIntoViewIfNeeded();
      await page.locator('#eval-reload').hover();
    },
  },
];

const clipsDir = path.join(outDir, 'clips-stickers');
console.log('synthesizing narration…');
await synthesize(STEPS, clipsDir);

console.log('recording…');
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: W, height: H },
  recordVideo: { dir: outDir, size: { width: W, height: H } },
  locale: 'es-CO',
});
await context.addInitScript(OVERLAY_INIT);
const page = await context.newPage();
const t0 = Date.now();
await page.goto('http://localhost:4650/demo.html', { waitUntil: 'domcontentloaded' });

const cues = await runSteps(page, STEPS, clipsDir, t0);

await context.close();
const webm = await page.video().path();
await browser.close();
server.close();

console.log('muxing…');
const out = path.join(outDir, 'tutorial-stickers-dashboard.mp4');
await mux(webm, cues, clipsDir, out);
fs.rmSync(webm, { force: true });
console.log('done:', out);
