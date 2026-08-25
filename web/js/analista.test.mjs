// Self-check for withTimeout(), the per-source hang guard in analista.js's
// fetch orchestration. Run: node web/js/analista.test.mjs
//
// analista.js transitively imports the Firebase SDK via auth.js/data.js/
// israel-source.js -> firebase-config.js -> bare https:// specifiers, which
// Node's ESM loader can't resolve on its own (same root cause documented in
// data.test.mjs for data.js). Unlike data.js's pure helpers, withTimeout is a
// correction-transaction addition scoped to analista.js only (it must not
// move to utils.js in this pass), so a tiny in-file loader hook stubs the
// three gstatic specifiers actually reached by analista.js's import graph
// with no-op named exports, letting the REAL analista.js module load so the
// REAL withTimeout is what's under test.
// ponytail: this hook's stub export list is hand-matched to analista.js's
// current transitive imports; if a future import chain reaches a firebase-*
// export not listed here, the test fails loudly (unresolved export) rather
// than silently — extend STUB_EXPORTS or extract withTimeout to utils.js
// (like bustParams/suspensionServicios) if this friction grows.
import assert from 'node:assert/strict';
import { register } from 'node:module';

const STUB_EXPORTS = {
  'firebase-app.js': ['initializeApp', 'getApps', 'getApp'],
  'firebase-auth.js': [
    'getAuth', 'setPersistence', 'browserLocalPersistence', 'GoogleAuthProvider',
    'signInWithPopup', 'signInWithEmailAndPassword', 'onAuthStateChanged', 'signOut',
  ],
  'firebase-firestore.js': ['getFirestore', 'collection', 'getDocs'],
};

const hookSource = `
export async function load(url, context, next) {
  if (url.startsWith('https://www.gstatic.com/firebasejs/')) {
    const stubs = ${JSON.stringify(STUB_EXPORTS)};
    const file = Object.keys(stubs).find((name) => url.endsWith(name));
    const source = (stubs[file] || []).map((n) => \`export function \${n}() {}\`).join('\\n');
    return { format: 'module', source, shortCircuit: true };
  }
  return next(url, context);
}`;
register(`data:text/javascript,${encodeURIComponent(hookSource)}`, import.meta.url);

const { withTimeout } = await import('./analista.js');

// A promise that resolves before the timeout window resolves normally.
{
  const value = await withTimeout(Promise.resolve('ok'), 50, 'fast');
  assert.equal(value, 'ok');
}

// A promise that never resolves within the timeout window rejects with a
// timeout error, and does so within roughly the expected window (not hanging
// forever, not firing wildly late).
{
  const never = new Promise(() => {});
  const start = Date.now();
  await assert.rejects(
    withTimeout(never, 50, 'stuck-source'),
    /timeout: stuck-source/,
  );
  const elapsed = Date.now() - start;
  assert.ok(elapsed < 500, `timeout fired too late: ${elapsed}ms`);
}

console.log('ok — analista.js withTimeout');
