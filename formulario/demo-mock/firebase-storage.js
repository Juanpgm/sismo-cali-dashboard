// DEMO ONLY — see demo-mock/firebase-app.js. Records uploads, writes nothing.
export function getStorage() { return window.__fb.storage || {}; }
export function ref(storage, path) { return { _path: path }; }
export function uploadBytes(refObj, file) {
  window.__fb.uploads.push({ path: refObj._path, name: file && file.name, size: file && file.size });
  return Promise.resolve({});
}
export function getDownloadURL(refObj) {
  return Promise.resolve('https://demo.local/' + encodeURIComponent(refObj._path));
}
