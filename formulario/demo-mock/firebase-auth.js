// DEMO ONLY — see demo-mock/firebase-app.js.
export const browserLocalPersistence = 'local';
export function getAuth() { return window.__fb.auth; }
export function setPersistence() { return Promise.resolve(); }

export function signInWithEmailAndPassword(auth, email, password) {
  const u = window.__fb.seed.users[email];
  if (!u || u.password !== password) {
    const e = new Error('invalid credentials'); e.code = 'auth/invalid-credential';
    return Promise.reject(e);
  }
  auth.currentUser = { uid: u.uid, email, getIdToken: () => Promise.resolve('demo-token') };
  auth._emit();
  return Promise.resolve({ user: auth.currentUser });
}

export function onAuthStateChanged(auth, cb) {
  auth._listeners.push(cb);
  Promise.resolve().then(() => cb(auth.currentUser));
  return () => {};
}

export function signOut(auth) { auth.currentUser = null; auth._emit(); return Promise.resolve(); }
