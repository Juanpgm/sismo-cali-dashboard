import { defineConfig, devices } from '@playwright/test';

const PORT = 4321;

// Field form runs on phones — test under a mobile viewport with geolocation
// permission granted (the app reads navigator.geolocation on boot).
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.js',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    permissions: ['geolocation'],
    geolocation: { latitude: 3.4516, longitude: -76.532 }, // Cali
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'mobile-chrome', use: { ...devices['Pixel 5'] } },
  ],
  webServer: {
    command: 'node e2e/static-server.mjs',
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    env: { PORT: String(PORT) },
  },
});
