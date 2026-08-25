// Stickers view: the field operation in one screen.
//
// Two sections over the same /api/stickers endpoint:
//   · Evaluaciones ATC-20 — KPIs, map and detail of what the brigades recorded
//     (see evaluaciones.js).
//   · Inspectores de campo — list, create (modal), enable/disable accounts. A
//     disabled inspector can no longer create evaluaciones (enforced by the
//     Firestore rules' `activo` gate).
//
// Design note: the 3-digit brigade code is each inspector's field identity — it
// appears on the ATC-20 placard they post and inside every record id
// (76001-1-1030001). It's the recurring visual anchor of the roster. The admin
// no longer types it: the server allocates the lowest free code on create.
import { escapeHtml } from './utils.js';
import { sectionHtml as evalSectionHtml, initEvaluaciones } from './evaluaciones.js';
import { initStickersAsignacion } from './stickers-asignacion.js';

const ENDPOINT = '/api/stickers';

async function callApi(getToken, body) {
  const token = await getToken();
  if (!token) throw new Error('Sesión no válida. Volvé a iniciar sesión.');
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

const initials = (name) => (name || '').trim().split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase() || '—';

function rowHtml(i) {
  const activo = !i.disabled && i.activo;
  const estado = activo
    ? '<span class="sticker-pill sticker-pill-on">Activo</span>'
    : '<span class="sticker-pill sticker-pill-off">Inhabilitado</span>';
  const toggle = activo
    ? `<button type="button" class="sticker-action sticker-action-off" data-uid="${escapeHtml(i.uid)}" data-enable="false">Inhabilitar</button>`
    : `<button type="button" class="sticker-action sticker-action-on" data-uid="${escapeHtml(i.uid)}" data-enable="true">Habilitar</button>`;
  const warn = i.registrado ? '' : '<span class="sticker-warn" title="Sin perfil en Firestore">sin perfil</span>';
  const n = Number.isFinite(i.registros) ? i.registros : null;
  const registros = n === null ? '— registros' : `${n} registro${n === 1 ? '' : 's'}`;
  const meta = [i.entidad, registros].filter(Boolean).join(' · ');
  return `<li class="sticker-row${activo ? '' : ' is-off'}">
    <span class="sticker-code" title="Código de brigada">${escapeHtml(i.codigo || '—')}</span>
    <span class="sticker-avatar" aria-hidden="true">${escapeHtml(initials(i.nombre_completo))}</span>
    <div class="sticker-identity">
      <span class="sticker-name">${escapeHtml(i.nombre_completo || '—')} ${warn}</span>
      <span class="sticker-meta">${escapeHtml(i.cedula)}${meta ? ` · ${escapeHtml(meta)}` : ''}</span>
    </div>
    ${estado}
    ${toggle}
  </li>`;
}

const field = (name, label, attrs = '') => `<label class="sticker-field">
    <span>${label}</span>
    <input name="${name}" ${attrs}>
  </label>`;

// Case/accent-insensitive match across the identifying fields an operator is
// likely to type: name, cédula, brigade code, entidad. Pure — no DOM.
function normalizeSearch(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}
function filterInspectores(inspectores, query) {
  const q = normalizeSearch(query).trim();
  if (!q) return inspectores;
  return inspectores.filter((i) => {
    const hay = normalizeSearch([i.nombre_completo, i.cedula, i.codigo, i.entidad].filter(Boolean).join(' '));
    return hay.includes(q);
  });
}

// Just the list — re-rendered on every keystroke of the search box, so it
// lives apart from the search input (whose focus must survive a filter).
function rosterListHtml(inspectores, filtered) {
  if (!inspectores.length) {
    return '<p class="sticker-empty">Todavía no hay inspectores. Creá el primero con «Nuevo inspector».</p>';
  }
  if (!filtered.length) {
    return '<p class="sticker-empty">Ningún inspector coincide con la búsqueda.</p>';
  }
  return `<ul class="sticker-list">${filtered.map(rowHtml).join('')}</ul>`;
}

function rosterHtml(inspectores) {
  const activos = inspectores.filter((i) => !i.disabled && i.activo).length;
  const off = inspectores.length - activos;
  const search = inspectores.length
    ? `<div class="sticker-search">
        <input type="search" id="sticker-search" class="sticker-search-input"
          placeholder="Buscar por nombre, cédula, código o entidad…"
          aria-label="Buscar inspectores" autocomplete="off">
      </div>`
    : '';
  const roster = `<div id="sticker-roster-list">${rosterListHtml(inspectores, inspectores)}</div>`;

  // Counters ride in the section bar as inline chips instead of a second row
  // of stat cards: three numbers do not need three cards' worth of height.
  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Inspectores de campo</h3>
      <div class="sticker-chips" aria-label="Resumen de inspectores">
        <span class="sticker-chip">${inspectores.length} total</span>
        <span class="sticker-chip is-on">${activos} activos</span>
        <span class="sticker-chip is-off">${off} inhabilitados</span>
      </div>
      <button type="button" class="btn-primary sticker-new" id="sticker-new">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Nuevo inspector
      </button>
    </div>

    <p class="sticker-ok" id="sticker-ok" role="status" hidden></p>

    ${search}
    ${roster}

    <div class="modal" id="sticker-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="sticker-modal-title">
      <div class="modal-backdrop" data-modal-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="sticker-modal-title">Nuevo inspector</h2>
          <button type="button" class="btn-icon" data-modal-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <form id="sticker-form" class="sticker-form" novalidate>
            <div class="sticker-form-grid">
              ${field('cedula', 'Cédula *', 'inputmode="numeric" required placeholder="1020735324" autocomplete="off"')}
              ${field('nombre_completo', 'Nombre completo', 'placeholder="Andrés Torres" autocomplete="off"')}
              ${field('entidad', 'Entidad', 'placeholder="SGRED" autocomplete="off"')}
              ${field('password', 'Contraseña *', 'type="text" required placeholder="mínimo 6 caracteres" autocomplete="off"')}
            </div>
            <p class="sticker-note">El código de brigada se asigna solo: el servidor toma el número libre más bajo (001, 002, …) y nunca reutiliza uno ya entregado.</p>
            <p class="sticker-error" id="sticker-error" role="alert" hidden></p>
            <div class="sticker-form-actions">
              <button type="button" class="btn-secondary" data-modal-close>Cancelar</button>
              <button type="submit" class="btn-primary" id="sticker-submit">Crear inspector</button>
            </div>
          </form>
        </div>
      </div>
    </div>`;
}

// Rendered once per tab open. The roster reloads into #sticker-roster on its
// own; keeping each section behind its own wrapper means switching segments
// doesn't tear down the map (evaluaciones) or refetch Firestore (asignación).
//
// Three-way segmented control (Roster · Evaluaciones · Asignación) — the
// first sub-nav pattern inside #view-stickers (0.1's finding: none existed
// before this change, so nothing to extend).
function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Operación de campo</h2>
      <p class="sticker-lead">Lo que registran las brigadas y quién puede registrarlo.</p>
    </header>

    <div class="asignacion-segmented" role="tablist" aria-label="Sección de Stickers">
      <button type="button" class="asignacion-segment is-active" data-sticker-segment="roster" role="tab" aria-selected="true">Inspectores</button>
      <button type="button" class="asignacion-segment" data-sticker-segment="evaluaciones" role="tab" aria-selected="false">Evaluaciones</button>
      <button type="button" class="asignacion-segment" data-sticker-segment="asignacion" role="tab" aria-selected="false">Asignación</button>
    </div>

    <div data-sticker-section="roster">
      <section class="sticker-roster" id="sticker-roster"></section>
    </div>
    <div data-sticker-section="evaluaciones" hidden>${evalSectionHtml()}</div>
    <div data-sticker-section="asignacion" hidden></div>`;
}

// initStickers(root, { getToken }) — renders the tab and wires its actions.
// Refetches from the API on each open so both sections are always current.
export function initStickers(root, { getToken }) {
  let busy = false;
  // Survives a roster reload so the "brigada 007 asignada" confirmation is
  // still on screen after the list comes back with the new inspector on it.
  let assignedNotice = '';
  // Last roster response: the Asignación sub-section's inspector <select>s
  // read this instead of issuing their own `list` call (spec.md "Inspector
  // dropdown reuses the existing roster").
  let inspectoresCache = [];

  root.innerHTML = shellHtml();
  const rosterRoot = root.querySelector('#sticker-roster');
  const segmentButtons = root.querySelectorAll('[data-sticker-segment]');
  const sections = {
    roster: root.querySelector('[data-sticker-section="roster"]'),
    evaluaciones: root.querySelector('[data-sticker-section="evaluaciones"]'),
    asignacion: root.querySelector('[data-sticker-section="asignacion"]'),
  };
  // Set on first "Asignación" open; subsequent opens call .reload() instead
  // of re-initializing (spec.md "Init runs once on first Asignación open").
  let asignacionHandle = null;

  const evaluacionesHandle = initEvaluaciones(root.querySelector('.eval-section'), {
    fetchEvaluaciones: async () => (await callApi(getToken, { action: 'evaluaciones' })).evaluaciones,
  });

  function showSegment(name) {
    for (const [key, el] of Object.entries(sections)) el.hidden = key !== name;
    segmentButtons.forEach((btn) => {
      const active = btn.dataset.stickerSegment === name;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    // Leaflet renders broken tiles when its container was hidden at build time;
    // re-measure once the segment is actually visible.
    if (name === 'evaluaciones' && evaluacionesHandle) {
      setTimeout(() => evaluacionesHandle.invalidate(), 60);
    }
    if (name !== 'asignacion') return;
    if (asignacionHandle) {
      asignacionHandle.reload();
    } else {
      asignacionHandle = initStickersAsignacion(sections.asignacion, {
        getToken,
        getInspectores: () => inspectoresCache,
      });
    }
  }
  segmentButtons.forEach((btn) => btn.addEventListener('click', () => showSegment(btn.dataset.stickerSegment)));

  async function reload() {
    rosterRoot.innerHTML = '<p class="sticker-loading">Cargando inspectores…</p>';
    try {
      const { inspectores } = await callApi(getToken, { action: 'list' });
      inspectoresCache = inspectores;
      rosterRoot.innerHTML = rosterHtml(inspectores);
      wire();
      if (assignedNotice) {
        const ok = rosterRoot.querySelector('#sticker-ok');
        ok.textContent = assignedNotice;
        ok.hidden = false;
        assignedNotice = '';
      }
    } catch (err) {
      rosterRoot.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  function wire() {
    const modal = rosterRoot.querySelector('#sticker-modal');
    const form = rosterRoot.querySelector('#sticker-form');
    const errBox = rosterRoot.querySelector('#sticker-error');
    const showError = (msg) => { errBox.textContent = msg; errBox.hidden = !msg; };

    const openModal = () => {
      showError('');
      form.reset();
      modal.classList.add('is-open');
      modal.setAttribute('aria-hidden', 'false');
      form.querySelector('[name="cedula"]').focus();
    };
    const closeModal = () => {
      modal.classList.remove('is-open');
      modal.setAttribute('aria-hidden', 'true');
    };

    rosterRoot.querySelector('#sticker-new').addEventListener('click', openModal);
    modal.querySelectorAll('[data-modal-close]').forEach((el) => el.addEventListener('click', closeModal));
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy) return;
      showError('');
      const body = { action: 'create' };
      new FormData(form).forEach((v, k) => { body[k] = String(v).trim(); });
      busy = true;
      rosterRoot.querySelector('#sticker-submit').disabled = true;
      try {
        // The server allocates the brigade code, so the admin only learns it
        // from the response — surface it instead of silently closing.
        const { codigo } = await callApi(getToken, body);
        assignedNotice = `Inspector creado. Código de brigada asignado: ${codigo}.`;
        closeModal();
        await reload();
      } catch (err) {
        showError(err.message);
        rosterRoot.querySelector('#sticker-submit').disabled = false;
      } finally {
        busy = false;
      }
    });

    // Scoped to the roster rows: the evaluaciones section reuses .sticker-action
    // for its own buttons, which must not get the enable/disable handler.
    // Re-run after every search filter, since the list is re-rendered then.
    function wireRows() {
      rosterRoot.querySelectorAll('#sticker-roster-list .sticker-row .sticker-action[data-uid]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          if (busy) return;
          busy = true;
          btn.disabled = true;
          try {
            await callApi(getToken, { action: 'setEnabled', uid: btn.dataset.uid, enabled: btn.dataset.enable === 'true' });
            await reload();
          } catch (err) {
            busy = false;
            btn.disabled = false;
            alert(err.message); // rare path (network/permission); surface it plainly
          }
        });
      });
    }
    wireRows();

    // Client-side search over the already-loaded roster: filter and re-render
    // only the list container so the search input keeps focus between keystrokes.
    const searchInput = rosterRoot.querySelector('#sticker-search');
    const listEl = rosterRoot.querySelector('#sticker-roster-list');
    if (searchInput && listEl) {
      searchInput.addEventListener('input', () => {
        const filtered = filterInspectores(inspectoresCache, searchInput.value);
        listEl.innerHTML = rosterListHtml(inspectoresCache, filtered);
        wireRows();
      });
    }
  }

  reload();
}
