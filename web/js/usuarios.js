// Usuarios view: superset account management across the three real
// populations (password admins, google.com @cali.gov.co viewers,
// @sismocali.gov.co inspectors) over the new /api/usuarios endpoint.
//
// Mirrors web/js/stickers.js's shape (callApi, chip-in-section-bar pattern,
// lazy init on first tab open, full re-render on data change). Net-new here:
// the role/status filter, the delete action, and password reset via the
// Firebase client SDK directly (no API hop — Firebase's hosted email +
// action URL). See design.md ADR-5/ADR-6.
import { getAuth, sendPasswordResetEmail } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js';
import { escapeHtml } from './utils.js';
import { getFirebaseApp } from './firebase-config.js';

const ENDPOINT = '/api/usuarios';

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

const ROLE_LABEL = { admin: 'Admin', viewer: 'Viewer', inspector: 'Inspector', otro: 'Otro' };
const initials = (email) => (email || '').trim().slice(0, 2).toUpperCase() || '—';
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' }) : '—');

const field = (name, label, attrs = '') => `<label class="sticker-field">
    <span>${label}</span>
    <input name="${name}" ${attrs}>
  </label>`;

function rowHtml(u, ownUid) {
  const activo = !u.disabled;
  const isSelf = u.uid === ownUid;
  const estado = activo
    ? '<span class="sticker-pill sticker-pill-on">Activo</span>'
    : '<span class="sticker-pill sticker-pill-off">Inhabilitado</span>';
  // Hiding disable/delete on the caller's own row is a UI courtesy only —
  // the server's self-management guard (design.md ADR-3) is the real
  // boundary and rejects these actions regardless of what the UI shows.
  const toggle = isSelf ? '' : (activo
    ? `<button type="button" class="sticker-action sticker-action-off" data-uid="${escapeHtml(u.uid)}" data-enable="false">Inhabilitar</button>`
    : `<button type="button" class="sticker-action sticker-action-on" data-uid="${escapeHtml(u.uid)}" data-enable="true">Habilitar</button>`);
  const del = isSelf ? '' : `<button type="button" class="sticker-action" data-uid="${escapeHtml(u.uid)}" data-delete="true">Eliminar</button>`;
  const reset = `<button type="button" class="sticker-action" data-email="${escapeHtml(u.email)}" data-reset="true">Resetear contraseña</button>`;
  const meta = `${ROLE_LABEL[u.role] || u.role} · último acceso: ${fmtDate(u.lastSignInTime)} · alta: ${fmtDate(u.creationTime)}`;
  return `<li class="sticker-row usuario-row${activo ? '' : ' is-off'}">
    <span class="sticker-avatar" aria-hidden="true">${escapeHtml(initials(u.email))}</span>
    <div class="sticker-identity">
      <span class="sticker-name">${escapeHtml(u.email || '—')}${isSelf ? ' <span class="sticker-warn" title="Tu propia cuenta">vos</span>' : ''}</span>
      <span class="sticker-meta">${escapeHtml(meta)}</span>
    </div>
    ${estado}
    <div class="usuario-actions">${reset}${toggle}${del}</div>
  </li>`;
}

// Chips ride the section bar and always reflect the UNFILTERED superset
// (spec: "In-tab stat chips" — filtering must never hide the totals).
function chipsHtml(usuarios) {
  const activos = usuarios.filter((u) => !u.disabled).length;
  const off = usuarios.length - activos;
  const counts = usuarios.reduce((acc, u) => { acc[u.role] = (acc[u.role] || 0) + 1; return acc; }, {});
  return `<div class="sticker-chips" aria-label="Resumen de usuarios">
      <span class="sticker-chip">${usuarios.length} total</span>
      <span class="sticker-chip is-on">${activos} activos</span>
      <span class="sticker-chip is-off">${off} inhabilitados</span>
      <span class="sticker-chip">${counts.admin || 0} admins</span>
      <span class="sticker-chip">${counts.viewer || 0} viewers</span>
      <span class="sticker-chip">${counts.inspector || 0} inspectores</span>
    </div>`;
}

function rosterHtml(usuarios, filtered, ownUid, { role, status }) {
  const roster = filtered.length
    ? `<ul class="sticker-list">${filtered.map((u) => rowHtml(u, ownUid)).join('')}</ul>`
    : `<p class="sticker-empty">Ningún usuario coincide con el filtro.</p>`;
  const opt = (value, label, current) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`;

  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Cuentas</h3>
      ${chipsHtml(usuarios)}
      <button type="button" class="btn-primary sticker-new" id="usuario-new">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Nuevo administrador
      </button>
    </div>

    <div class="usuario-filters">
      <label class="sticker-field"><span>Rol</span>
        <select id="usuario-filter-role">
          ${opt('', 'Todos', role)}${opt('admin', 'Admin', role)}${opt('viewer', 'Viewer', role)}${opt('inspector', 'Inspector', role)}${opt('otro', 'Otro', role)}
        </select>
      </label>
      <label class="sticker-field"><span>Estado</span>
        <select id="usuario-filter-status">
          ${opt('', 'Todos', status)}${opt('activo', 'Activo', status)}${opt('inhabilitado', 'Inhabilitado', status)}
        </select>
      </label>
    </div>

    <p class="sticker-ok" id="usuario-ok" role="status" hidden></p>

    ${roster}

    <div class="modal" id="usuario-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="usuario-modal-title">
      <div class="modal-backdrop" data-modal-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="usuario-modal-title">Nuevo administrador</h2>
          <button type="button" class="btn-icon" data-modal-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <form id="usuario-form" class="sticker-form" novalidate>
            <div class="sticker-form-grid">
              ${field('email', 'Email *', 'type="email" required placeholder="admin@ejemplo.com" autocomplete="off"')}
              ${field('password', 'Contraseña *', 'type="text" required placeholder="mínimo 6 caracteres" autocomplete="off"')}
            </div>
            <p class="sticker-note">Crea una cuenta de administrador (contraseña). Los inspectores se crean desde la pestaña Stickers.</p>
            <p class="sticker-error" id="usuario-form-error" role="alert" hidden></p>
            <div class="sticker-form-actions">
              <button type="button" class="btn-secondary" data-modal-close>Cancelar</button>
              <button type="submit" class="btn-primary" id="usuario-submit">Crear administrador</button>
            </div>
          </form>
        </div>
      </div>
    </div>`;
}

function shellHtml() {
  return `
    <header class="sticker-page-head">
      <h2 class="sticker-h1">Usuarios</h2>
      <p class="sticker-lead">Todo el que tiene acceso al dashboard: administradores, viewers e inspectores.</p>
    </header>
    <section class="sticker-roster" id="usuario-roster"></section>`;
}

// initUsuarios(root, { getToken }) — renders the tab and wires its actions.
// Refetches from the API on each open (main.js calls this every time the
// tab is opened), same lifecycle as initStickers.
export function initUsuarios(root, { getToken }) {
  let busy = false;
  let usuarios = [];
  let ownUid = null;
  let roleFilter = '';
  let statusFilter = '';
  // Survives a re-render so a confirmation stays on screen after the list
  // comes back refreshed (mirrors stickers.js's assignedNotice).
  let notice = '';

  root.innerHTML = shellHtml();
  const rosterRoot = root.querySelector('#usuario-roster');

  const currentFiltered = () => usuarios.filter((u) =>
    (!roleFilter || u.role === roleFilter)
    && (!statusFilter || (statusFilter === 'activo' ? !u.disabled : u.disabled)));

  function render() {
    rosterRoot.innerHTML = rosterHtml(usuarios, currentFiltered(), ownUid, { role: roleFilter, status: statusFilter });
    wire();
    if (notice) {
      const ok = rosterRoot.querySelector('#usuario-ok');
      ok.textContent = notice;
      ok.hidden = false;
      notice = '';
    }
  }

  async function reload() {
    rosterRoot.innerHTML = '<p class="sticker-loading">Cargando usuarios…</p>';
    try {
      ownUid = getAuth(getFirebaseApp()).currentUser?.uid || null;
      const { usuarios: list } = await callApi(getToken, { action: 'list' });
      usuarios = list;
      render();
    } catch (err) {
      rosterRoot.innerHTML = `<p class="sticker-error" role="alert">${escapeHtml(err.message)}</p>`;
    }
  }

  function wire() {
    rosterRoot.querySelector('#usuario-filter-role').addEventListener('change', (e) => {
      roleFilter = e.target.value;
      render();
    });
    rosterRoot.querySelector('#usuario-filter-status').addEventListener('change', (e) => {
      statusFilter = e.target.value;
      render();
    });

    const modal = rosterRoot.querySelector('#usuario-modal');
    const form = rosterRoot.querySelector('#usuario-form');
    const formErr = rosterRoot.querySelector('#usuario-form-error');
    const showFormError = (msg) => { formErr.textContent = msg; formErr.hidden = !msg; };

    const openModal = () => {
      showFormError('');
      form.reset();
      modal.classList.add('is-open');
      modal.setAttribute('aria-hidden', 'false');
      form.querySelector('[name="email"]').focus();
    };
    const closeModal = () => {
      modal.classList.remove('is-open');
      modal.setAttribute('aria-hidden', 'true');
    };

    rosterRoot.querySelector('#usuario-new').addEventListener('click', openModal);
    modal.querySelectorAll('[data-modal-close]').forEach((el) => el.addEventListener('click', closeModal));
    modal.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy) return;
      showFormError('');
      const body = { action: 'create' };
      new FormData(form).forEach((v, k) => { body[k] = String(v).trim(); });
      busy = true;
      rosterRoot.querySelector('#usuario-submit').disabled = true;
      try {
        await callApi(getToken, body);
        notice = `Administrador creado: ${body.email}.`;
        closeModal();
        await reload();
      } catch (err) {
        showFormError(err.message);
        rosterRoot.querySelector('#usuario-submit').disabled = false;
      } finally {
        busy = false;
      }
    });

    rosterRoot.querySelectorAll('.usuario-row .sticker-action[data-uid][data-enable]').forEach((btn) => {
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
          alert(err.message); // rare path (network/permission, or the anti-lockout 403s); surface it plainly
        }
      });
    });

    rosterRoot.querySelectorAll('.usuario-row [data-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (busy) return;
        if (!confirm('¿Eliminar esta cuenta? Esta acción no se puede deshacer.')) return;
        busy = true;
        btn.disabled = true;
        try {
          await callApi(getToken, { action: 'delete', uid: btn.dataset.uid });
          notice = 'Usuario eliminado.';
          await reload();
        } catch (err) {
          busy = false;
          btn.disabled = false;
          alert(err.message); // surfaces the anti-lockout 403s ("último administrador") verbatim
        }
      });
    });

    rosterRoot.querySelectorAll('.usuario-row [data-reset]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (busy) return;
        busy = true;
        btn.disabled = true;
        try {
          // Client SDK, no API hop (design.md ADR-5): Firebase's hosted
          // email + action URL, same getAuth(getFirebaseApp()) handle
          // web/js/auth.js already uses.
          await sendPasswordResetEmail(getAuth(getFirebaseApp()), btn.dataset.email);
          alert(`Correo de recuperación enviado a ${btn.dataset.email}.`);
        } catch (err) {
          alert(err.message || String(err));
        } finally {
          busy = false;
          btn.disabled = false;
        }
      });
    });
  }

  reload();
}
