import { test, expect } from '@playwright/test';

// planeacion-flujo-confiable, D3: Survey123 connectivity ONLY — no
// submission, ever. Hardcodes the known share-URL base (not read from
// SURVEY123_FORM_URL/env — this spec exists specifically to prove the
// public share link itself is reachable) plus a dummy `field:codigoapp`,
// mirroring `app/services/survey_link.py:build_survey_urls`'s own
// `{form_url}?field:codigoapp=<clave>` shape (web variant).

const SURVEY123_BASE = 'https://survey123.arcgis.com/share/082c0446a4334038b3f8e677bcc27074';
const DUMMY_CODIGOAPP = 'PLN-TEST-00000000';
const SURVEY123_URL = `${SURVEY123_BASE}?field:codigoapp=${DUMMY_CODIGOAPP}`;

test.describe('Survey123 connectivity (read-only, no submission)', () => {
  test('prefilled URL responds 200; the codigoapp param is carried on the request', async ({ request }) => {
    // The requested URL carries the param — asserted on the exact string we
    // are about to GET, before any redirect ArcGIS may issue.
    expect(SURVEY123_URL).toContain(`field:codigoapp=${DUMMY_CODIGOAPP}`);

    const response = await request.get(SURVEY123_URL, { maxRedirects: 10 });

    expect(response.status(), `expected 200, got ${response.status()} for final URL ${response.url()}`).toBe(200);
    // GET only, no `request.post`/form submission anywhere in this spec.
  });
});
