// Firebase web config for the Sismo Cali dashboard login.
//
// This is NOT a secret: the Firebase web apiKey is a public project identifier.
// Real security comes from (a) the authorized-domains list in the Firebase
// console and (b) enabling only the sign-in providers you want.
//
// HOW TO FILL THIS IN:
//   Firebase console → Project settings (gear) → "Your apps" → Web app →
//   "SDK setup and configuration" → copy the `firebaseConfig` object here.
//
// Then in the console make sure:
//   • Authentication → Sign-in method → Google is ENABLED.
//   • Authentication → Sign-in method → Email/Password is ENABLED
//     (with "Email link" off; you create admin users manually under Users).
//   • Authentication → Settings → Authorized domains includes your Vercel
//     domain (e.g. sismo-cali.vercel.app) and any custom domain.

import { initializeApp, getApps, getApp } from 'https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js';

export const firebaseConfig = {
  apiKey: 'AIzaSyAVVewMgunLWBiZz5XU-GjrzbO3ZKcyvD0',
  authDomain: 'dagma-85aad.firebaseapp.com',
  projectId: 'dagma-85aad',
  appId: '1:716440297451:web:6971b2bb4118f7ea3cc3ae',
  messagingSenderId: '716440297451',
  // La apiKey web NO es secreta (identificador público del proyecto).
};

// Google sign-ins are restricted to this email domain → "viewer" role
// (Panel only). Anything else signing in with Google is rejected.
export const ALLOWED_DOMAIN = 'cali.gov.co';

// True once real values have been pasted (guards a friendly "configura Firebase"
// message instead of a cryptic SDK crash while the placeholders are still here).
export const isConfigured = () =>
  !Object.values(firebaseConfig).some(
    (v) => typeof v === 'string' && v.startsWith('PEGA_'),
  );

// Shared Firebase app instance. auth.js and israel-source.js both need it and
// can run in either order (data loading now starts in parallel with the auth
// SDK boot) — initializeApp() throws if called twice for the default app, so
// this is the one place that's allowed to create it.
export const getFirebaseApp = () => (getApps().length ? getApp() : initializeApp(firebaseConfig));
