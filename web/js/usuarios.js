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

const ROLE_LABEL = { admin: 'Administrador', usuario: 'Usuario', viewer: 'Viewer', inspector: 'Inspector', otro: 'Otro' };
const initials = (email) => (email || '').trim().slice(0, 2).toUpperCase() || '—';
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString('es-CO', { dateStyle: 'medium', timeStyle: 'short' }) : '—');

const field = (name, label, attrs = '') => `<label class="sticker-field">
    <span>${label}</span>
    <input name="${name}" ${attrs}>
  </label>`;

function rowHtml(u, ownUid, selected) {
  const activo = !u.disabled;
  const isSelf = u.uid === ownUid;
  // Self can't be selected/deleted (server guard rejects it too), so those
  // rows keep the decorative avatar; every other row swaps it for a select
  // checkbox in the same first grid column — no grid surgery needed.
  const lead = isSelf
    ? `<span class="sticker-avatar" aria-hidden="true">${escapeHtml(initials(u.email))}</span>`
    : `<input type="checkbox" class="usuario-check" data-uid="${escapeHtml(u.uid)}"${selected.has(u.uid) ? ' checked' : ''} aria-label="Seleccionar ${escapeHtml(u.email)}">`;
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
  // "Cambiar rol": a button that opens the role modal (assignable roles only, so
  // a stray non-assignable claim like "operador" is never offered). Hidden on
  // the caller's own row — the server blocks self-demotion anyway.
  const roleBtn = isSelf ? '' : `<button type="button" class="sticker-action" data-role-uid="${escapeHtml(u.uid)}" data-role-current="${escapeHtml(u.role)}" data-role-email="${escapeHtml(u.email)}">Cambiar rol</button>`;
  const meta = `${ROLE_LABEL[u.role] || u.role} · último acceso: ${fmtDate(u.lastSignInTime)} · alta: ${fmtDate(u.creationTime)}`;
  return `<li class="sticker-row usuario-row${activo ? '' : ' is-off'}">
    ${lead}
    <div class="sticker-identity">
      <span class="sticker-name">${escapeHtml(u.email || '—')}${isSelf ? ' <span class="sticker-warn" title="Tu propia cuenta">vos</span>' : ''}</span>
      <span class="sticker-meta">${escapeHtml(meta)}</span>
    </div>
    ${estado}
    <div class="usuario-actions">${roleBtn}${reset}${toggle}${del}</div>
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
      <span class="sticker-chip">${counts.usuario || 0} usuarios</span>
      <span class="sticker-chip">${counts.viewer || 0} viewers</span>
      <span class="sticker-chip">${counts.inspector || 0} inspectores</span>
    </div>`;
}

function pagerHtml({ page, totalPages, total }) {
  if (totalPages <= 1) return `<p class="usuario-pager-info">${total} resultado(s)</p>`;
  return `<div class="usuario-pager">
      <button type="button" class="btn-secondary" id="usuario-prev"${page <= 1 ? ' disabled' : ''}>Anterior</button>
      <span class="usuario-pager-info">Página ${page} de ${totalPages} · ${total} resultado(s)</span>
      <button type="button" class="btn-secondary" id="usuario-next"${page >= totalPages ? ' disabled' : ''}>Siguiente</button>
    </div>`;
}

function rosterHtml(usuarios, filtered, pageItems, ownUid, { role, status, query }, selected, pag) {
  const roster = filtered.length
    ? `<ul class="sticker-list">${pageItems.map((u) => rowHtml(u, ownUid, selected)).join('')}</ul>${pagerHtml(pag)}`
    : `<p class="sticker-empty">Ningún usuario coincide con la búsqueda o el filtro.</p>`;
  const opt = (value, label, current) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`;
  const bulk = selected.size
    ? `<button type="button" class="sticker-action sticker-action-off" id="usuario-bulk-delete">Eliminar seleccionados (${selected.size})</button>`
    : '';

  return `
    <div class="section-bar">
      <h3 class="section-bar-title">Cuentas</h3>
      ${chipsHtml(usuarios)}
      ${bulk}
      <button type="button" class="btn-primary sticker-new" id="usuario-new">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        Nuevo usuario
      </button>
    </div>

    <div class="usuario-filters">
      <label class="sticker-field usuario-search"><span>Buscar</span>
        <input type="search" id="usuario-search" placeholder="email, rol o estado…" value="${escapeHtml(query || '')}" autocomplete="off">
      </label>
      <label class="sticker-field"><span>Rol</span>
        <select id="usuario-filter-role">
          ${opt('', 'Todos', role)}${opt('admin', 'Administrador', role)}${opt('usuario', 'Usuario', role)}${opt('viewer', 'Viewer', role)}${opt('inspector', 'Inspector', role)}${opt('otro', 'Otro', role)}
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
          <h2 id="usuario-modal-title">Nuevo usuario</h2>
          <button type="button" class="btn-icon" data-modal-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <form id="usuario-form" class="sticker-form" novalidate>
            <div class="sticker-form-grid">
              ${field('email', 'Email *', 'type="email" required placeholder="usuario@ejemplo.com" autocomplete="off"')}
              ${field('password', 'Contraseña *', 'type="text" required placeholder="mínimo 6 caracteres" autocomplete="off"')}
            </div>
            <p class="sticker-note">Crea una cuenta de usuario (contraseña): ve solo el Panel. Promovela a administrador después con "Cambiar rol" si hace falta. Los inspectores se crean desde la pestaña Stickers.</p>
            <p class="sticker-error" id="usuario-form-error" role="alert" hidden></p>
            <div class="sticker-form-actions">
              <button type="button" class="btn-secondary" data-modal-close>Cancelar</button>
              <button type="submit" class="btn-primary" id="usuario-submit">Crear usuario</button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <div class="modal" id="usuario-role-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="usuario-role-title">
      <div class="modal-backdrop" data-role-close></div>
      <div class="modal-panel sticker-modal-panel">
        <div class="modal-header">
          <h2 id="usuario-role-title">Cambiar rol</h2>
          <button type="button" class="btn-icon" data-role-close aria-label="Cerrar">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <p class="sticker-note" id="usuario-role-target"></p>
          <div class="usuario-role-options" role="radiogroup" aria-label="Nuevo rol">
            <label><input type="radio" name="usuario-role" value="usuario"> <span><strong>Usuario</strong> · solo Panel</span></label>
            <label><input type="radio" name="usuario-role" value="viewer"> <span><strong>Viewer</strong> · solo Panel</span></label>
            <label><input type="radio" name="usuario-role" value="admin"> <span><strong>Administrador</strong> · acceso total (Stickers, Usuarios, Actualizar)</span></label>
          </div>
          <p class="sticker-note">El cambio aplica cuando el usuario vuelve a iniciar sesión.</p>
          <p class="sticker-error" id="usuario-role-error" role="alert" hidden></p>
          <div class="sticker-form-actions">
            <button type="button" class="btn-secondary" data-role-close>Cancelar</button>
            <button type="button" class="btn-primary" id="usuario-role-save">Guardar</button>
          </div>
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
  let query = '';
  let page = 1;
  const PAGE_SIZE = 25;
  const selected = new Set(); // uids checked for bulk delete; survives re-render
  // Survives a re-render so a confirmation stays on screen after the list
  // comes back refreshed (mirrors stickers.js's assignedNotice).
  let notice = '';

  root.innerHTML = shellHtml();
  const rosterRoot = root.querySelector('#usuario-roster');

  // Multi-term AND search over email + role label + estado (the user-visible
  // attributes). Empty query matches everything.
  const matchesQuery = (u) => {
    if (!query) return true;
    const hay = `${u.email} ${ROLE_LABEL[u.role] || u.role} ${u.disabled ? 'inhabilitado' : 'activo'}`.toLowerCase();
    return query.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
  };

  const currentFiltered = () => usuarios.filter((u) =>
    (!roleFilter || u.role === roleFilter)
    && (!statusFilter || (statusFilter === 'activo' ? !u.disabled : u.disabled))
    && matchesQuery(u));

  function render() {
    const filtered = currentFiltered();
    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    if (page < 1) page = 1;
    const start = (page - 1) * PAGE_SIZE;
    const pageItems = filtered.slice(start, start + PAGE_SIZE);
    rosterRoot.innerHTML = rosterHtml(
      usuarios, filtered, pageItems, ownUid,
      { role: roleFilter, status: statusFilter, query }, selected,
      { page, totalPages, total: filtered.length },
    );
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
      selected.clear(); // no dejar seleccionadas cuentas que el filtro oculta
      render();
    });
    rosterRoot.querySelector('#usuario-filter-status').addEventListener('change', (e) => {
      statusFilter = e.target.value;
      selected.clear();
      render();
    });

    rosterRoot.querySelector('#usuario-search').addEventListener('input', (e) => {
      query = e.target.value;
      page = 1;
      selected.clear(); // narrowing the set, like the filters, clears selection
      render();
      // render() rebuilt the input; restore focus + caret so typing is smooth
      const s = rosterRoot.querySelector('#usuario-search');
      s.focus();
      s.setSelectionRange(s.value.length, s.value.length);
    });

    const prev = rosterRoot.querySelector('#usuario-prev');
    const next = rosterRoot.querySelector('#usuario-next');
    if (prev) prev.addEventListener('click', () => { page -= 1; render(); });
    if (next) next.addEventListener('click', () => { page += 1; render(); });

    rosterRoot.querySelectorAll('.usuario-check').forEach((box) => {
      box.addEventListener('change', () => {
        if (box.checked) selected.add(box.dataset.uid);
        else selected.delete(box.dataset.uid);
        render(); // refreshes the "Eliminar seleccionados (N)" count
      });
    });

    const bulkBtn = rosterRoot.querySelector('#usuario-bulk-delete');
    if (bulkBtn) bulkBtn.addEventListener('click', async () => {
      if (busy) return;
      const uids = [...selected];
      if (!uids.length) return;
      if (!confirm(`¿Eliminar ${uids.length} cuenta(s)? Esta acción no se puede deshacer.`)) return;
      busy = true;
      bulkBtn.disabled = true;
      // Sequential on purpose: the API deletes one uid per call and the
      // last-admin guard reads a fresh snapshot each time, so serializing
      // keeps that guard honest. Failures (e.g. last-admin 403) are collected
      // per-account instead of aborting the batch.
      const emailByUid = new Map(usuarios.map((u) => [u.uid, u.email]));
      const errors = [];
      for (const uid of uids) {
        try {
          await callApi(getToken, { action: 'delete', uid });
          usuarios = usuarios.filter((u) => u.uid !== uid);
          selected.delete(uid);
        } catch (err) {
          errors.push(`${emailByUid.get(uid) || uid}: ${err.message}`);
        }
      }
      busy = false;
      const done = uids.length - errors.length;
      notice = errors.length
        ? `Eliminadas ${done}/${uids.length}. No se pudieron: ${errors.join('; ')}`
        : `${done} usuario(s) eliminado(s).`;
      render(); // local mutation + re-render, no full page/API reload
    });

    // Cambiar rol: a per-row button opens a modal to pick an assignable role.
    // roleEdit holds the target while the modal is open (no render happens between
    // open and save, so a wire()-scoped var is safe).
    let roleEdit = null;
    const roleModal = rosterRoot.querySelector('#usuario-role-modal');
    const roleErr = rosterRoot.querySelector('#usuario-role-error');
    const showRoleError = (msg) => { roleErr.textContent = msg; roleErr.hidden = !msg; };
    const closeRoleModal = () => {
      roleModal.classList.remove('is-open');
      roleModal.setAttribute('aria-hidden', 'true');
      roleEdit = null;
    };
    roleModal.querySelectorAll('[data-role-close]').forEach((el) => el.addEventListener('click', closeRoleModal));
    roleModal.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeRoleModal(); });

    rosterRoot.querySelectorAll('[data-role-uid]').forEach((btn) => {
      btn.addEventListener('click', () => {
        roleEdit = { uid: btn.dataset.roleUid, email: btn.dataset.roleEmail, current: btn.dataset.roleCurrent };
        showRoleError('');
        rosterRoot.querySelector('#usuario-role-target').textContent =
          `${roleEdit.email} — rol actual: ${ROLE_LABEL[roleEdit.current] || roleEdit.current}`;
        // Pre-select the current role if it's assignable; otherwise leave blank.
        roleModal.querySelectorAll('input[name="usuario-role"]').forEach((r) => { r.checked = r.value === roleEdit.current; });
        roleModal.classList.add('is-open');
        roleModal.setAttribute('aria-hidden', 'false');
      });
    });

    rosterRoot.querySelector('#usuario-role-save').addEventListener('click', async () => {
      if (busy || !roleEdit) return;
      const picked = roleModal.querySelector('input[name="usuario-role"]:checked');
      if (!picked) { showRoleError('Elegí un rol.'); return; }
      const role = picked.value;
      if (role === roleEdit.current) { closeRoleModal(); return; }
      const label = ROLE_LABEL[role] || role;
      busy = true;
      rosterRoot.querySelector('#usuario-role-save').disabled = true;
      try {
        await callApi(getToken, { action: 'setRole', uid: roleEdit.uid, role });
        const target = usuarios.find((u) => u.uid === roleEdit.uid);
        if (target) target.role = role; // reflect locally; no refetch
        notice = `Rol actualizado a ${label}. El usuario debe volver a iniciar sesión para que aplique.`;
        busy = false;
        closeRoleModal();
        render();
      } catch (err) {
        busy = false;
        rosterRoot.querySelector('#usuario-role-save').disabled = false;
        showRoleError(err.message); // e.g. the anti-lockout "tu propio rol" 403
      }
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
        notice = `Usuario creado: ${body.email}.`;
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
          busy = false;
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
          usuarios = usuarios.filter((u) => u.uid !== btn.dataset.uid);
          selected.delete(btn.dataset.uid);
          notice = 'Usuario eliminado.';
          busy = false;
          render(); // local mutation + re-render, no full page/API reload
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
