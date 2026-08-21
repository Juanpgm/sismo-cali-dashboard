// DEMO ONLY — in-memory Firebase stand-in. Not used in production (index.html
// imports the real SDK from gstatic). demo.html remaps the gstatic URLs here
// via an import map so you can click through the whole flow with no real
// Firebase, no credentials, and no data written anywhere.
export function initializeApp(config) { return { name: '[DEFAULT]', options: config }; }
