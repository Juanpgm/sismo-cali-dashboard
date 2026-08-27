// In-memory Firebase test double. The four gstatic module URLs the app imports
// are intercepted by Playwright and served with the bodies below; all of them
// read/write a single shared `window.__fb` object seeded per test via
// addInitScript. This exercises the app's real wiring (auth gate, transactions,
// upload-then-write order, create-only submit) with zero network and full
// observability, without a Firebase emulator or real credentials.

export const GSTATIC = 'https://www.gstatic.com/firebasejs/10.12.0/';

// Default seed: one registered inspector with code "004", consecutivo 0.
export function defaultSeed() {
  return {
    users: { 'inspector@cali.gov.co': { uid: 'uid-004', password: 'secret123' } },
    firestore: {
      inspectores: {
        'uid-004': {
          nombre_completo: 'Ana Ruiz',
          identificacion: '1144000111',
          entidad: 'SGRED',
          profesion: 'Ingeniera civil',
          codigo: '004',
          consecutivo: 0,
        },
      },
      evaluaciones: {},
    },
    // Assigned-points endpoint (`/inspector-asignaciones`) responses, served by
    // the Node-side route in mockFirebase (NOT the browser Firebase double, so
    // these live outside `firestore`). Empty by default → the picker shows only
    // its "Registrar sin punto asignado" escape hatch. A spec seeds
    // `stickers`/`planeacion`/`cercanos` to exercise the assigned-points flow.
    asignaciones: { stickers: [], planeacion: [], cercanos: [] },
    flags: {},
  };
}

// Runs in the browser before any app module. Builds window.__fb from the seed.
export function initBackendScript(seed) {
  return `
    window.__fb = {
      seed: ${JSON.stringify(seed)},
      firestore: ${JSON.stringify(seed.firestore)},
      auth: { currentUser: null, _listeners: [] },
      uploads: [],
      flags: ${JSON.stringify(seed.flags || {})},
    };
    window.__fb.auth._emit = function () {
      window.__fb.auth._listeners.forEach(function (cb) { cb(window.__fb.auth.currentUser); });
    };
  `;
}

const MOCK_APP = `
  export function initializeApp(config) { return { name: '[DEFAULT]', options: config }; }
`;

const MOCK_AUTH = `
  export const browserLocalPersistence = 'local';
  export function getAuth() { return window.__fb.auth; }
  export function setPersistence() { return Promise.resolve(); }
  export function signInWithEmailAndPassword(auth, email, password) {
    const u = window.__fb.seed.users[email];
    if (!u || u.password !== password) {
      const e = new Error('invalid credentials'); e.code = 'auth/invalid-credential';
      return Promise.reject(e);
    }
    auth.currentUser = {
      uid: u.uid, email: email,
      getIdToken: function () { return Promise.resolve('mock-id-token'); },
    };
    auth._emit();
    return Promise.resolve({ user: auth.currentUser });
  }
  export function onAuthStateChanged(auth, cb) {
    auth._listeners.push(cb);
    Promise.resolve().then(function () { cb(auth.currentUser); });
    return function () {};
  }
  export function signOut(auth) { auth.currentUser = null; auth._emit(); return Promise.resolve(); }
`;

const MOCK_FIRESTORE = `
  export function getFirestore() { return window.__fb.firestore; }
  export function doc(db, coll, id) { return { _coll: coll, _id: id }; }
  export function serverTimestamp() { return { __server: true }; }
  export function getDoc(ref) {
    // Scoped to 'inspectores' so it only affects the auth-boot profile read,
    // not the unrelated pre-submit 'evaluaciones' existence check in form.js.
    if (ref._coll === 'inspectores' && window.__fb.flags.getDocFailQueue && window.__fb.flags.getDocFailQueue.length) {
      const code = window.__fb.flags.getDocFailQueue.shift();
      const e = new Error('mock-getdoc-fail: ' + code);
      e.code = code;
      return Promise.reject(e);
    }
    const c = window.__fb.firestore[ref._coll] || {};
    const d = c[ref._id];
    return Promise.resolve({ exists: function () { return d !== undefined; }, data: function () { return d; } });
  }
  // Minimal query support: collection() + query() + where() build a
  // descriptor; getDocs() filters in-memory docs by dotted-path equality
  // (e.g. 'inspector.uid') — enough for the app's single-field lookups.
  export function collection(db, coll) { return { _coll: coll }; }
  export function where(field, op, value) { return { field: field, op: op, value: value }; }
  export function query(collRef, ...clauses) { return { _coll: collRef._coll, _clauses: clauses }; }
  function getPath(obj, path) {
    return path.split('.').reduce(function (acc, key) { return acc == null ? acc : acc[key]; }, obj);
  }
  export function getDocs(q) {
    const c = window.__fb.firestore[q._coll] || {};
    const docs = Object.keys(c)
      .filter(function (id) {
        const d = c[id];
        return (q._clauses || []).every(function (cl) { return getPath(d, cl.field) === cl.value; });
      })
      .map(function (id) { return { id: id, data: function () { return c[id]; } }; });
    return Promise.resolve({ docs: docs, forEach: function (cb) { docs.forEach(cb); } });
  }
  export function runTransaction(db, fn) {
    const tx = {
      get: function (ref) {
        const c = window.__fb.firestore[ref._coll] || {};
        const d = c[ref._id];
        return Promise.resolve({ exists: function () { return d !== undefined; }, data: function () { return d; } });
      },
      update: function (ref, patch) { Object.assign(window.__fb.firestore[ref._coll][ref._id], patch); },
      set: function (ref, val) {
        if (!window.__fb.firestore[ref._coll]) window.__fb.firestore[ref._coll] = {};
        window.__fb.firestore[ref._coll][ref._id] = val;
      },
    };
    return Promise.resolve().then(function () {
      // One-shot generic write failure, distinct from the create-only
      // duplicate check, so a spec can exercise "photos already uploaded,
      // but the doc write still failed" — the scenario a retry-without-
      // re-upload cache actually protects against.
      if (window.__fb.flags.failTransactionOnce) {
        window.__fb.flags.failTransactionOnce = false;
        throw new Error('mock-transaction-fail');
      }
      return fn(tx);
    });
  }
`;

const MOCK_STORAGE = `
  export function getStorage() { return window.__fb.storage || {}; }
  export function ref(storage, path) { return { _path: path }; }
  export function uploadBytes(ref, file) {
    if (window.__fb.flags.failUpload) return Promise.reject(new Error('storage-fail'));
    window.__fb.uploads.push({ path: ref._path, name: file && file.name, size: file && file.size });
    return Promise.resolve({});
  }
  export function getDownloadURL(ref) { return Promise.resolve('https://mock.storage/' + encodeURIComponent(ref._path)); }
`;

// Register route interceptors for the gstatic module URLs plus the S3 photo
// pipeline (signer + presigned PUT) on a page.
export async function mockFirebase(page, seed = defaultSeed()) {
  // Dashboard assigned-points endpoint (`asignacionesApi` in form.js POSTs
  // here with `{action}`). Ran on Node, NOT in the browser, so it reads the
  // seed directly instead of `window.__fb`. Fails CLOSED on an unknown action
  // (400) so a typo in a new action surfaces instead of silently passing.
  const asig = seed.asignaciones || {};
  await page.route('**/inspector-asignaciones', (route) => {
    const { action } = route.request().postDataJSON() || {};
    if (action === 'misPuntos') return route.fulfill({ json: { ok: true, puntos: asig.stickers || [] } });
    if (action === 'misPuntosPlaneacion') return route.fulfill({ json: { ok: true, puntos: asig.planeacion || [] } });
    if (action === 'puntosCercanosDisponibles') return route.fulfill({ json: { ok: true, puntos: asig.cercanos || [] } });
    if (action === 'tomarPunto') return route.fulfill({ json: { ok: true, asignados: {}, tambien_asignado: false } });
    if (action === 'marcarHecho' || action === 'marcarHechoPlaneacion') return route.fulfill({ json: { ok: true } });
    return route.fulfill({ status: 400, json: { ok: false, detail: `unknown-action:${action}` } });
  });

  const bodies = {
    'firebase-app.js': MOCK_APP,
    'firebase-auth.js': MOCK_AUTH,
    'firebase-firestore.js': MOCK_FIRESTORE,
    'firebase-storage.js': MOCK_STORAGE,
  };
  for (const [file, body] of Object.entries(bodies)) {
    await page.route(GSTATIC + file, (route) =>
      route.fulfill({ contentType: 'application/javascript; charset=utf-8', body }));
  }

  // Photo signer: answers like the real Vercel function, deriving the S3 key
  // from the request body so specs can assert the stored public URL.
  await page.route('https://sismo-fotos-signer.vercel.app/**', (route) => {
    const { idToken, codigo, slot } = route.request().postDataJSON();
    if (!idToken) return route.fulfill({ status: 401, json: { error: 'invalid-token' } });
    const key = `evaluaciones/${codigo}/foto_${slot}.jpg`;
    return route.fulfill({
      json: { uploadUrl: `https://s3.mock/upload/${key}`, publicUrl: `https://s3.mock/${key}` },
    });
  });
  await page.route('https://s3.mock/upload/**', (route) => route.fulfill({ status: 200, body: '' }));
}
