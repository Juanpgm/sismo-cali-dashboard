// In-app carousel for a Google Drive folder's contents ("Ver fotos" on the
// Vuelos UAS tab). The folder listing comes from OUR OWN /api/drive-folder
// endpoint (api/drive-folder.js), never from googleapis.com directly — the
// Drive API key is a server-side secret (GOOGLE_DRIVE_API_KEY in Vercel),
// never shipped to the browser. See api/drive-folder.js for why.
//
// Once we have the file list, we render OUR OWN thumbnail strip + main
// viewer: images as plain <img>, video/other as Drive's single-file
// /preview iframe (that one does NOT have the big blank-space quirk the
// whole-folder `embeddedfolderview` grid has, and it plays video inline
// with native controls without us needing to stream bytes ourselves).
// If /api/drive-folder itself fails (network down, key not yet configured
// in Vercel, Drive API error), we fall back to the old embeddedfolderview
// iframe so the tab keeps working either way.
import { escapeHtml } from './utils.js';

/** Lists every non-folder file directly inside `folderId` via our server
 *  proxy. Throws on a non-OK response or a network failure — callers render
 *  the message and fall back to the plain "Abrir en Drive" link. */
export async function listDriveFolder(folderId) {
  const res = await fetch(`/api/drive-folder?id=${encodeURIComponent(folderId)}`);
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json?.error || `HTTP ${res.status}`);
  return json.files || [];
}

export const mimeKind = (mimeType) => {
  const m = String(mimeType || '');
  if (m.startsWith('image/')) return 'image';
  if (m.startsWith('video/')) return 'video';
  return 'other';
};

/** Drive's thumbnailLink comes back sized (e.g. "...=s220"); swap that
 *  suffix for the size we actually want. Returns null when the API omitted
 *  a thumbnail (a file still being virus-scanned, or an unsupported type)
 *  so callers can render a placeholder instead of a broken <img>. */
export function thumbSrc(file, size) {
  const link = file?.thumbnailLink;
  if (!link) return null;
  return /=s\d+$/.test(link) ? link.replace(/=s\d+$/, `=s${size}`) : `${link}=s${size}`;
}

/** Clamped, wrap-around index step — index 0 stepping -1 lands on the last
 *  item and vice versa; an empty/singleton list always stays at 0. */
export const wrapIndex = (index, delta, length) => (length ? (((index + delta) % length) + length) % length : 0);

function mainMediaHtml(file) {
  const name = escapeHtml(file.name || 'Sin nombre');
  const id = encodeURIComponent(file.id || '');
  if (mimeKind(file.mimeType) === 'image') {
    const src = thumbSrc(file, 1600);
    if (src) return `<img class="uas-carousel-img" src="${escapeHtml(src)}" alt="${name}">`;
  }
  return `<iframe class="uas-carousel-embed" src="https://drive.google.com/file/d/${id}/preview" title="${name}" allow="autoplay" allowfullscreen></iframe>`;
}

function thumbHtml(file, i, active) {
  const src = thumbSrc(file, 160);
  const isVideo = mimeKind(file.mimeType) === 'video';
  return `
    <button type="button" class="uas-carousel-thumb${active ? ' is-active' : ''}" data-uas-carousel-index="${i}" title="${escapeHtml(file.name || '')}" aria-current="${active}">
      ${src ? `<img src="${escapeHtml(src)}" alt="" loading="lazy">` : `<span class="uas-carousel-thumb-fallback">${isVideo ? '▶' : '···'}</span>`}
      ${isVideo ? '<span class="uas-carousel-thumb-badge" aria-hidden="true">▶</span>' : ''}
    </button>`;
}

function carouselShellHtml(files) {
  const multi = files.length > 1;
  return `
    <div class="uas-carousel">
      <div class="uas-carousel-main">
        <button type="button" class="uas-carousel-nav uas-carousel-prev" data-uas-carousel-prev aria-label="Anterior" ${multi ? '' : 'disabled'}>&#8249;</button>
        <div class="uas-carousel-frame-wrap" data-uas-carousel-frame></div>
        <button type="button" class="uas-carousel-nav uas-carousel-next" data-uas-carousel-next aria-label="Siguiente" ${multi ? '' : 'disabled'}>&#8250;</button>
      </div>
      <div class="uas-carousel-footer">
        <span class="uas-carousel-count" data-uas-carousel-count></span>
        <span class="uas-carousel-name" data-uas-carousel-name></span>
      </div>
      <div class="uas-carousel-strip" data-uas-carousel-strip>${files.map((f, i) => thumbHtml(f, i, i === 0)).join('')}</div>
    </div>`;
}

function fallbackFrameHtml(folderId) {
  return `<iframe class="uas-fotos-frame" src="https://drive.google.com/embeddedfolderview?id=${encodeURIComponent(folderId)}#grid" title="Fotos del vuelo en Google Drive"></iframe>`;
}

function errorHtml(folderId, message) {
  return `
    <p class="sticker-error" role="alert">No se pudo cargar el carrusel (${escapeHtml(message)}). Mostrando la vista de Drive:</p>
    ${fallbackFrameHtml(folderId)}`;
}

function emptyHtml(folderId) {
  return `
    <p class="sticker-empty">Esta carpeta no tiene archivos todavía.</p>
    <a class="sticker-action" href="https://drive.google.com/drive/folders/${encodeURIComponent(folderId)}" target="_blank" rel="noopener">Abrir en Drive</a>`;
}

/** Mounts the carousel (or its fallback/error/empty state) into `bodyEl`.
 *  Returns `{ destroy }` — callers MUST call it on close so the keydown
 *  listener doesn't pile up across repeated opens. */
export async function mountDriveCarousel(bodyEl, folderId) {
  bodyEl.innerHTML = '<p class="sticker-loading">Cargando…</p>';

  let files;
  try {
    files = await listDriveFolder(folderId);
  } catch (err) {
    bodyEl.innerHTML = errorHtml(folderId, err.message);
    return { destroy() {} };
  }

  if (!files.length) {
    bodyEl.innerHTML = emptyHtml(folderId);
    return { destroy() {} };
  }

  bodyEl.innerHTML = carouselShellHtml(files);
  let index = 0;
  const paint = () => {
    const f = files[index];
    bodyEl.querySelector('[data-uas-carousel-frame]').innerHTML = mainMediaHtml(f);
    bodyEl.querySelector('[data-uas-carousel-count]').textContent = `${index + 1} / ${files.length}`;
    bodyEl.querySelector('[data-uas-carousel-name]').textContent = f.name || '';
    bodyEl.querySelectorAll('[data-uas-carousel-index]').forEach((btn) => {
      const active = Number(btn.dataset.uasCarouselIndex) === index;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-current', String(active));
    });
    bodyEl.querySelector(`[data-uas-carousel-index="${index}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'center' });
  };
  const go = (delta) => { index = wrapIndex(index, delta, files.length); paint(); };
  paint();

  const onClick = (e) => {
    if (e.target.closest('[data-uas-carousel-prev]')) return go(-1);
    if (e.target.closest('[data-uas-carousel-next]')) return go(1);
    const thumb = e.target.closest('[data-uas-carousel-index]');
    if (thumb) { index = Number(thumb.dataset.uasCarouselIndex); paint(); }
  };
  bodyEl.addEventListener('click', onClick);

  // Left/right steer the carousel while its modal is open; scoped to the
  // document (the modal traps focus visually but not via a dialog element),
  // so it's removed on destroy() to avoid leaking into the rest of the app.
  const onKeydown = (e) => {
    if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === 'ArrowRight') go(1);
  };
  document.addEventListener('keydown', onKeydown);

  return {
    destroy() {
      bodyEl.removeEventListener('click', onClick);
      document.removeEventListener('keydown', onKeydown);
    },
  };
}
