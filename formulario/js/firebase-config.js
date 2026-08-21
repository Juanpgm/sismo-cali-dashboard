// Firebase web config for the ATC-20 field form (same project as the dashboard).
//
// This is NOT a secret: the Firebase web apiKey is a public project identifier.
// Real security comes from (a) the authorized-domains list in the Firebase
// console and (b) the Firestore/Storage security rules (see SETUP.md).

export const firebaseConfig = {
  apiKey: 'AIzaSyAVVewMgunLWBiZz5XU-GjrzbO3ZKcyvD0',
  authDomain: 'dagma-85aad.firebaseapp.com',
  projectId: 'dagma-85aad',
  appId: '1:716440297451:web:6971b2bb4118f7ea3cc3ae',
  messagingSenderId: '716440297451',
  // Verify the exact bucket name in Firebase console -> Storage
  // (older projects use dagma-85aad.appspot.com).
  storageBucket: 'dagma-85aad.firebasestorage.app',
};

// True once real values have been pasted (guards a friendly config message
// instead of a cryptic SDK crash while placeholders are still here).
export const isConfigured = () =>
  !Object.values(firebaseConfig).some(
    (v) => typeof v === 'string' && v.startsWith('PEGA_'),
  );
