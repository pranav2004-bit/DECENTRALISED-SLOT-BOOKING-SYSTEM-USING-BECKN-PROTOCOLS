import { defineConfig, devices } from '@playwright/test';

/**
 * livetracker3.md §5.2 — real, repeatable browser-automation E2E suite for the customer
 * golden path (search -> select -> init -> confirm -> rate -> support -> track), run against
 * the REAL docker-compose stack (bap-web on :3000, bap-backend on :8001, bpp-backend on
 * :8002, beckn-gateway on :8003, registry on :8000) — never mocked, matching this project's
 * established "not mocked, not simulated" E2E discipline (see this tracker's own §3.4).
 *
 * `globalSetup` (./global-setup.ts) does the real onboarding + business/resource/availability
 * seeding via real HTTP calls (and `docker compose exec` for the onboarding management
 * commands) before any test runs — see that file for the full sequence.
 *
 * Long `timeout`: `golden-path.spec.ts`'s one test genuinely waits several real minutes for
 * the seeded booking's slot to reach its real end time plus a reconciliation tick before the
 * booking flips to COMPLETE (see that file's own comment for the exact derivation) — this is
 * a real wait, not a flake to be engineered away, matching TESTING.md's own "Real timing for
 * TTL behavior" precedent for a different feature.
 */
export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  timeout: 15 * 60 * 1000, // 15 minutes — comfortably covers the ~9-10 real minute wait below
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  globalSetup: require.resolve('./global-setup.ts'),
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    // Fixed to UTC so the datetime-local value the test types into /select's own
    // "min = now + 5 minutes" client-side-constrained input can be computed directly from
    // the seeded slot's own UTC ISO timestamp, with no host-machine-timezone ambiguity —
    // datetime-local inputs carry no timezone of their own, the browser's local zone is
    // what the page's `new Date(requestedTime).toISOString()` call implicitly assumes.
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
