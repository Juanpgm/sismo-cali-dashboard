import { expect, type Page } from '@playwright/test';

// Shared admin-login + tab-switch helpers for the *-optimizado specs
// (Firestore-quota-reduction Phase 1-3 regressions). NOT a `.spec.ts` file —
// `playwright.config.ts`'s `testMatch: '**/*.spec.ts'` won't collect this,
// so it's safe to import without becoming its own (credential-less) test run.
//
// Login sequence mirrors admin-flow.spec.ts exactly (lines 24-49): fill
// `#auth-email`/`#auth-password`, submit, then race `#auth-error` vs the
// revealed admin tab becoming visible — a wrong/expired credential throws a
// clear Error instead of a confusing later timeout. EMAIL/PASSWORD are never
// logged.

export async function loginAsAdmin(
  page: Page,
  email: string,
  password: string,
  revealedTabView: string,
): Promise<void> {
  await page.goto('/');
  await expect(page.locator('#auth-email')).toBeVisible();
  await page.locator('#auth-email').fill(email);
  await page.locator('#auth-password').fill(password);
  await page.locator('#auth-submit').click();

  const authError = page.locator('#auth-error');
  const revealedTab = page.locator(`.view-tab[data-view="${revealedTabView}"]`);
  await Promise.race([
    revealedTab.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {}),
    authError.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {}),
  ]);
  if (await authError.isVisible()) {
    throw new Error(
      `Firebase login rejected E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD: "${await authError.textContent()}" — credential issue, not a spec/app bug.`,
    );
  }

  await expect(revealedTab).toBeVisible({ timeout: 5_000 });
}

// Clicks a `.view-tab[data-view="<view>"]` and waits for its panel
// (`#view-<view>` / `[data-view-panel="<view>"]`) to lose `hidden` — the
// same attribute `switchView()` (web/js/main.js) toggles on every tab
// switch, which is also what gates the background polls in
// evaluaciones.js/puntos_solicitados.js (`section.closest('[hidden]')`).
export async function openView(page: Page, view: string): Promise<void> {
  await page.locator(`.view-tab[data-view="${view}"]`).click();
  await expect(page.locator(`[data-view-panel="${view}"]`)).not.toBeHidden({ timeout: 15_000 });
}
