import { test, expect } from '@playwright/test';

// planeacion-flujo-confiable, D3: unauthenticated smoke — MUST always run,
// no credentials required. `auth.js`'s login overlay (`#auth-overlay`) is
// injected into the DOM immediately, hidden behind `.is-resolving` until
// Firebase resolves the (absent) session, then revealed — Playwright's
// auto-waiting `toBeVisible()` handles that transition, no manual wait.

test.describe('unauthenticated smoke', () => {
  test('dashboard responds and the login UI renders', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.ok(), `dashboard did not respond OK (status ${response?.status()})`).toBeTruthy();

    await expect(page.locator('#auth-form')).toBeVisible();
    await expect(page.locator('#auth-email')).toBeVisible();
    await expect(page.locator('#auth-password')).toBeVisible();
    await expect(page.locator('#auth-submit')).toBeVisible();
  });
});
