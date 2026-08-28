// Pure form logic, DOM- and Firebase-free so it is testable in Node.

export const MUNICIPIO = '76001'; // DIVIPOLA code for Cali

// Inspectors sign in with their cédula only; Firebase Auth needs an email, so
// we synthesize one. A value that already contains "@" is used verbatim (lets a
// real email still work and avoids a double-@).
export const LOGIN_EMAIL_DOMAIN = 'sismocali.gov.co';
export function cedulaToEmail(raw) {
  const v = String(raw == null ? '' : raw).trim().toLowerCase();
  if (!v) return '';
  return v.includes('@') ? v : `${v}@${LOGIN_EMAIL_DOMAIN}`;
}

// ATC-20: the worst observed condition governs the suggested classification.
export function sugerirClasificacion(criterios) {
  const vals = Object.values(criterios);
  if (vals.includes('severo')) return 'INSEGURO';
  if (vals.includes('moderado')) return 'USO_RESTRINGIDO';
  return 'INSPECCIONADA';
}

// e.g. buildCodigo('1', '004', 1) -> '76001-1-0040001'
export function buildCodigo(area, codigoInspector, consecutivo) {
  return `${MUNICIPIO}-${area}-${codigoInspector}${String(consecutivo).padStart(4, '0')}`;
}

// Inverse of buildCodigo's third segment: strips the fixed 3-digit inspector
// code and parses the remainder as the consecutive. Width-agnostic so codes
// that already widened past 9999 (see the ceiling test above) still parse:
// '76001-1-00410000' with codigoInspector '004' -> 10000. Returns null when
// the code does not belong to this inspector (wrong area segment, wrong
// inspector prefix, or malformed input).
export function parseConsecutivo(codigo, codigoInspector) {
  if (typeof codigo !== 'string' || !codigoInspector) return null;
  const parts = codigo.split('-');
  if (parts.length !== 3) return null;
  const [, , tail] = parts;
  if (!tail.startsWith(codigoInspector)) return null;
  const rest = tail.slice(codigoInspector.length);
  if (!/^\d+$/.test(rest)) return null;
  return Number(rest);
}

// Max-based (not count-based) so gaps in the sequence never collide with an
// existing code: {1, 3} -> 4, not 3. Codes that do not belong to this
// inspector (parseConsecutivo -> null) are ignored.
export function siguienteConsecutivo(codigos, codigoInspector) {
  let max = 0;
  for (const codigo of codigos) {
    const n = parseConsecutivo(codigo, codigoInspector);
    if (n != null && n > max) max = n;
  }
  return max + 1;
}

// Pure "next from a known max" arithmetic — the session-cache counterpart to
// siguienteConsecutivo, for when the caller already holds the max (no codes
// array to re-scan). Same max-based semantics: null/undefined means "no
// known max yet" and is treated as 0.
export function siguienteDesdeMax(maxConocido) {
  return (maxConocido == null ? 0 : maxConocido) + 1;
}

// Folds a consecutive that was just actually saved into the session's known
// max, via max() — never a blind assignment. This is what lets the session
// cache learn from a manually-edited code (the saved value can be higher OR
// lower than what was derived before the edit) without a gap-filling
// correction (saving a value BELOW the current max) ever dragging the known
// max backwards.
export function plegarConsecutivoGuardado(maxConocido, consecutivoGuardado) {
  const max = maxConocido == null ? 0 : maxConocido;
  return Math.max(max, consecutivoGuardado);
}

// Consecutivos already saved by THIS inspector, extracted from record ids.
// Backs the duplicate guard on the editable segment: an edited value that is
// already in this set can never be submitted (it would collide at the
// create-only transaction anyway — this catches it at edit time instead of
// after photo uploads). Codes of other inspectors parse to null and drop out.
export function consecutivosExistentes(codigos, codigoInspector) {
  const set = new Set();
  for (const codigo of codigos) {
    const n = parseConsecutivo(codigo, codigoInspector);
    if (n != null) set.add(n);
  }
  return set;
}

// Validates the editable 4-digit consecutive segment the inspector types.
// Deliberately no "floor at next available" rule: a value below the derived
// next is a legitimate gap-filling correction, not an error (caller shows a
// non-blocking hint instead). '0000' is rejected — zero is not a valid
// consecutive.
export function validarSegmento(raw) {
  const v = String(raw == null ? '' : raw).trim();
  if (!v) return { ok: false, value: null, code: 'vacio' };
  if (v.length !== 4) return { ok: false, value: null, code: 'longitud' };
  if (!/^\d{4}$/.test(v)) return { ok: false, value: null, code: 'no-numerico' };
  const n = Number(v);
  if (n === 0) return { ok: false, value: null, code: 'cero' };
  return { ok: true, value: n, code: null };
}

// ---- Photos -----------------------------------------------------------------

// Slot-generic cap (design decision "Slot-Generic Design With Capped
// Fallback"): the client never hardcodes below-10 UI logic — only this
// constant changes if the external signer's accepted slot range changes.
// Set to 10 after a signer re-probe confirmed slot 1..10 pass schema
// validation while slot 11 is rejected with `400 bad-request` (see
// formulario/SETUP.md section 7 for the raw probe evidence).
export const MAX_FOTOS = 10;

// Pure predicate for the dynamic add-tile: true while there is room for one
// more photo. `current` is the number of photos already attached.
export function canAddSlot(current, max = MAX_FOTOS) {
  return current < max;
}

// ---- Assigned points (sticker_matches) ---------------------------------------

// Assigned points still to visit: everything except those already completed.
// The dashboard/cruce marks a point 'hecho' once its evaluación lands, so those
// drop off the inspector's list. Firestore `!=` needs a composite index, so the
// estado filter is done here in code over the inspector's own docs.
export function filtrarPendientes(asignaciones) {
  return (asignaciones || []).filter((a) => a && a.estado_asignacion !== 'hecho');
}

// Habitability criterion → severity color token, reusing the form's palette.
// h = habitable (green), r1/r2 = restricted use (amber), i1/i2/i3 = unsafe
// (red). Unknown/empty → muted grey. Value is a CSS custom-property reference
// so the caller can drop it straight into an inline style.
export function habitabilidadColor(criterio) {
  const c = String(criterio == null ? '' : criterio).trim().toLowerCase();
  if (c === 'h') return 'var(--verde)';
  if (c === 'r1' || c === 'r2') return 'var(--ambar)';
  if (c === 'i1' || c === 'i2' || c === 'i3') return 'var(--rojo)';
  return 'var(--muted)';
}

// Colapso pill label: total/parcial get a (red) pill; anything else — 'no',
// empty, unknown — means no pill (return '').
export function colapsoLabel(colapso) {
  const c = String(colapso == null ? '' : colapso).trim().toLowerCase();
  if (c === 'total') return 'Total';
  if (c === 'parcial') return 'Parcial';
  return '';
}

// Planeación priority → color token, same palette habitabilidadColor uses
// (alta = red, media = amber, baja/unknown = muted) so a planeación card's
// left border reads consistently with a sticker card's own severity color.
export function prioridadColor(prioridad) {
  const p = String(prioridad == null ? '' : prioridad).trim().toLowerCase();
  if (p === 'alta') return 'var(--rojo)';
  if (p === 'media') return 'var(--ambar)';
  return 'var(--muted)';
}

// Google Maps directions deep link to a point's coordinates. sticker_matches
// coords are { lat, lon } (note: lon, not lng). Returns '' when coords are
// missing so the caller can hide the "Cómo llegar" button.
export function mapsDirUrl(coords) {
  if (!coords || coords.lat == null || coords.lon == null) return '';
  return `https://www.google.com/maps/dir/?api=1&destination=${coords.lat},${coords.lon}`;
}

// Which prefilled Survey123 link a "planeación" (EDAN) card's "Abrir
// encuesta" button should open. `punto` carries `survey_web`/`survey_app`
// as returned by misPuntosPlaneacion (app is null when
// SURVEY123_FIELD_APP_ITEM_ID is not configured). Always prefers `survey_web`:
// `survey_app` is an `arcgis-survey123://` custom-scheme deep link that only
// resolves if the device has the native Survey123 app installed — on any
// other device the browser can't open it at all (Safari: "address is
// invalid"; Android Chrome: blank tab showing the raw scheme), which is a
// dead end with no client-side fallback once navigation has already
// happened. The web URL works in any browser regardless of what's
// installed, so it's the only link this app opens on its own; `survey_app`
// is kept in the returned URLs (see logic.test.mjs) for a future explicit
// "open in app" affordance, not as an automatic mobile default. Returns ''
// when neither link is available (missing SURVEY123_FORM_URL), so the
// caller can disable/hide the button instead of wiring up a dead link.
export function elegirEnlaceEncuesta(punto, _isMobile) {
  if (!punto) return '';
  return punto.survey_web || punto.survey_app || '';
}

// ---- Proximity sort (assigned points) ----------------------------------------

// Normalizes a lat/lng-ish object into { lat, lng } finite numbers, tolerant
// of every shape this app deals with: backend assigned points use
// { lat, lon } (see mapsDirUrl above), the raw browser Geolocation API gives
// { latitude, longitude }, and the form's own GPS state already normalizes
// to { lat, lng }. Returns null when no usable pair of finite numbers is
// found so callers never fall back to 0/0 — a false "you are here" is worse
// than "unknown".
function normalizarCoords(p) {
  if (!p) return null;
  const lat = p.lat != null ? p.lat : p.latitude;
  const lng = p.lng != null ? p.lng : (p.lon != null ? p.lon : p.longitude);
  if (typeof lat !== 'number' || typeof lng !== 'number' || !Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null;
  }
  return { lat, lng };
}

const EARTH_RADIUS_M = 6371000;

// Haversine distance in metres between two lat/lng-ish points (see
// normalizarCoords for the tolerated shapes). Returns null — never NaN,
// never 0 — when either point lacks usable coordinates.
export function distanciaM(a, b) {
  const pa = normalizarCoords(a);
  const pb = normalizarCoords(b);
  if (!pa || !pb) return null;
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(pb.lat - pa.lat);
  const dLng = toRad(pb.lng - pa.lng);
  const lat1 = toRad(pa.lat);
  const lat2 = toRad(pb.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

// Sorts assigned points nearest-first from `origen` (the user's current GPS
// fix). Points with no usable coordinates (distanciaM -> null) sort LAST,
// are never dropped, and keep their original relative order (native sort is
// stable). If `origen` is null (GPS denied/unavailable/not resolved yet) the
// list is returned unchanged rather than an arbitrary reorder — never guess
// a "nearest" without a real fix.
export function ordenarPorCercania(puntos, origen) {
  const lista = puntos || [];
  if (!origen) return lista;
  return [...lista].sort((x, y) => {
    const dx = distanciaM(origen, x && x.coords);
    const dy = distanciaM(origen, y && y.coords);
    if (dx == null && dy == null) return 0;
    if (dx == null) return 1;
    if (dy == null) return -1;
    return dx - dy;
  });
}

// puntos-solicitados, field-form-session delta: solicited points ("PRIORIDAD")
// MUST render before every other assigned point. Stable partition — points
// keep whatever relative order they already had (e.g. from
// ordenarPorCercania) within their own group, so composing
// solicitadosPrimero(ordenarPorCercania(puntos, origen)) keeps nearest-first
// WITHIN each group while still putting the whole solicited group first.
export function solicitadosPrimero(puntos) {
  const lista = puntos || [];
  return [...lista].sort((x, y) => (y && y.es_solicitado ? 1 : 0) - (x && x.es_solicitado ? 1 : 0));
}

// puntos-solicitados, field-form-session delta: a solicited point gets its
// own "PRIORIDAD" badge instead of the alta/media/baja pill — it is a
// different KIND of priority (admin-flagged special case), not another
// severity level, so the two never render together for the same point.
export function prioridadVisual(p) {
  const esSolicitado = Boolean(p && p.es_solicitado);
  return {
    badge: esSolicitado,
    pill: Boolean(p && p.prioridad) && !esSolicitado,
  };
}

// Field-readable distance for a point card: null -> em dash (unavailable),
// sub-km rounds to whole metres, 1 km and above uses one decimal with a
// Spanish decimal comma (this app is Spanish-only).
export function formatDistancia(m) {
  if (typeof m !== 'number' || !Number.isFinite(m)) return '—';
  if (m < 1000) return `${Math.round(m)} m`;
  return `${(m / 1000).toFixed(1).replace('.', ',')} km`;
}

// ---- Nearby UNASSIGNED points (puntosCercanosDisponibles / tomarPunto) ------
// `puntos-disponibles` change (2026-08-26): a THIRD tab, "Cercanos", next to
// the existing Survey/Stickers tabs — see form.js's own module notes for why
// a third tab (not a section within each tab) was chosen: an inspector must
// never confuse "assigned to me" with "up for grabs", and a separate tab is
// the one layout that makes the two lists impossible to visually merge,
// unlike a section that scrolls past and can be mistaken for more of the
// same list above it.

// Campaign label a "cercano" card shows — one lookup so form.js never
// hand-picks the Spanish label inline.
export function etiquetaCampana(campana) {
  if (campana === 'sticker') return 'Sticker';
  if (campana === 'survey') return 'Encuesta EDAN';
  return '';
}

// Cercanos claim-button label per campaign — same one-lookup convention as
// etiquetaCampana (unknown/undefined falls back to '', never a hand-picked
// inline string in form.js).
export function etiquetaAccionCercano(campana) {
  if (campana === 'survey') return 'Levantar survey';
  if (campana === 'sticker') return 'Pegar sticker';
  return '';
}

// The "cercanos" tab's own small state machine, driving BOTH the status
// message and whether the card list itself is shown. GPS is a HARD
// requirement for this tab (binding requirement: "if GPS is denied, this
// section cannot work at all — say so plainly"), unlike the assigned-points
// tabs where a missing fix only degrades sort order (they still show every
// assigned point, just unordered).
export const CERCANOS_ESPERANDO = 'esperando';
export const CERCANOS_SIN_GPS = 'sin-gps';
export const CERCANOS_CARGANDO = 'cargando';
export const CERCANOS_VACIO = 'vacio';
export const CERCANOS_LISTO = 'listo';
export const CERCANOS_ERROR = 'error';

// Deterministic status message per state — one function so form.js never
// hand-picks Spanish strings inline (and the mapping is testable without
// mocking geolocation/DOM).
export function mensajeEstadoCercanos(estado) {
  switch (estado) {
    case CERCANOS_ESPERANDO:
      return 'Obteniendo tu ubicación para mostrar puntos cercanos…';
    case CERCANOS_SIN_GPS:
      return 'No se pudo obtener tu ubicación: sin ubicación no es posible mostrar puntos cercanos disponibles.';
    case CERCANOS_CARGANDO:
      return 'Buscando puntos cercanos…';
    case CERCANOS_VACIO:
      return 'No hay puntos disponibles cerca de ti en este momento.';
    case CERCANOS_ERROR:
      return 'No se pudo cargar los puntos cercanos. Intenta de nuevo.';
    default:
      return '';
  }
}

// True only once there is a real card list to render (a GPS fix landed AND
// the fetch returned at least one point) — every other state shows the
// status message INSTEAD of the list, never an ambiguous empty list that
// could be misread as "confirmed nothing nearby" when it actually means
// "we never even asked" (no GPS) or "still asking" (loading).
export function cercanosMuestraLista(estado) {
  return estado === CERCANOS_LISTO;
}

// Human-readable outcome line for a successful tomarPunto response
// (`{ asignados: { sticker?: id, survey?: id }, tambien_asignado: bool }`),
// so the "también se te asignó X" outcome is never swallowed. '' when the
// claim only covered the campaign the inspector already tapped from.
export function mensajeTomarPunto(campanaOriginal, resultado) {
  if (!resultado || !resultado.tambien_asignado) return '';
  const otra = campanaOriginal === 'sticker' ? 'la encuesta EDAN' : 'el sticker';
  return `También te asignamos ${otra} de este mismo edificio.`;
}

// Failure message for a rejected tomarPunto (lost race, already covered,
// etc.) — prefers the backend's own `detail` (already field-facing Spanish,
// e.g. "otro inspector ya tomó este punto") and falls back to a generic
// message only when the backend gave none.
export function mensajeErrorTomarPunto(detalle) {
  return detalle
    || 'No se pudo tomar el punto. Puede que otro inspector ya lo haya tomado; actualiza la lista e intenta con otro.';
}

// ---- Session resilience ------------------------------------------------------

const TRANSIENT_FIRESTORE_CODES = new Set(['unavailable', 'deadline-exceeded', 'network-request-failed']);
const FATAL_FIRESTORE_CODES = new Set(['permission-denied', 'not-found']);

// Classifies a Firebase/Firestore error by its `.code`. Fatal errors are
// authoritative rejections (retrying can never succeed); everything else,
// including unknown/missing codes, is transient — the Firestore rules are
// the durable gate (a live ID token can't create evaluaciones on its own),
// so failing OPEN on the session here is safe, and failing CLOSED is the
// field-logout bug this classification exists to prevent.
export function clasificarErrorFirestore(err) {
  const code = err && err.code ? err.code : '';
  if (FATAL_FIRESTORE_CODES.has(code)) return 'fatal';
  return 'transient';
}

// Exponential backoff for the profile-read retry loop: attempt 1 -> 600ms,
// attempt 2 -> 1800ms, attempt 3 -> 5400ms (base * 3^(attempt-1)).
export function backoffDelay(attempt, base = 600) {
  return base * 3 ** (attempt - 1);
}
