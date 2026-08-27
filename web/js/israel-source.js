// Lee la colección Firestore `inspecciones_israel` (survey del equipo de Israel,
// ya remapeado al esquema EDE de Cali). Es una fuente SEPARADA de
// inspections.json: se trae aparte y solo se marca con `fuente: 'israel'` para
// poder colorear/distinguir por origen en el mapa, sin combinar los datasets en
// disco. Lectura pública (ver integracion_F1/firestore.rules).
//
// Reusa la app de Firebase que auth.js ya inicializó (getApp()). Nunca lanza:
// ante cualquier fallo (reglas, red, app sin iniciar) devuelve [] para que el
// tablero de Cali siga cargando igual.
const COLLECTION = 'inspecciones_israel';

export async function fetchIsraelRecords() {
  try {
    // D2 (planeacion-flujo-confiable): BOTH `./firebase-config.js` (which
    // itself top-level-imports the `firebase-app.js` CDN URL) AND the
    // `firebase-firestore.js` CDN URL are lazy `await import()`s here, not
    // top-level imports — a top-level CDN-touching import makes
    // `node --test` crash on EVERY transitive importer of this module
    // (data.js, analista.js, evaluaciones.js) with
    // ERR_UNSUPPORTED_ESM_URL_SCHEME. Same "lazy-import firebase-config.js
    // itself, not just the raw CDN specifier" precedent `usuarios.js`'s own
    // `loadFirebaseAuth` already established. Resolved lazily, on first
    // real call; browser behavior is unchanged (same modules, same URLs).
    const { isConfigured, getFirebaseApp } = await import('./firebase-config.js');
    if (!isConfigured()) return [];
    const { getFirestore, collection, getDocs } = await import(
      'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js'
    );
    const db = getFirestore(getFirebaseApp());
    const snap = await getDocs(collection(db, COLLECTION));
    return snap.docs.map((d) => ({ ...d.data(), fuente: 'israel' }));
  } catch (err) {
    console.warn(`No se pudo leer ${COLLECTION} de Firestore (sigue solo con Cali):`, err);
    return [];
  }
}
