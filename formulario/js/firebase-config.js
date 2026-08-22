// Firebase web config for the ATC-20 field form (same project as the dashboard).
//
// This is NOT a secret: the Firebase web apiKey is a public project identifier.
// Real security comes from (a) the authorized-domains list in the Firebase
// console and (b) the Firestore/Storage security rules (see SETUP.md).

export const firebaseConfig = {
  apiKey: 'AIzaSyDeJjKCGyfu_BSqxKUu4OhHEsUtOLaONyU',
  authDomain: 'sismo-agosto-sgred.firebaseapp.com',
  projectId: 'sismo-agosto-sgred',
  appId: '1:802494899680:web:1258ddebab557655073785',
  messagingSenderId: '802494899680',
  storageBucket: 'sismo-agosto-sgred.firebasestorage.app',
};

// True once real values have been pasted (guards a friendly config message
// instead of a cryptic SDK crash while placeholders are still here).
export const isConfigured = () =>
  !Object.values(firebaseConfig).some(
    (v) => typeof v === 'string' && v.startsWith('PEGA_'),
  );
