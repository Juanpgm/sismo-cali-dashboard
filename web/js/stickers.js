// Stickers view: manage @sismocali.gov.co field inspectors via /api/stickers.
// List, create (modal), and enable/disable accounts. A disabled inspector can
// no longer create evaluaciones (enforced by the Firestore rules' `activo` gate).
//
// Design note: the 3-digit brigade code is each inspector's field identity — it
// appears on the ATC-20 placard they post and inside every record id
// (76001-1-1030001). It's the recurring visual anchor of the roster.
import { escapeHtml } from './utils.js';

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

function view(inspectores) {
  const activos = inspectores.filter((i) => !i.disabled && i.activo).length;
  const off = inspectores.length - activos;
  const roster = inspectores.length
    ? `<ul class="sticker-list">${inspectores.map(rowHtml).join('')}</ul>`
    : `<p class="sticker-empty">Todavía no hay inspectores. Creá el primero con «Nuevo inspector».</p>`;

  return `
    <header class="sticker-head">
      <div>
        <h2 class="sticker-h2">Inspectores de campo</h2>
        <p class="sticker-lead">Quién puede registrar evaluaciones desde el formulario ATC-20.</p>
      </div>
      <button type="button" class="btn-primary sticker-new" id="sticker-new">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Nuevo inspector
      </button>
    </header>

    <div class="sticker-stats">
      <div class="sticker-stat"><span class="sticker-stat-num">${inspectores.length}</span><span class="sticker-stat-lbl">Total</span></div>
      <div class="sticker-stat is-on"><span class="sticker-stat-num">${activos}</span><span class="sticker-stat-lbl">Activos</span></div>
      <div class="sticker-stat is-off"><span class="sticker-stat-num">${off}</span><span class="sticker-stat-lbl">Inhabilitados</span></div>
    </div>

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
              ${field('codigo', 'Código de brigada *', 'inputmode="numeric" required maxlength="3" placeholder="004" autocomplete="off"')}
              ${field('nombre_completo', 'Nombre completo', 'placeholder="Andrés Torres" autocomplete="off"')}
              ${field('entidad', 'Entidad', 'placeholder="SGRED" autocomplete="off"')}
            </div>
            ${field('password', 'Contraseña *', 'type="text" required placeholder="mínimo 6 caracteres" autocomplete="off"')}
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

// initStickers(root, { getToken }) — renders the tab and wires its actions.
// Refetches from the API on each open so the roster is always current.
export function initStickers(root, { getToken }) {
  let busy = false;

  async function reload() {
    root.innerHTML = '<p class="sticker-loading">Cargando inspectores…</p>';
    try {
      const { inspectores } = await callApi(getToken, { action: 'list' });
      root.innerHTML = view(inspectores);
      wire();
    } catch (err) {
      root.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  function wire() {
    const modal = root.querySelector('#sticker-modal');
    const form = root.querySelector('#sticker-form');
    const errBox = root.querySelector('#sticker-error');
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

    root.querySelector('#sticker-new').addEventListener('click', openModal);
    modal.querySelectorAll('[data-modal-close]').forEach((el) => el.addEventListener('click', closeModal));
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy) return;
      showError('');
      const body = { action: 'create' };
      new FormData(form).forEach((v, k) => { body[k] = String(v).trim(); });
      busy = true;
      root.querySelector('#sticker-submit').disabled = true;
      try {
        await callApi(getToken, body);
        closeModal();
        await reload();
      } catch (err) {
        showError(err.message);
        root.querySelector('#sticker-submit').disabled = false;
      } finally {
        busy = false;
      }
    });

    root.querySelectorAll('.sticker-action').forEach((btn) => {
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

  reload();
}
