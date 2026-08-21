// Self-check for the pure validators in api/stickers.js. Run: node api/stickers.test.js
const assert = require('assert');
const s = require('./stickers.js');

// cedula: digits only, length 5–12
assert.strictEqual(s.isValidCedula('1020735324'), true);
assert.strictEqual(s.isValidCedula('12345'), true);
assert.strictEqual(s.isValidCedula('1234'), false);
assert.strictEqual(s.isValidCedula('abc123'), false);
assert.strictEqual(s.isValidCedula(''), false);

// codigo: exactly 3 digits
assert.strictEqual(s.isValidCodigo('004'), true);
assert.strictEqual(s.isValidCodigo('4'), false);
assert.strictEqual(s.isValidCodigo('0040'), false);

// password: Firebase minimum 6 chars
assert.strictEqual(s.isValidPassword('Cali2026+-'), true);
assert.strictEqual(s.isValidPassword('12345'), false);
assert.strictEqual(s.isValidPassword(undefined), false);

// email round-trip
assert.strictEqual(s.cedulaToEmail(' 1020735324 '), '1020735324@sismocali.gov.co');
assert.strictEqual(s.emailToCedula('1020735324@sismocali.gov.co'), '1020735324');

console.log('stickers.test.js OK');
