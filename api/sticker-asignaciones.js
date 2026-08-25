// Vercel Serverless Function — sticker matching/assignment CRUD ("Asignación"
// sub-section of the Stickers tab).
//
// Reads/writes two lean Firestore collections that `integracion_F1/
// cruce_sticker.py` (pipeline) and this endpoint (admin) jointly own, split by
// field group — see design.md ADR-1:
//   sticker_matches/{fuente}_{registro_id}  — pipeline-owned cruce result +
//                                              admin-owned assignment state
//   cuadrillas/{id}                         — groups of pending points,
//                                              optionally linked to one
//                                              inspector
//
// This endpoint NEVER reads inspections.json/puntos_israel_cali.json (the
// full Panel) — it only ever touches the two lean collections above, which is
// the "sin necesidad de cargar todos los datos de Panel" requirement.
//
// Required env in Vercel (Project Settings → Environment Variables):
//   FIREBASE_SERVICE_ACCOUNT_JSON  Service-account key JSON for sismo-agosto-sgred,
//     as a single-line string (Firebase console → Project settings →
//     Service accounts → Generate new private key).
//
// Auth: same Bearer ID-token check as api/stickers.js / api/usuarios.js —
// fail-closed, admin-only.

const { verifyFirebaseToken, roleFromClaims } = require('./refresh.js');

const FIREBASE_PROJECT_ID = process.env.FIREBASE_PROJECT_ID || 'sismo-agosto-sgred';

// task 0.2 placeholders: not yet confirmed with the operator. Named constants
// (not magic numbers) so a later tune is a one-line change. See design.md
// ADR-3 / tasks.md 0.2.
const DEFAULT_MAX_RADIUS_M = 800;
const DEFAULT_MAX_SIZE = 8;

// ---- firebase-admin singleton ----------------------------------------------
// Duplicated (not imported from stickers.js) on purpose — each serverless
// function stays self-contained (design.md ADR-3, mirrors usuarios.js's own
// note on the same pattern).
let adminSdk = null;
function getAdmin() {
  if (adminSdk) return adminSdk;
  const raw = process.env.FIREBASE_SERVICE_ACCOUNT_JSON;
  if (!raw) throw new Error('FIREBASE_SERVICE_ACCOUNT_JSON no está configurado en Vercel.');
  const admin = require('firebase-admin');
  if (!admin.apps.length) {
    admin.initializeApp({ credential: admin.credential.cert(JSON.parse(raw)) });
  }
  adminSdk = admin;
  return admin;
}

// ---- pure helpers (exported for the self-check) ----------------------------

// Great-circle distance in meters. No JS haversine already exists in the repo
// (checked web/js/evaluaciones.js and the rest of web/js/*.js) — five-line
// port of the same formula scripts/refresh_data.py's `_haversine_m` uses.
const EARTH_RADIUS_M = 6371000;
function haversineM(a, b) {
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const la1 = toRad(a.lat);
  const la2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

// Deterministic greedy nearest-neighbor clustering — design.md ADR-3's locked
// pseudocode. Stable [lat, lon] sort order, no RNG, no k-means, so re-running
// on an unchanged point set produces identical groups (proposal.md risk 4).
// ponytail: O(n²) greedy grouping, fine to a few thousand pending points;
// switch to a spatial grid pre-bucket if it ever gets slow.
function autoAgrupar(puntos, { maxRadiusM, maxSize }) {
  const sorted = [...puntos].sort((a, b) => {
    const dLat = a.coords.lat - b.coords.lat;
    if (dLat !== 0) return dLat;
    return a.coords.lon - b.coords.lon;
  });
  const unassigned = new Set(sorted.map((p) => p.id));
  const grupos = [];
  for (const seed of sorted) {
    if (!unassigned.has(seed.id)) continue;
    const grupo = [seed];
    unassigned.delete(seed.id);
    for (const p of sorted) {
      if (grupo.length >= maxSize) break;
      if (!unassigned.has(p.id)) continue;
      if (haversineM(seed.coords, p.coords) <= maxRadiusM) {
        grupo.push(p);
        unassigned.delete(p.id);
      }
    }
    grupos.push(grupo);
  }
  return grupos;
}

// ---- Firestore-backed actions ----------------------------------------------

async function listPuntos(admin) {
  const snap = await admin.firestore().collection('sticker_matches').get();
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

async function listCuadrillas(admin) {
  const snap = await admin.firestore().collection('cuadrillas').get();
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}

// Groups current `pendiente` points with no `cuadrilla_id` and creates new
// `cuadrillas` docs. MUST NOT touch `estado_asignacion` — grouping and
// assigning are separate actions (design.md ADR-3).
async function runAutoAgrupar(admin, body) {
  const maxRadiusM = Number(body.maxRadiusM) > 0 ? Number(body.maxRadiusM) : DEFAULT_MAX_RADIUS_M;
  const maxSize = Number(body.maxSize) > 0 ? Number(body.maxSize) : DEFAULT_MAX_SIZE;

  const db = admin.firestore();
  const snap = await db.collection('sticker_matches')
    .where('estado_asignacion', '==', 'pendiente')
    .where('cuadrilla_id', '==', null)
    .get();
  const puntos = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
  if (puntos.length === 0) return [];

  const grupos = autoAgrupar(puntos, { maxRadiusM, maxSize });
  const cuadrillas = [];
  const batch = db.batch();
  for (const grupo of grupos) {
    const ref = db.collection('cuadrillas').doc();
    const puntoIds = grupo.map((p) => p.id);
    const data = { puntos: puntoIds, inspector_uid: null, origen: 'auto' };
    batch.set(ref, data);
    for (const puntoId of puntoIds) {
      batch.set(db.doc(`sticker_matches/${puntoId}`), { cuadrilla_id: ref.id }, { merge: true });
    }
    cuadrillas.push({ id: ref.id, ...data });
  }
  await batch.commit();
  return cuadrillas;
}

async function crearCuadrilla(admin, body) {
  const nombre = String(body.nombre || '').trim();
  const puntos = Array.isArray(body.puntos) ? body.puntos.map(String) : [];
  if (!puntos.length) throw badRequest('crearCuadrilla necesita al menos un punto.');

  const db = admin.firestore();
  const ref = db.collection('cuadrillas').doc();
  const data = { nombre, puntos, inspector_uid: null, origen: 'manual' };
  const batch = db.batch();
  batch.set(ref, data);
  for (const puntoId of puntos) {
    batch.set(db.doc(`sticker_matches/${puntoId}`), { cuadrilla_id: ref.id }, { merge: true });
  }
  await batch.commit();
  return { id: ref.id };
}

// Add/remove points from an existing cuadrilla, keeping each point's
// `cuadrilla_id` consistent with membership.
async function editarCuadrilla(admin, body) {
  const cuadrillaId = String(body.cuadrilla_id ?? '').trim();
  const add = Array.isArray(body.add) ? body.add.map(String) : [];
  const remove = Array.isArray(body.remove) ? body.remove.map(String) : [];
  if (!cuadrillaId) throw badRequest('Falta cuadrilla_id.');

  const db = admin.firestore();
  const ref = db.doc(`cuadrillas/${cuadrillaId}`);
  const snap = await ref.get();
  if (!snap.exists) throw badRequest(`No existe la cuadrilla ${cuadrillaId}.`);

  const current = new Set((snap.data().puntos || []).map(String));
  for (const id of remove) current.delete(id);
  for (const id of add) current.add(id);
  const nextPuntos = [...current];

  const batch = db.batch();
  batch.set(ref, { puntos: nextPuntos }, { merge: true });
  for (const puntoId of add) {
    batch.set(db.doc(`sticker_matches/${puntoId}`), { cuadrilla_id: cuadrillaId }, { merge: true });
  }
  for (const puntoId of remove) {
    batch.set(db.doc(`sticker_matches/${puntoId}`), { cuadrilla_id: null }, { merge: true });
  }
  await batch.commit();
  return { id: cuadrillaId, puntos: nextPuntos };
}

// Propagates inspector_uid/asignado_en/estado_asignacion:'asignado' to every
// point currently in the cuadrilla.
async function asignarInspector(admin, body) {
  const cuadrillaId = String(body.cuadrilla_id ?? '').trim();
  const inspectorUid = String(body.inspector_uid ?? '').trim();
  if (!cuadrillaId) throw badRequest('Falta cuadrilla_id.');
  if (!inspectorUid) throw badRequest('Falta inspector_uid.');

  const db = admin.firestore();
  const ref = db.doc(`cuadrillas/${cuadrillaId}`);
  const snap = await ref.get();
  if (!snap.exists) throw badRequest(`No existe la cuadrilla ${cuadrillaId}.`);

  const puntos = (snap.data().puntos || []).map(String);
  const now = admin.firestore.FieldValue.serverTimestamp();
  const batch = db.batch();
  batch.set(ref, { inspector_uid: inspectorUid }, { merge: true });
  for (const puntoId of puntos) {
    batch.set(db.doc(`sticker_matches/${puntoId}`), {
      inspector_uid: inspectorUid,
      asignado_en: now,
      estado_asignacion: 'asignado',
    }, { merge: true });
  }
  await batch.commit();
  return { id: cuadrillaId };
}

// Reassigns a single point to a different inspector, recording the previous
// inspector uid as a one-hop breadcrumb — independent of cuadrilla membership.
async function reasignarPunto(admin, body) {
  const puntoId = String(body.punto_id ?? '').trim();
  const nuevoInspectorUid = String(body.nuevo_inspector_uid ?? '').trim();
  if (!puntoId) throw badRequest('Falta punto_id.');
  if (!nuevoInspectorUid) throw badRequest('Falta nuevo_inspector_uid.');

  const db = admin.firestore();
  const ref = db.doc(`sticker_matches/${puntoId}`);
  const snap = await ref.get();
  if (!snap.exists) throw badRequest(`No existe el punto ${puntoId}.`);

  const prevInspectorUid = snap.data().inspector_uid ?? null;
  await ref.set({
    inspector_uid: nuevoInspectorUid,
    reasignado_de: prevInspectorUid,
  }, { merge: true });
  return { id: puntoId, inspector_uid: nuevoInspectorUid, reasignado_de: prevInspectorUid };
}

// Clears cuadrilla_id/inspector_uid on every member point BEFORE deleting the
// cuadrillas doc, so no point is left referencing a nonexistent cuadrilla
// even if the delete step fails partway.
async function eliminarCuadrilla(admin, body) {
  const cuadrillaId = String(body.cuadrilla_id ?? '').trim();
  if (!cuadrillaId) throw badRequest('Falta cuadrilla_id.');

  const db = admin.firestore();
  const ref = db.doc(`cuadrillas/${cuadrillaId}`);
  const snap = await ref.get();
  if (!snap.exists) throw badRequest(`No existe la cuadrilla ${cuadrillaId}.`);

  const puntos = (snap.data().puntos || []).map(String);
  const clearBatch = db.batch();
  for (const puntoId of puntos) {
    clearBatch.set(db.doc(`sticker_matches/${puntoId}`), { cuadrilla_id: null, inspector_uid: null }, { merge: true });
  }
  await clearBatch.commit();
  await ref.delete();
  return { id: cuadrillaId };
}

function badRequest(message) {
  const err = new Error(message);
  err.status = 400;
  return err;
}

module.exports = async (req, res) => {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  // Fail-closed auth: valid Firebase ID token AND effective role 'admin' —
  // byte-for-byte the same preamble as api/stickers.js.
  const authHeader = req.headers.authorization || '';
  const idToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
  if (!idToken) return res.status(401).json({ error: 'Autenticación requerida.' });
  try {
    const claims = await verifyFirebaseToken(idToken, FIREBASE_PROJECT_ID);
    if (roleFromClaims(claims) !== 'admin') {
      return res.status(403).json({ error: 'Solo administradores pueden gestionar asignaciones.' });
    }
  } catch (err) {
    return res.status(401).json({ error: `Token inválido: ${(err && err.message) || err}` });
  }

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body || {});
  const action = body.action;

  try {
    const admin = getAdmin();
    if (action === 'listPuntos') return res.status(200).json({ ok: true, puntos: await listPuntos(admin) });
    if (action === 'listCuadrillas') return res.status(200).json({ ok: true, cuadrillas: await listCuadrillas(admin) });
    if (action === 'autoAgrupar') return res.status(200).json({ ok: true, cuadrillas: await runAutoAgrupar(admin, body) });
    if (action === 'crearCuadrilla') return res.status(201).json({ ok: true, ...(await crearCuadrilla(admin, body)) });
    if (action === 'editarCuadrilla') return res.status(200).json({ ok: true, ...(await editarCuadrilla(admin, body)) });
    if (action === 'asignarInspector') return res.status(200).json({ ok: true, ...(await asignarInspector(admin, body)) });
    if (action === 'reasignarPunto') return res.status(200).json({ ok: true, ...(await reasignarPunto(admin, body)) });
    if (action === 'eliminarCuadrilla') return res.status(200).json({ ok: true, ...(await eliminarCuadrilla(admin, body)) });
    return res.status(400).json({ error: `Acción desconocida: ${action}` });
  } catch (err) {
    const status = err && err.status ? err.status : 502;
    return res.status(status).json({ error: String((err && err.message) || err) });
  }
};

// Exposed for the self-check (api/sticker-asignaciones.test.js); Vercel uses
// the default export.
module.exports.autoAgrupar = autoAgrupar;
module.exports.haversineM = haversineM;
module.exports.DEFAULT_MAX_RADIUS_M = DEFAULT_MAX_RADIUS_M;
module.exports.DEFAULT_MAX_SIZE = DEFAULT_MAX_SIZE;
