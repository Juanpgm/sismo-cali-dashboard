// DEMO ONLY — see demo-mock/firebase-app.js.
export function getFirestore() { return window.__fb.firestore; }
export function doc(db, coll, id) { return { _coll: coll, _id: id }; }
export function serverTimestamp() { return { __server: true }; }

export function getDoc(ref) {
  const c = window.__fb.firestore[ref._coll] || {};
  const d = c[ref._id];
  return Promise.resolve({ exists: () => d !== undefined, data: () => d });
}

export function runTransaction(db, fn) {
  const tx = {
    get: (ref) => {
      const c = window.__fb.firestore[ref._coll] || {};
      const d = c[ref._id];
      return Promise.resolve({ exists: () => d !== undefined, data: () => d });
    },
    update: (ref, patch) => { Object.assign(window.__fb.firestore[ref._coll][ref._id], patch); },
    set: (ref, val) => {
      if (!window.__fb.firestore[ref._coll]) window.__fb.firestore[ref._coll] = {};
      window.__fb.firestore[ref._coll][ref._id] = val;
    },
  };
  return Promise.resolve().then(() => fn(tx));
}
