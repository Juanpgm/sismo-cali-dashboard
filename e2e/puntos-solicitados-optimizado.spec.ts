import { test, expect } from '@playwright/test';
import { loginAsAdmin, openView } from './_auth';

// Firestore-quota-reduction (Phase 1-3): Puntos Solicitados
// (web/js/puntos_solicitados.js) regression coverage for the backend-search
// modal (`#ps-buscar`) that replaced client-side full-text search, and for
// the Phase 3 trade-off that dropped requester-name matching from that
// backend search (name still displays, but is no longer searchable — see
// `runGuardedBuscar`'s server-side query). Admin-gated: skips cleanly
// without E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD, never fails the run.

const EMAIL = process.env.E2E_ADMIN_EMAIL;
const PASSWORD = process.env.E2E_ADMIN_PASSWORD;

test.describe('Puntos Solicitados — optimización (búsqueda backend)', () => {
  test.skip(!EMAIL || !PASSWORD, 'E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD ausentes — Puntos Solicitados optimizado omitido');

  test('búsqueda backend por término amplio responde sin error', async ({ page }) => {
    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'puntos-solicitados');
    await openView(page, 'puntos-solicitados');

    await page.locator('#ps-buscar').click();
    await expect(page.locator('#ps-buscar-modal')).toHaveClass(/is-open/);

    const requestPromise = page.waitForRequest(/\/puntos-solicitados\/buscar/, { timeout: 15_000 });
    await page.locator('#ps-buscar-input').fill('calle');
    await requestPromise;

    const buscarList = page.locator('#ps-buscar-list');
    await expect(buscarList.locator('li').first()).toBeVisible({ timeout: 15_000 });
    await expect(buscarList.locator('.sticker-error')).toHaveCount(0);

    // Results-nonempty is data-dependent (softened): either real result rows
    // or the documented "Sin resultados" empty state both count as a valid,
    // error-free response — only an error state fails this test.
    const resultRows = buscarList.locator('[data-ps-buscar-usar]');
    const emptyState = buscarList.locator('li.eval-empty');
    expect((await resultRows.count()) > 0 || (await emptyState.count()) > 0).toBe(true);
  });

  test('búsqueda por nombre de solicitante no devuelve resultados (Phase 3)', async ({ page }) => {
    // PROMINENT NOTE: this intentionally codifies the accepted Phase 3
    // trade-off — requester-name search no longer matches server-side (the
    // name still renders in the list/detail, it just isn't indexed for
    // `/puntos-solicitados/buscar`). If name search is ever re-enabled this
    // test MUST fail, on purpose, as the regression signal.
    // ALSO NOTE: this asserts NEW behavior — it fails against a backend that
    // hasn't deployed Phase 3 yet (old code would return matches).
    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'puntos-solicitados');
    await openView(page, 'puntos-solicitados');

    // Try to extract a real requester name from the main list's first row
    // (listItemHtml renders it as `<span class="eval-meta">{nombre_solicitante} · {fotos}</span>`,
    // web/js/puntos_solicitados.js ~line 586) — strongest regression check,
    // since it's a name known to actually exist in the dataset.
    await expect(page.locator('#ps-list li.ps-item').first()).toBeVisible({ timeout: 20_000 });
    const metaTexts = await page.locator('#ps-list li.ps-item').first().locator('.eval-meta').allTextContents();
    // metaTexts[1] is "{comuna} · {barrio}", metaTexts[2] is "{solicitante} · {fotos}".
    const solicitanteMeta = metaTexts[2] || '';
    const extracted = solicitanteMeta.split('·')[0]?.trim();

    // Fallback (weaker signal) if extraction failed or the field is "Sin solicitante".
    const nombreSolicitante =
      extracted && extracted !== 'Sin solicitante' ? extracted : 'María Fernanda González';

    await page.locator('#ps-buscar').click();
    await expect(page.locator('#ps-buscar-modal')).toHaveClass(/is-open/);

    const requestPromise = page.waitForRequest(/\/puntos-solicitados\/buscar/, { timeout: 15_000 });
    await page.locator('#ps-buscar-input').fill(nombreSolicitante);
    await requestPromise;

    const buscarList = page.locator('#ps-buscar-list');
    await expect(buscarList.locator('li.eval-empty')).toBeVisible({ timeout: 15_000 });
    await expect(buscarList.locator('[data-ps-buscar-usar]')).toHaveCount(0);
  });

  test('búsqueda vacía o solo espacios no dispara fetch ni rompe el modal', async ({ page }) => {
    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'puntos-solicitados');
    await openView(page, 'puntos-solicitados');

    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    let buscarFired = false;
    page.on('request', (r) => {
      if (/\/puntos-solicitados\/buscar/.test(r.url())) buscarFired = true;
    });

    await page.locator('#ps-buscar').click();
    await expect(page.locator('#ps-buscar-modal')).toHaveClass(/is-open/);

    await page.locator('#ps-buscar-input').fill('   ');
    await page.waitForTimeout(600); // past the 300ms debounce, per runGuardedBuscar's trim guard

    expect(buscarFired, 'whitespace-only query should no-op the /buscar fetch').toBe(false);
    await expect(page.locator('#ps-buscar-modal')).toHaveClass(/is-open/);
    await expect(page.locator('#ps-buscar-list [data-ps-buscar-usar]')).toHaveCount(0);
    expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
