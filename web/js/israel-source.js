// Lee la colección Firestore `inspecciones_israel` (survey del equipo de Israel,
// ya remapeado al esquema EDE de Cali). Es una fuente SEPARADA de
// inspections.json: se trae aparte y solo se marca con `fuente: 'israel'` para
// poder colorear/distinguir por origen en el mapa, sin combinar los datasets en
// disco. Lectura pública (ver integracion_F1/firestore.rules).
//
// Reusa la app de Firebase que auth.js ya inicializó (getApp()). Nunca lanza:
// ante cualquier fallo (reglas, red, app sin iniciar) devuelve [] para que el
// tablero de Cali siga cargando igual.
import { getApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';
import {
  getFirestore, collection, getDocs,
} from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js';

const COLLECTION = 'inspecciones_israel';

export async function fetchIsraelRecords() {
  try {
    const db = getFirestore(getApp());
    const snap = await getDocs(collection(db, COLLECTION));
    return snap.docs.map((d) => ({ ...d.data(), fuente: 'israel' }));
  } catch (err) {
    console.warn(`No se pudo leer ${COLLECTION} de Firestore (sigue solo con Cali):`, err);
    return [];
  }
}
