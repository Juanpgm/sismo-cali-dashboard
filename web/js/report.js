// Per-record PDF report (see openspec/changes/informe-pdf-registros).
// buildReportDocDefinition (Phase 1) stays pure: no fetch/DOM/network.
// gatherAssets/buildLocatorMap/loadPdfmake/generarInformePdf (Phase 2) are the
// I/O shell around it — browser-only (fetch, canvas, Image, FileReader), so
// they are covered by manual verification (tasks.md Phase 4) rather than
// node:assert tests.
import {
  DETAIL_GROUPS, labelForField, formatValue, barrioVeredaDisplay, downloadStamp,
  SURVEY_LAYER_URL, isFirmaAttachment, attachmentUrl, basemapTileUrl,
} from './utils.js';

export const MAX_PHOTOS = 12;

const DISCLAIMER = 'Informe generado automáticamente a partir de los registros del EDE. '
  + 'No sustituye la evaluación técnica original ni constituye un documento oficial certificado.';

/** {dataURL, sourceUrl} -> pdfmake image node, or a "no disponible" placeholder
 *  with a link back to the source when dataURL is missing. */
function imageOrPlaceholder(asset, { width } = {}) {
  if (asset && asset.dataURL) {
    return width ? { image: asset.dataURL, width } : { image: asset.dataURL, fit: [220, 220] };
  }
  const sourceUrl = asset && asset.sourceUrl;
  return {
    stack: [
      { text: 'Imagen no disponible', italics: true, color: '#888', margin: [0, 0, 0, 2] },
      ...(sourceUrl ? [{ text: sourceUrl, link: sourceUrl, color: '#2a5db0', fontSize: 8 }] : []),
    ],
    margin: [0, 0, 0, 8],
  };
}

function buildHeader(record) {
  const codigo = record?.codigo || record?.ObjectID || 'Sin código';
  return [
    { text: 'Informe de inspección EDE', style: 'title' },
    { text: `Código / ObjectID: ${codigo}`, style: 'subtitle' },
    { text: `Fecha de generación: ${downloadStamp().legible}`, style: 'subtitle' },
    { text: DISCLAIMER, style: 'disclaimer', margin: [0, 4, 0, 12] },
  ];
}

/** One section per DETAIL_GROUPS entry that has at least one populated field
 *  in `record`, mirroring the on-screen detail modal exactly (empty fields —
 *  and thus empty groups — are omitted rather than shown as "Sin dato"). */
function buildFieldSections(record) {
  const sections = [];
  for (const [group, fields] of Object.entries(DETAIL_GROUPS)) {
    const populated = fields.filter((f) => {
      const v = record?.[f];
      return v !== null && v !== undefined && v !== '';
    });
    if (!populated.length) continue;
    sections.push({ text: group, style: 'sectionHeader' });
    sections.push({
      table: {
        widths: ['40%', '60%'],
        body: populated.map((f) => [
          { text: labelForField(f), style: 'fieldLabel' },
          { text: f === 'barrio_vereda_resuelto' ? barrioVeredaDisplay(record) : formatValue(f, record[f]), style: 'fieldValue' },
        ]),
      },
      layout: 'lightHorizontalLines',
      margin: [0, 0, 0, 10],
    });
  }
  return sections;
}

function buildImagesSection(title, assets, emptyText) {
  const list = Array.isArray(assets) ? assets : [];
  const section = [{ text: title, style: 'sectionHeader' }];
  if (!list.length) {
    section.push({ text: emptyText, italics: true, color: '#888', margin: [0, 0, 0, 10] });
    return section;
  }
  const shown = list.slice(0, MAX_PHOTOS);
  const overflow = list.length - shown.length;
  section.push({ columns: shown.map((a) => imageOrPlaceholder(a)), columnGap: 8, margin: [0, 0, 0, 4] });
  if (overflow > 0) {
    section.push({ text: `${overflow} fotos adicionales no incluidas`, italics: true, color: '#888', margin: [0, 0, 0, 10] });
  } else {
    section.push({ text: '', margin: [0, 0, 0, 10] });
  }
  return section;
}

function buildMapSection(mapImage) {
  const asset = mapImage && mapImage.dataURL
    ? { dataURL: mapImage.dataURL, sourceUrl: mapImage.sourceUrl }
    : { dataURL: null, sourceUrl: mapImage && mapImage.sourceUrl };
  return [
    { text: 'Ubicación', style: 'sectionHeader' },
    imageOrPlaceholder(asset, { width: 300 }),
  ];
}

/**
 * Pure builder: record + resolved assets -> pdfmake document definition.
 * No I/O — `photos`/`signatures`/`mapImage` must already be resolved
 * (dataURL or null+sourceUrl) by the Phase 2 asset-gathering step.
 */
export function buildReportDocDefinition(record, { photos, signatures, mapImage } = {}) {
  return {
    content: [
      ...buildHeader(record),
      ...buildFieldSections(record),
      ...buildImagesSection('Fotos', photos, 'Sin fotos en el survey.'),
      ...buildImagesSection('Firmas', signatures, 'Sin firmas en el survey.'),
      ...buildMapSection(mapImage),
    ],
    styles: {
      title: { fontSize: 16, bold: true },
      subtitle: { fontSize: 9, color: '#555' },
      disclaimer: { fontSize: 8, italics: true, color: '#777' },
      sectionHeader: { fontSize: 12, bold: true, margin: [0, 10, 0, 4] },
      fieldLabel: { fontSize: 9, bold: true },
      fieldValue: { fontSize: 9 },
    },
    defaultStyle: { fontSize: 9 },
  };
}

/* ------------------------------------------------------------------ */
/* Phase 2: asset gathering (photos/firmas), downscale, locator map,    */
/* pdfmake lazy-load, and the generarInformePdf orchestrator.           */
/* ------------------------------------------------------------------ */

function readBlobAsDataURL(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error('No se pudo leer el archivo'));
    reader.readAsDataURL(blob);
  });
}

function loadImageEl(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('No se pudo decodificar la imagen'));
    img.src = src;
  });
}

const DOWNSCALE_MAX_WIDTH = 1000;
const DOWNSCALE_JPEG_QUALITY = 0.7;

/** Cap embedded images to a sane size/weight before they go into the PDF —
 *  raw phone photos can be several MB each and there can be up to
 *  MAX_PHOTOS of them. Downscale by width only (aspect ratio preserved);
 *  images already under the cap pass through untouched. */
export async function downscaleDataUrl(dataURL) {
  const img = await loadImageEl(dataURL);
  if (!img.naturalWidth || img.naturalWidth <= DOWNSCALE_MAX_WIDTH) return dataURL;
  const scale = DOWNSCALE_MAX_WIDTH / img.naturalWidth;
  const canvas = document.createElement('canvas');
  canvas.width = DOWNSCALE_MAX_WIDTH;
  canvas.height = Math.round(img.naturalHeight * scale);
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', DOWNSCALE_JPEG_QUALITY);
}

/** Resolve one ArcGIS attachment to an embeddable dataURL. Never throws —
 *  any failure (network, decode) degrades to {dataURL: null, sourceUrl} so
 *  one bad photo can't abort the rest of the report (per-image degradation). */
async function resolveAttachment(objectId, info) {
  const sourceUrl = attachmentUrl(objectId, info.id);
  try {
    const res = await fetch(sourceUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const raw = await readBlobAsDataURL(await res.blob());
    const dataURL = await downscaleDataUrl(raw);
    return { dataURL, sourceUrl };
  } catch {
    return { dataURL: null, sourceUrl };
  }
}

/** Fetch a record's attachments and split/resolve them into photos vs
 *  firma* signatures, each already downscaled to an embeddable dataURL (or
 *  a null-dataURL placeholder on failure). A total fetch failure (e.g. the
 *  attachments listing itself 404s) degrades to empty lists rather than
 *  throwing — the record's fields still deserve a PDF. */
export async function gatherAssets(objectId) {
  if (objectId == null) return { photos: [], signatures: [] };
  let infos = [];
  try {
    const res = await fetch(`${SURVEY_LAYER_URL}/${objectId}/attachments?f=json`);
    const json = await res.json();
    infos = (json.attachmentInfos || []).filter((a) => (a.contentType || '').startsWith('image'));
  } catch {
    return { photos: [], signatures: [] };
  }
  const [photos, signatures] = await Promise.all([
    Promise.all(infos.filter((a) => !isFirmaAttachment(a.name)).map((a) => resolveAttachment(objectId, a))),
    Promise.all(infos.filter((a) => isFirmaAttachment(a.name)).map((a) => resolveAttachment(objectId, a))),
  ]);
  return { photos, signatures };
}

const TILE_SIZE = 256;
const MAP_ZOOM = 15;
const MAP_GRID = 3; // 3x3 tiles, marker on the center one
const MAP_SUBDOMAINS = 'abcd';
const MARKER_COLOR = '#e63946';

// Standard slippy-map projection (Web Mercator), fractional so we can place
// the marker at its exact pixel offset within the center tile.
function lonLatToTilePoint(lon, lat, zoom) {
  const n = 2 ** zoom;
  const x = ((lon + 180) / 360) * n;
  const latRad = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return { x, y };
}

function loadTileImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous'; // required for a clean (untainted) canvas read
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`No se pudo cargar el tile ${url}`));
    img.src = url;
  });
}

function osmLink(lat, lon) {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=${MAP_ZOOM}/${lat}/${lon}`;
}

/** Compose a small locator map for the record's coordinates: a 3x3 CARTO
 *  tile grid on an offscreen canvas plus a marker, exported as a dataURL.
 *  No Leaflet dependency (Leaflet needs a live DOM container, not an
 *  offscreen export) — same tile source (basemapTileUrl) as the on-screen
 *  mini-map, so the report stays visually consistent with the modal.
 *  Degrades to {dataURL: null} on missing/invalid coords or any tile
 *  fetch failure. */
export async function buildLocatorMap(record) {
  const lon = Number(record?.x);
  const lat = Number(record?.y);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return { dataURL: null, sourceUrl: null };
  const sourceUrl = osmLink(lat, lon);
  try {
    const { x, y } = lonLatToTilePoint(lon, lat, MAP_ZOOM);
    const tileX = Math.floor(x);
    const tileY = Math.floor(y);
    const template = basemapTileUrl();
    const offset = Math.floor(MAP_GRID / 2);
    const tiles = [];
    for (let dy = -offset; dy <= offset; dy += 1) {
      for (let dx = -offset; dx <= offset; dx += 1) tiles.push({ dx, dy, tx: tileX + dx, ty: tileY + dy });
    }
    const images = await Promise.all(tiles.map(({ dx, dy, tx, ty }) => {
      const s = MAP_SUBDOMAINS[Math.abs(tx + ty) % MAP_SUBDOMAINS.length];
      const url = template.replace('{s}', s).replace('{z}', MAP_ZOOM).replace('{x}', tx).replace('{y}', ty).replace('{r}', '');
      return loadTileImage(url).then((img) => ({ img, dx, dy }));
    }));
    const canvas = document.createElement('canvas');
    canvas.width = TILE_SIZE * MAP_GRID;
    canvas.height = TILE_SIZE * MAP_GRID;
    const ctx = canvas.getContext('2d');
    for (const { img, dx, dy } of images) ctx.drawImage(img, (dx + offset) * TILE_SIZE, (dy + offset) * TILE_SIZE);
    const markerX = (x - tileX + offset) * TILE_SIZE;
    const markerY = (y - tileY + offset) * TILE_SIZE;
    ctx.beginPath();
    ctx.arc(markerX, markerY, 8, 0, Math.PI * 2);
    ctx.fillStyle = MARKER_COLOR;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#ffffff';
    ctx.stroke();
    return { dataURL: canvas.toDataURL('image/png'), sourceUrl };
  } catch {
    return { dataURL: null, sourceUrl };
  }
}

// pdfmake (~450KB incl. vfs_fonts) is only needed once a report is actually
// generated — load it on first call, mirroring loadXlsx() (utils.js).
let pdfmakePromise = null;
export function loadPdfmake() {
  if (!pdfmakePromise) {
    const base = 'https://cdn.jsdelivr.net/npm/pdfmake@0.2.20/build/';
    pdfmakePromise = new Promise((resolve, reject) => {
      const core = document.createElement('script');
      core.src = `${base}pdfmake.min.js`;
      core.onload = () => {
        const fonts = document.createElement('script');
        fonts.src = `${base}vfs_fonts.js`;
        fonts.onload = () => resolve(window.pdfMake);
        fonts.onerror = () => reject(new Error('No se pudo cargar pdfmake (vfs_fonts)'));
        document.head.appendChild(fonts);
      };
      core.onerror = () => reject(new Error('No se pudo cargar pdfmake'));
      document.head.appendChild(core);
    });
  }
  return pdfmakePromise;
}

/** Orchestrator: gather assets + build the doc definition + lazy-load
 *  pdfmake + trigger the download. Per-image failures are already absorbed
 *  by gatherAssets/buildLocatorMap (placeholder, never throw); this
 *  try/catch only guards genuine whole-report failures (e.g. pdfmake
 *  failing to load), so a caught error here means no file — partial or
 *  otherwise — was downloaded. */
export async function generarInformePdf(record) {
  try {
    const objectId = record?.ObjectID;
    const [assets, mapImage, pdfMake] = await Promise.all([
      gatherAssets(objectId),
      buildLocatorMap(record),
      loadPdfmake(),
    ]);
    const def = buildReportDocDefinition(record, { photos: assets.photos, signatures: assets.signatures, mapImage });
    const codigo = record?.codigo || record?.ObjectID || 'registro';
    const filename = `informe_EDE_${codigo}_${downloadStamp().slug}.pdf`;
    pdfMake.createPdf(def).download(filename);
  } catch (err) {
    console.error('generarInformePdf: fallo la generación', err);
    throw err;
  }
}
