import { test, expect } from '@playwright/test';
import { loginAsAdmin, openView } from './_auth';

// Firestore-quota-reduction (Phase 1-3): Evaluaciones (web/js/evaluaciones.js,
// inside the Stickers tab) regression coverage. The Phase 1-3 work gated the
// 5-minute background poll (`AUTO_REFRESH_MS`) behind tab visibility
// (`document.visibilityState === 'hidden' || section.closest('[hidden]')`)
// to cut Firestore reads — these cases guard the render states and that
// poll-gating. Admin-gated: skips cleanly without
// E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD, never fails the run.

const EMAIL = process.env.E2E_ADMIN_EMAIL;
const PASSWORD = process.env.E2E_ADMIN_PASSWORD;

test.describe('Evaluaciones — optimización (poll gating)', () => {
  test.skip(!EMAIL || !PASSWORD, 'E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD ausentes — Evaluaciones optimizado omitido');

  test('renderiza lista o estado vacío, sin excepciones', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'stickers');
    await openView(page, 'stickers');

    const evalSegment = page.locator('[data-sticker-segment="evaluaciones"]');
    if (await evalSegment.count()) {
      const isActive = await evalSegment.evaluate((el) => el.classList.contains('is-active'));
      if (!isActive) await evalSegment.click();
    }

    // Either real data rows or the documented empty state — either way at
    // least one <li> must render (no infinite "loading" limbo).
    await expect(page.locator('#eval-list li').first()).toBeVisible({ timeout: 20_000 });

    await expect(page.locator('#eval-kpis .sticker-error')).toHaveCount(0);
    expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toHaveLength(0);
  });

  test('el sondeo de fondo se pausa con la pestaña oculta (page.clock)', async ({ page }) => {
    // HONEST NOTE: the "visible" side of this assertion is the switchView()
    // re-mount fetch (evaluaciones.js re-inits on every tab open), not
    // strictly the setInterval firing — that's expected and fine, it still
    // proves a fetch resumes once the tab is visible again. page.clock +
    // real timer/interval interaction is the most fragile mechanism in this
    // whole file; the load-bearing assertion is the "no request while
    // hidden" check below, which holds regardless of clock quirks.
    const evalReqs: string[] = [];
    page.on('request', (r) => {
      if (/\/evaluaciones(\?|$)/.test(r.url())) evalReqs.push(r.url());
    });

    await page.clock.install();

    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'stickers');
    await openView(page, 'stickers');

    await expect.poll(() => evalReqs.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
    await expect(page.locator('#eval-list li').first()).toBeVisible({ timeout: 20_000 });

    const hiddenBaseline = evalReqs.length;

    await openView(page, 'panel'); // #view-stickers becomes [hidden] -> poll gated
    await page.clock.fastForward('06:00'); // > one 5-minute AUTO_REFRESH_MS interval

    // Core assertion: no poll request fired while the stickers panel is hidden.
    expect(evalReqs.length, 'a /evaluaciones request fired while the tab was hidden').toBe(hiddenBaseline);

    await openView(page, 'stickers');
    await expect.poll(() => evalReqs.length, { timeout: 20_000 }).toBeGreaterThan(hiddenBaseline);
  });

  test('un 500 del backend degrada la UI sin crashear', async ({ page }) => {
    // Backend-deploy-independent: the 500 is faked via route interception.
    await page.route('**/evaluaciones', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'fallo simulado' }),
      }),
    );

    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'stickers');
    await openView(page, 'stickers');

    const errorEl = page.locator('#eval-kpis .sticker-error[role="alert"]');
    await expect(errorEl).toBeVisible({ timeout: 20_000 });
    await expect(errorEl).toContainText('No se pudieron cargar las evaluaciones');

    expect(errors, `uncaught page errors: ${errors.join(' | ')}`).toHaveLength(0);
  });
});
