import { test, expect } from '@playwright/test';
import { loginAsAdmin, openView } from './_auth';

// Firestore-quota-reduction (Phase 1-3): Planeación regression coverage for
// the snapshot/re-mount optimization work. `switchView()` (web/js/main.js)
// re-mounts (re-inits + re-fetches) planeacion.js every time the tab is
// opened — these cases guard against the caching/dirty-snapshot regressions
// that kind of re-mount is prone to. Admin-gated: skips cleanly without
// E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD, never fails the run.

const EMAIL = process.env.E2E_ADMIN_EMAIL;
const PASSWORD = process.env.E2E_ADMIN_PASSWORD;

test.describe('Planeación — optimización (snapshot/re-mount)', () => {
  test.skip(!EMAIL || !PASSWORD, 'E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD ausentes — Planeación optimizado omitido');

  test('cambios repetidos de pestaña no producen errores de JS', async ({ page }) => {
    const errors: string[] = [];
    const consoleErrors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => {
      if (m.type() === 'error' && !/Failed to load resource|net::|favicon/i.test(m.text())) {
        consoleErrors.push(m.text());
      }
    });

    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'planeacion');
    await openView(page, 'planeacion');

    const crearGrupoBtn = page.locator('#planeacion-grupo-crear');
    const tableWrap = page.locator('#planeacion-table-wrap');
    await expect(crearGrupoBtn).toBeVisible({ timeout: 20_000 });
    await expect(tableWrap.locator('p.sticker-loading')).toHaveCount(0, { timeout: 20_000 });

    for (let i = 0; i < 3; i++) {
      await openView(page, 'panel');
      await expect(page.locator('[data-view-panel="panel"]')).not.toBeHidden();
      await openView(page, 'planeacion');
      await expect(crearGrupoBtn).toBeVisible({ timeout: 20_000 });
      await expect(tableWrap.locator('p.sticker-loading')).toHaveCount(0, { timeout: 20_000 });
    }

    // Uncaught exceptions are a real caching-regression signal — hard assert.
    expect(errors, `uncaught page errors during tab switching: ${errors.join(' | ')}`).toHaveLength(0);
    expect(consoleErrors, `console.error during tab switching: ${consoleErrors.join(' | ')}`).toHaveLength(0);
  });

  test('grupo creado se refleja sin reload y sobrevive al re-mount de la pestaña', async ({ page }) => {
    // Targets the snapshot-marks-dirty behavior: a mutation must show up
    // immediately (client-side optimistic/refresh) AND still be present
    // after the module is fully torn down and re-fetched from scratch
    // (proves the re-fetched snapshot reflects the write, not a stale cache
    // nor a one-off client-only render).
    page.on('dialog', (dialog) => dialog.accept());

    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'planeacion');
    await openView(page, 'planeacion');

    const crearGrupoBtn = page.locator('#planeacion-grupo-crear');
    await expect(crearGrupoBtn).toBeVisible({ timeout: 20_000 });

    const nombreGrupo = `TEST-E2E-PW-${Date.now()}`;
    await crearGrupoBtn.click();
    await expect(page.locator('#planeacion-grupo-modal')).toHaveClass(/is-open/);
    await page.locator('#planeacion-grupo-nombre').fill(nombreGrupo);
    await page.locator('#planeacion-grupo-miembros-list input[type="checkbox"]').first().check();
    await page.locator('#planeacion-grupo-save').click();
    await expect(page.locator('#planeacion-grupo-modal')).not.toHaveClass(/is-open/, { timeout: 15_000 });

    const grupoRow = page.locator('[data-grupo-row]', { hasText: nombreGrupo });
    await expect(grupoRow).toBeVisible({ timeout: 15_000 });

    // Force a full module re-mount (leave the tab, come back) and assert the
    // group survives the fresh snapshot read.
    await openView(page, 'panel');
    await openView(page, 'planeacion');
    await expect(page.locator('#planeacion-grupo-crear')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-grupo-row]', { hasText: nombreGrupo })).toBeVisible({ timeout: 15_000 });

    // Cleanup: throwaway group must not linger in shared production data.
    await page.locator('[data-grupo-row]', { hasText: nombreGrupo }).locator('[data-eliminar-grupo]').click();
    await expect(page.locator('[data-grupo-row]', { hasText: nombreGrupo })).toHaveCount(0, { timeout: 15_000 });
  });

  test('banner de truncación nunca se muestra de forma incorrecta', async ({ page }) => {
    // NOTED DEVIATION: the spec brief asked to assert the banner is never
    // shown, but this production dataset legitimately has ~14.8k puntos
    // pendientes > the 500-point listPuntos limit, so the banner correctly
    // DOES show. Asserting plain absence isn't feasible without a <500-result
    // filter that doesn't exist yet — instead we assert the correctness
    // invariant: hidden is fine, but if visible its text must be well-formed
    // AND N (shown) must be strictly less than M (pendientes).
    await loginAsAdmin(page, EMAIL!, PASSWORD!, 'planeacion');
    await openView(page, 'planeacion');

    await expect(page.locator('#planeacion-grupo-crear')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('#planeacion-table-wrap').locator('p.sticker-loading')).toHaveCount(0, {
      timeout: 20_000,
    });

    const truncEl = page.locator('#planeacion-truncacion');
    if (await truncEl.isHidden()) {
      return; // legitimate: fewer pendientes than the 500-point page limit
    }

    const text = (await truncEl.textContent()) || '';
    const match = text.match(/Mostrando los (\d+) puntos de mayor prioridad de (\d+) pendientes\./);
    expect(match, `truncation banner text malformed: "${text}"`).not.toBeNull();
    const [, shown, pending] = match!;
    expect(Number(shown), `shown (${shown}) must be < pendientes (${pending})`).toBeLessThan(Number(pending));
  });
});
