// Tutorial 1 — filling the ATC-20 field form at
// https://formulario-atc20-cali.vercel.app/
//
// Records against the REAL production form, logged in with a real inspector
// account, up to (but never past) the submit button: pressing it would write
// a real evaluation into production Firestore, so the last step narrates the
// send instead of performing it. Everything before submit is local or
// read-only (photo uploads only happen on submit).
//
// Usage: node videos/tools/video-formulario.mjs <cedula> <password> <sample-photo> <out-dir>
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { OVERLAY_INIT, runSteps, synthesize, mux } from './recorder.mjs';

// Playwright lives in formulario/node_modules; resolve it from there.
const require = createRequire('C:/Users/User/Documents/workspace/seismic_disaster_data_analisys_cali/formulario/package.json');
const { chromium } = require('@playwright/test');

const [cedula, password, samplePhoto, outDir] = process.argv.slice(2);
if (!cedula || !password || !samplePhoto || !outDir) {
  console.error('usage: node video-formulario.mjs <cedula> <password> <sample-photo> <out-dir>');
  process.exit(1);
}

const URL = 'https://formulario-atc20-cali.vercel.app/';
const W = 480;
const H = 854;

// A "human" click: glide the fake cursor to the element, then click it.
const go = async (page, sel) => {
  const el = page.locator(sel).first();
  await el.scrollIntoViewIfNeeded();
  await el.hover();
  await page.waitForTimeout(350);
  await el.click();
};
const typeSlow = async (page, sel, text) => {
  await go(page, sel);
  await page.locator(sel).first().pressSequentially(text, { delay: 90 });
};

const STEPS = [
  {
    text: 'Bienvenido. En este video aprenderá a registrar una evaluación ATC-20 desde el formulario de campo. Entre desde su celular a: formulario-atc20-cali.vercel.app',
    action: async (page) => { await page.waitForSelector('#auth-form', { timeout: 30000 }); },
  },
  {
    text: 'Escriba su número de cédula, sin puntos ni espacios.',
    action: async (page) => { await typeSlow(page, '#auth-email', cedula); },
  },
  {
    text: 'Escriba su contraseña. La clave por defecto es: Cali2026+- — es decir, la palabra Cali con C mayúscula, el año 2026, el signo más y el signo menos.',
    action: async (page) => { await typeSlow(page, '#auth-password', password); },
  },
  {
    text: 'Pulse Ingresar. El sistema valida su usuario de inspector.',
    action: async (page) => {
      await go(page, '#auth-submit');
      await page.waitForSelector('#app:not([hidden])', { timeout: 45000 });
    },
  },
  {
    text: 'Al entrar, el formulario obtiene su ubicación GPS automáticamente. Si no aparece, pulse "Actualizar ubicación" y acepte el permiso del navegador.',
    action: async (page) => {
      await page.locator('#geo-display').scrollIntoViewIfNeeded();
      await page.locator('#btn-geo').hover();
    },
  },
  {
    text: 'Seleccione el área donde está la edificación: Cabecera, Centro Poblado o Rural Disperso.',
    action: async (page) => {
      await go(page, '#area');
      await page.selectOption('#area', '1');
    },
  },
  {
    text: 'Pulse "Generar código". El sistema crea el código único de la edificación usando su número de brigada y un consecutivo automático.',
    action: async (page) => {
      await go(page, '#btn-codigo');
      await page.waitForSelector('#codigo-display:not([hidden])', { timeout: 30000 });
    },
  },
  {
    text: 'Escriba el nombre de la edificación y su dirección.',
    action: async (page) => {
      await typeSlow(page, '#nombre', 'Edificio de ejemplo');
      await typeSlow(page, '#direccion', 'Cra 39 # 5-12');
    },
  },
  {
    text: 'Marque la clasificación según lo observado: Inspeccionada, Uso Restringido o Inseguro. Esta es la placa que queda pegada en la edificación.',
    action: async (page) => {
      // The inputs are visually hidden (styled controls) — click their labels.
      await go(page, 'label:has(input[name="clasificacion"][value="INSPECCIONADA"])');
    },
  },
  {
    text: 'Indique el alcance de la inspección: solo el exterior, o el exterior y el interior.',
    action: async (page) => { await go(page, 'label:has(input[name="alcance"][value="exterior"])'); },
  },
  {
    text: 'Si aplica, describa las restricciones de uso de la edificación.',
    action: async (page) => { await typeSlow(page, '#restricciones', 'Ejemplo: no usar el segundo piso.'); },
  },
  {
    text: 'Agregue las fotos de la edificación. Muy importante: incluya una foto del sticker pegado. Puede elegir fotos de la galería o tomarlas en el momento.',
    action: async (page) => {
      await page.locator('#btn-foto-galeria').scrollIntoViewIfNeeded();
      const chooser = page.waitForEvent('filechooser');
      await go(page, '#btn-foto-galeria');
      await (await chooser).setFiles(samplePhoto);
      await page.waitForSelector('.foto-tile', { timeout: 15000 });
    },
  },
  {
    text: 'Si lo necesita, abra "Acciones posteriores", marque las recomendaciones y agregue comentarios.',
    action: async (page) => {
      // The checkboxes live inside a collapsed <details>; open it first.
      await go(page, '.acciones-card summary');
      await go(page, 'label:has(#evaluacion_detallada)');
      await typeSlow(page, '#comentarios', 'Comentario de ejemplo.');
    },
  },
  {
    text: 'Para terminar, revise la información y pulse "Enviar evaluación". Verá una pantalla de confirmación con el código del registro. En este video no la enviaremos, porque es una demostración.',
    action: async (page) => {
      await page.locator('#btn-submit').scrollIntoViewIfNeeded();
      await page.locator('#btn-submit').hover();
    },
  },
  {
    text: 'Y listo. Recuerde: su usuario es su número de cédula y la clave por defecto es Cali2026+-. ¡Buen trabajo en campo!',
    action: async (page) => { await page.waitForTimeout(300); },
  },
];

const clipsDir = path.join(outDir, 'clips-formulario');
console.log('synthesizing narration…');
await synthesize(STEPS, clipsDir);

console.log('recording…');
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: W, height: H },
  recordVideo: { dir: outDir, size: { width: W, height: H } },
  geolocation: { latitude: 3.4372, longitude: -76.5225, accuracy: 10 },
  permissions: ['geolocation'],
  locale: 'es-CO',
});
await context.addInitScript(OVERLAY_INIT);
const page = await context.newPage();
const t0 = Date.now();
await page.goto(URL, { waitUntil: 'domcontentloaded' });

const cues = await runSteps(page, STEPS, clipsDir, t0);

await context.close(); // flushes the video file
const webm = (await page.video().path());
await browser.close();

console.log('muxing…');
const out = path.join(outDir, 'tutorial-formulario-atc20.mp4');
await mux(webm, cues, clipsDir, out);
fs.rmSync(webm, { force: true });
console.log('done:', out);
