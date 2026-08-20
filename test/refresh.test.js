// Self-check for the Firebase ID-token verification in refresh.js.
// Run: node api/refresh.test.js   (no framework, no network — fetch is stubbed).
//
// Generates a throwaway RSA keypair, signs tokens with it, stubs the Google
// certs endpoint to return that key, and asserts the verifier accepts a valid
// admin token and rejects tampering / bad claims / wrong provider.

const crypto = require('crypto');
const assert = require('assert');

const PROJECT = 'test-proj';
const KID = 'testkid';
const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const PUB_PEM = publicKey.export({ type: 'spki', format: 'pem' });

const b64url = (buf) =>
  Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

function makeToken(payload, { kid = KID, key = privateKey } = {}) {
  const header = b64url(JSON.stringify({ alg: 'RS256', kid }));
  const body = b64url(JSON.stringify(payload));
  const signer = crypto.createSign('RSA-SHA256');
  signer.update(`${header}.${body}`);
  signer.end();
  const sig = b64url(signer.sign(key));
  return `${header}.${body}.${sig}`;
}

function validClaims(over = {}) {
  const now = Math.floor(Date.now() / 1000);
  return {
    aud: PROJECT,
    iss: `https://securetoken.google.com/${PROJECT}`,
    iat: now - 10,
    exp: now + 3600,
    firebase: { sign_in_provider: 'password' },
    ...over,
  };
}

// Stub the certs fetch to hand back our test public key under KID.
global.fetch = async () => ({ json: async () => ({ [KID]: PUB_PEM }) });

const { verifyFirebaseToken } = require('../api/refresh.js');

async function expectReject(token, why) {
  await assert.rejects(() => verifyFirebaseToken(token, PROJECT), why);
}

(async () => {
  // Happy path.
  const ok = await verifyFirebaseToken(makeToken(validClaims()), PROJECT);
  assert.equal(ok.firebase.sign_in_provider, 'password');

  // Tampered signature (flip the body but keep old signature).
  const t = makeToken(validClaims());
  const [h, , s] = t.split('.');
  const forged = `${h}.${b64url(JSON.stringify(validClaims({ aud: PROJECT })))}.${s}xx`;
  await expectReject(forged, /firma|malformado/);

  // Wrong audience, wrong issuer, expired.
  await expectReject(makeToken(validClaims({ aud: 'other' })), /aud/);
  await expectReject(makeToken(validClaims({ iss: 'https://evil/' })), /iss/);
  await expectReject(makeToken(validClaims({ exp: Math.floor(Date.now() / 1000) - 5 })), /expirado/);

  // Unknown signing key (kid not in certs).
  await expectReject(makeToken(validClaims(), { kid: 'nope' }), /kid/);

  // Signed by a different key than the published cert.
  const other = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 }).privateKey;
  await expectReject(makeToken(validClaims(), { key: other }), /firma/);

  // Note: the "password" provider check lives in the handler, not the verifier;
  // the verifier only returns claims. A google.com token still verifies here.
  const g = await verifyFirebaseToken(makeToken(validClaims({ firebase: { sign_in_provider: 'google.com' } })), PROJECT);
  assert.equal(g.firebase.sign_in_provider, 'google.com');

  console.log('ok — refresh token verification self-check passed');
})().catch((e) => { console.error('FAIL:', e); process.exit(1); });
