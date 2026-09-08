// Per-record PDF report (see openspec/changes/informe-pdf-registros).
// buildReportDocDefinition (Phase 1) stays pure: no fetch/DOM/network.
// gatherAssets/buildLocatorMap/loadPdfmake/generarInformePdf (Phase 2) are the
// I/O shell around it — browser-only (fetch, canvas, Image, FileReader), so
// they are covered by manual verification (tasks.md Phase 4) rather than
// node:assert tests.
import {
  DETAIL_GROUPS, labelForField, formatValue, barrioVeredaDisplay, downloadStamp,
  SURVEY_LAYER_URL, isFirmaAttachment, attachmentUrl, basemapTileUrl,
  labelForCode, addressDisplay, faseInspector,
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

/* ------------------------------------------------------------------ */
/* "Revisión candidato a demolición" report (Acciones tab, see           */
/* acciones-capa.js): form record from the ArcGIS review layer + the     */
/* joined EDE record. Reuses the same asset pipeline as the EDE report. */
/* ------------------------------------------------------------------ */

function fieldTable(rows) {
  const body = rows.filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!body.length) return [];
  return [{
    table: {
      widths: ['40%', '60%'],
      body: body.map(([k, v]) => [
        { text: k, style: 'fieldLabel' },
        { text: String(v), style: 'fieldValue' },
      ]),
    },
    layout: 'lightHorizontalLines',
    margin: [0, 0, 0, 10],
  }];
}

function formatFechaRegistro(ms) {
  if (!ms) return 'Sin fecha';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return 'Sin fecha';
  return d.toLocaleString('es-CO', { dateStyle: 'long', timeStyle: 'short' });
}

/** Pure builder for the candidato-a-demolición report, same style tokens as
 *  buildReportDocDefinition. `ede` may be null (registro sin cruce). */
export function buildCandidatoDocDefinition(form, ede, { photos, signatures, mapImage } = {}) {
  const content = [
    { text: 'Revisión candidato a demolición', style: 'title' },
    { text: `Objetivo: ${form?.objectivo_id_r ?? 'Sin referencia'}${ede ? ` · ObjectID EDE: ${ede.ObjectID}` : ' · Sin cruce EDE'}`, style: 'subtitle' },
    { text: `Fecha de generación: ${downloadStamp().legible}`, style: 'subtitle' },
    { text: DISCLAIMER, style: 'disclaimer', margin: [0, 4, 0, 12] },
    { text: 'Revisión de campo', style: 'sectionHeader' },
    ...fieldTable([
      ['Candidato a demolición', labelForCode(form?.candidato_demolicion)],
      ['Colapso', labelForCode(form?.colapso)],
      ['Requiere visita adicional', labelForCode(form?.visita)],
      ['Fecha de registro', formatFechaRegistro(form?.fecha_registro)],
      ['Justificación de patología', form?.justificacion_patologia],
      ['Justificación de reconfirmación', form?.justificacion_reconfirmacion],
    ]),
  ];
  if (ede) {
    content.push(
      { text: 'Contexto EDE', style: 'sectionHeader' },
      ...fieldTable([
        ['Dirección', addressDisplay(ede).primary],
        ['Barrio / vereda', barrioVeredaDisplay(ede)],
        ['Comuna / corregimiento', ede.comuna],
        ['Edificación', ede.nombre_edificacion],
        ['Profesional que realizó la evaluación', ede.nombre_evaluador],
        ['Criterio de habitabilidad', ede.criterio_habitabilidad ? labelForCode(ede.criterio_habitabilidad) : null],
        ['Nivel de daño', ede.nivel_dano ? labelForCode(ede.nivel_dano) : null],
      ]),
    );
  }
  content.push(
    ...buildImagesSection('Fotos', photos, ede ? 'Sin fotos en el survey.' : 'Sin cruce EDE: no hay fotos disponibles.'),
    ...buildImagesSection('Firmas', signatures, ede ? 'Sin firmas en el survey.' : 'Sin cruce EDE: no hay firmas disponibles.'),
    ...buildMapSection(mapImage),
  );
  return {
    content,
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

/** Same orchestration as generarInformePdf, over the joined pair: photos and
 *  locator map come from the EDE record (the review layer's own geometry is
 *  all (0,0) and carries no attachments worth embedding). */
export async function generarInformeCandidato(form, ede) {
  try {
    const [assets, mapImage, pdfMake] = await Promise.all([
      gatherAssets(ede?.ObjectID ?? null),
      buildLocatorMap(ede || {}),
      loadPdfmake(),
    ]);
    const def = buildCandidatoDocDefinition(form, ede, { photos: assets.photos, signatures: assets.signatures, mapImage });
    const ref = ede?.ObjectID ?? String(form?.objectivo_id_r ?? 'registro').replace(/[^\w-]+/g, '_');
    const filename = `revision_demolicion_${ref}_${downloadStamp().slug}.pdf`;
    pdfMake.createPdf(def).download(filename);
  } catch (err) {
    console.error('generarInformeCandidato: fallo la generación', err);
    throw err;
  }
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

/* ------------------------------------------------------------------ */
/* Evaluación ATC-20 report (Stickers tab, see evaluaciones.js): a       */
/* server-flattened evaluación record — real nested shape (descripcion/  */
/* inspector/coords/acciones_posteriores), NOT the ArcGIS record.x/y     */
/* shape buildReportDocDefinition assumes. Reuses the same asset         */
/* pipeline (readBlobAsDataURL/downscaleDataUrl/buildLocatorMap/         */
/* loadPdfmake) as the other two reports.                                */
/* ------------------------------------------------------------------ */

const EVAL_CLASE_LABELS = {
  INSPECCIONADA: 'inspeccionada',
  USO_RESTRINGIDO: 'uso restringido',
  INSEGURO: 'inseguro',
};

/** Same clasificación normalization evaluaciones.js's claseDe() applies —
 *  label text only (no color, unneeded in a PDF table row). Kept local
 *  rather than imported from evaluaciones.js to avoid a circular import
 *  (evaluaciones.js already imports generarInformeEvaluacion from here). */
function evalClaseLabel(clasificacion) {
  const raw = String(clasificacion || '').trim().toUpperCase().replace(/[\s-]+/g, '_');
  return EVAL_CLASE_LABELS[raw] || 'sin dato';
}

const EVAL_FASE_LABELS = { FASE_II: 'fase II', FASE_I: 'fase I' };

/** Same derivation as evaluaciones.js's faseDe(), label text only — see
 *  that module's FASES for the color-carrying counterpart used on screen. */
function evalFaseLabel(np) {
  return EVAL_FASE_LABELS[faseInspector(np)];
}

function formatFechaEval(iso) {
  if (!iso) return 'Sin fecha';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' });
}

const siNoEval = (v) => (v ? 'Sí' : 'No');

/**
 * Pure builder for the evaluación ATC-20 report, same style tokens as
 * buildReportDocDefinition/buildCandidatoDocDefinition. Sections mirror
 * evaluaciones.js's detailHtml groups verbatim (Edificación, Evaluación,
 * Inspector — including Fase + NP, Ubicación), then Fotos, then the map.
 * No Firmas section: ATC-20 evaluaciones carry no firma concept (detailHtml
 * has none either). `photos`/`mapImage` must already be resolved by the I/O
 * shell below (gatherEvalPhotos/buildLocatorMap) — this stays pure.
 */
export function buildEvaluacionDocDefinition(e, { photos, mapImage } = {}) {
  const desc = e?.descripcion || {};
  const insp = e?.inspector || {};
  const acc = e?.acciones_posteriores || {};
  const coords = e?.coords;

  const content = [
    { text: 'Informe de evaluación ATC-20', style: 'title' },
    { text: `Código de edificación: ${e?.codigo_edificacion || 'Sin código'}`, style: 'subtitle' },
    { text: `Fecha de generación: ${downloadStamp().legible}`, style: 'subtitle' },
    { text: DISCLAIMER, style: 'disclaimer', margin: [0, 4, 0, 12] },
    { text: 'Edificación', style: 'sectionHeader' },
    ...fieldTable([
      ['Nombre', desc.nombre || 'Sin dato'],
      ['Dirección', desc.direccion || 'Sin dato'],
      ['Área', e?.area_nombre || e?.area || 'Sin dato'],
      ['Municipio (DIVIPOLA)', e?.municipio || 'Sin dato'],
      ['Consecutivo', e?.consecutivo],
    ]),
    { text: 'Evaluación', style: 'sectionHeader' },
    ...fieldTable([
      ['Clasificación', evalClaseLabel(e?.clasificacion)],
      ['Alcance', e?.alcance || 'Sin dato'],
      ['Restricciones', e?.restricciones || 'Ninguna registrada'],
      ['Barricadas', siNoEval(acc.barricadas)],
      ['Evaluación detallada', siNoEval(acc.evaluacion_detallada)],
      ['Comentarios', e?.comentarios || 'Sin comentarios'],
    ]),
    { text: 'Inspector', style: 'sectionHeader' },
    ...fieldTable([
      ['Nombre', insp.nombre_completo || 'Sin dato'],
      ['Código de brigada', insp.codigo || 'Sin dato'],
      ['Identificación', insp.identificacion || 'Sin dato'],
      ['Entidad', insp.entidad || 'Sin dato'],
      ['Fase', evalFaseLabel(insp.np)],
      ['NP', insp.np || 'Sin dato'],
      ['Fecha de registro', formatFechaEval(e?.fecha)],
    ]),
    { text: 'Coordenadas', style: 'sectionHeader' },
    ...fieldTable([
      ['Latitud', coords ? coords.lat.toFixed(6) : 'Sin coordenadas'],
      ['Longitud', coords ? coords.lng.toFixed(6) : 'Sin coordenadas'],
      ['Precisión', coords && coords.accuracy ? `±${Math.round(coords.accuracy)} m` : 'Sin dato'],
    ]),
    ...buildImagesSection('Fotos', photos, 'Sin fotos.'),
    ...buildMapSection(mapImage), // emits its own 'Ubicación' header (the locator map)
  ];

  return {
    content,
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

/** Evaluación photos are already-hosted Firebase Storage URLs, NOT ArcGIS
 *  attachments — fetched directly instead of through gatherAssets'
 *  attachment-listing flow, reusing the same readBlobAsDataURL/
 *  downscaleDataUrl pipeline. Never throws: a bad URL degrades to
 *  {dataURL: null, sourceUrl}, same per-image contract as resolveAttachment,
 *  so one broken photo link can't abort the rest of the report. */
export async function gatherEvalPhotos(fotos) {
  const list = Array.isArray(fotos) ? fotos : [];
  return Promise.all(list.map(async (url) => {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const raw = await readBlobAsDataURL(await res.blob());
      const dataURL = await downscaleDataUrl(raw);
      return { dataURL, sourceUrl: url };
    } catch {
      return { dataURL: null, sourceUrl: url };
    }
  }));
}

/** Same orchestration shape as generarInformeCandidato/generarInformePdf:
 *  gather photos + the locator map + pdfmake in parallel, build the doc
 *  definition, trigger the download. */
export async function generarInformeEvaluacion(e) {
  try {
    const [photos, mapImage, pdfMake] = await Promise.all([
      gatherEvalPhotos(e?.fotos),
      buildLocatorMap({ x: e?.coords?.lng, y: e?.coords?.lat }),
      loadPdfmake(),
    ]);
    const def = buildEvaluacionDocDefinition(e, { photos, mapImage });
    const filename = `evaluacion_ATC20_${e?.codigo_edificacion || 'registro'}_${downloadStamp().slug}.pdf`;
    pdfMake.createPdf(def).download(filename);
  } catch (err) {
    console.error('generarInformeEvaluacion: fallo la generación', err);
    throw err;
  }
}
