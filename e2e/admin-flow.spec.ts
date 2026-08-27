import { test, expect } from '@playwright/test';

// planeacion-flujo-confiable, D3: authenticated admin flow. Skips with a
// clear message when E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD are absent — never
// fails the run, per spec "Authenticated flow runs with credentials, skips
// without them". Firebase password-provider login grants the 'admin' role
// (see web/js/auth.js's own role resolution), which is what reveals the
// Planeación tab (styles.css: `body:not([data-role="admin"]) .view-tab
// [data-view="planeacion"] { display: none }`).
//
// auto-agrupar is deliberately NOT clicked here — it mutates the real,
// shared ~14.8k-point production dataset (proposal's own scope note). Only
// its enabled/disabled state is asserted.

const EMAIL = process.env.E2E_ADMIN_EMAIL;
const PASSWORD = process.env.E2E_ADMIN_PASSWORD;

test.describe('authenticated admin flow', () => {
  test.skip(!EMAIL || !PASSWORD, 'E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD ausentes — flujo autenticado omitido');

  test('crear grupo, vehicle modal, cleanup', async ({ page }) => {
    page.on('dialog', (dialog) => dialog.accept());

    await page.goto('/');
    await expect(page.locator('#auth-email')).toBeVisible();
    await page.locator('#auth-email').fill(EMAIL!);
    await page.locator('#auth-password').fill(PASSWORD!);
    await page.locator('#auth-submit').click();

    // Fail fast with a clear reason when the credentials themselves are
    // wrong/expired, instead of a confusing later timeout on the Planeación
    // tab (never logs EMAIL/PASSWORD — only auth.js's own error text).
    const authError = page.locator('#auth-error');
    const planeacionTab = page.locator('.view-tab[data-view="planeacion"]');
    await Promise.race([
      planeacionTab.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {}),
      authError.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {}),
    ]);
    if (await authError.isVisible()) {
      // A genuine, clearly-reported FAILURE (not test.fail()'s "expected to
      // fail" soft-pass annotation) — a wrong/expired credential should
      // never quietly read as green in the final report.
      throw new Error(
        `Firebase login rejected E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD: "${await authError.textContent()}" — credential issue, not a spec/app bug.`,
      );
    }

    // Role resolution + Planeación tab reveal.
    await expect(planeacionTab).toBeVisible({ timeout: 5_000 });
    await planeacionTab.click();

    const crearGrupoBtn = page.locator('#planeacion-grupo-crear');
    await expect(crearGrupoBtn).toBeVisible({ timeout: 20_000 });

    // ---- crear grupo TEST-E2E-PW, appears WITHOUT a page reload ----------
    const nombreGrupo = `TEST-E2E-PW-${Date.now()}`;
    await crearGrupoBtn.click();
    await expect(page.locator('#planeacion-grupo-modal')).toHaveClass(/is-open/);
    await page.locator('#planeacion-grupo-nombre').fill(nombreGrupo);
    // crearGrupo requires >=1 miembro (planeacion.js's own client-side
    // guard, mirroring the backend) — check the first available inspector.
    await page.locator('#planeacion-grupo-miembros-list input[type="checkbox"]').first().check();
    await page.locator('#planeacion-grupo-save').click();
    await expect(page.locator('#planeacion-grupo-modal')).not.toHaveClass(/is-open/, { timeout: 15_000 });

    const grupoRow = page.locator('[data-grupo-row]', { hasText: nombreGrupo });
    await expect(grupoRow).toBeVisible({ timeout: 15_000 });

    // ---- vehicle modal opens with conductor select populated -------------
    await page.locator('#planeacion-vehiculo-crear').click();
    await expect(page.locator('#planeacion-vehiculo-modal')).toHaveClass(/is-open/);
    const conductorSelect = page.locator('#planeacion-vehiculo-conductor');
    await expect(conductorSelect).toBeVisible();
    await expect(conductorSelect.locator('option')).not.toHaveCount(0);
    await page.locator('[data-vehiculo-close]').first().click();
    await expect(page.locator('#planeacion-vehiculo-modal')).not.toHaveClass(/is-open/);

    // ---- auto-agrupar: assert enabled only, never clicked -----------------
    // It mutates ~14.8k shared production points — out of scope here.
    await expect(page.locator('#planeacion-auto')).toBeEnabled();

    // ---- cleanup: eliminar grupo -------------------------------------------
    await grupoRow.locator('[data-eliminar-grupo]').click();
    await expect(page.locator('[data-grupo-row]', { hasText: nombreGrupo })).toHaveCount(0, { timeout: 15_000 });
  });
});
