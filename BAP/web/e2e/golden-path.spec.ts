import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * livetracker3.md §5.2 — the real, repeatable Playwright golden-path suite: search -> select
 * -> init -> confirm -> (real wait for completion) -> rate -> support -> track, run against
 * the real docker-compose stack (see e2e/playwright.config.ts / e2e/global-setup.ts).
 *
 * One continuous test, not several independent ones — matching the tracker's own §3.4 "one
 * continuous session" precedent this suite automates. The zero-result search assertion at the
 * very top is not just UX coverage: it is the regression-proof lever this Test Gate names —
 * with `filter_catalog()` (BPP/backend/core/catalog.py) disabled/reverted to a no-op, the
 * unrelated query below would wrongly return the whole (non-empty, thanks to global-setup's
 * own seeded resource) catalog instead of zero results, and this assertion is what would fail.
 */

interface SeedData {
  businessName: string;
  itemName: string;
  query: string;
  nonsenseQuery: string;
  resourceId: string;
  slotStartIso: string;
  slotEndIso: string;
  supportContact: string;
}

const seed: SeedData = JSON.parse(
  readFileSync(path.join(__dirname, '.seed-data.json'), 'utf-8')
);

// Same override global-setup.ts's docker-compose.e2e.yml applies to bpp-backend — kept as one
// named constant so a change to one doesn't silently drift from the other. Buffer padding
// (+10s) absorbs one extra reconciliation tick's worth of scheduling jitter.
const RECONCILIATION_INTERVAL_SECONDS = Number(process.env.E2E_RECONCILIATION_INTERVAL_SECONDS ?? 15);
const RECONCILIATION_POLL_MS = (RECONCILIATION_INTERVAL_SECONDS + 10) * 1000;
const MAX_COMPLETION_POLL_ATTEMPTS = 10; // 10 * ~25s ceiling well beyond one real sweep tick

function isoToDatetimeLocalUtc(iso: string): string {
  // The browser context is pinned to timezoneId: 'UTC' (playwright.config.ts), so a
  // datetime-local value built from the ISO string's own UTC components is exactly what the
  // page's `new Date(requestedTime).toISOString()` call (app/select/page.tsx) will re-derive.
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(
    d.getUTCHours()
  )}:${pad(d.getUTCMinutes())}`;
}

test.describe('BAP customer golden path (livetracker3.md §5.2)', () => {
  test('search -> select -> init -> confirm -> complete -> rate -> support -> track', async ({
    page,
  }) => {
    // --- Step 0: regression-proof negative case ---
    // A query that matches nothing real must return zero results, not an error and not the
    // whole catalog. This is the exact behavior §1.1's real, live `filter_catalog()` provides
    // and the thing a reverted/no-op filter would break.
    // Search's own poll (usePoll, app/search/page.tsx) only stops early if `until` says so —
    // this page's `until` is hardcoded `() => false`, so it always runs the full 12 attempts *
    // 1.5s (~18s) before flipping `loading` false, regardless of how quickly the real answer
    // actually arrives. SEARCH_UI_TIMEOUT_MS gives real margin above that fixed ~18s ceiling —
    // found necessary live: an initial run on this same machine had the real backend answer
    // (confirmed correct and empty via a direct API check) ready well within 18s, but the
    // browser's own JS timers lagged under real host CPU contention (concurrent docker
    // builds/npm installs during that specific run) enough to blow a tighter 30s assertion
    // window — a real environment-timing margin issue, not a functional defect either in the
    // filter or in this test's own logic.
    const SEARCH_UI_TIMEOUT_MS = 60_000;

    await page.goto('/search');
    await page.getByLabel('What are you looking for?').fill(seed.nonsenseQuery);
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByRole('heading', { name: 'No providers found' })).toBeVisible({
      timeout: SEARCH_UI_TIMEOUT_MS,
    });

    // --- Step 1: a real search for the seeded resource ---
    await page.getByRole('button', { name: 'New search' }).click();
    await page.getByLabel('What are you looking for?').fill(seed.query);
    await page.getByRole('button', { name: 'Search' }).click();
    await expect(page.getByText(seed.itemName, { exact: true })).toBeVisible({
      timeout: SEARCH_UI_TIMEOUT_MS,
    });
    await expect(page.getByText(seed.businessName, { exact: true })).toBeVisible();

    // --- Step 2: select the seeded slot's own start time ---
    await page.getByRole('link', { name: 'Choose a time' }).click();
    await expect(page).toHaveURL(/\/select\?/);
    await page.locator('#requested-time').fill(isoToDatetimeLocalUtc(seed.slotStartIso));
    await page.getByRole('button', { name: 'Reserve this slot' }).click();
    await expect(page.getByRole('link', { name: 'Review order' })).toBeVisible({
      timeout: 30_000,
    });

    // --- Step 3: init (automatic on page load) + confirm ---
    await page.getByRole('link', { name: 'Review order' }).click();
    await expect(page).toHaveURL(/\/confirm\?/);
    await expect(page.getByRole('button', { name: 'Confirm booking' })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole('button', { name: 'Confirm booking' }).click();
    await expect(page).toHaveURL(/\/bookings\//, { timeout: 30_000 });

    // --- Step 4: booking lands ACTIVE — no rating prompt yet ---
    await expect(page.getByText('Confirmed', { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('group', { name: 'Rate this booking' })).toHaveCount(0);

    // --- Step 5: the real wait ---
    // A booking only ever reaches Booking.Status.COMPLETE via the real, unmodified
    // sweep_completed_bookings() background sweep (shared/inventory_core/reconciliation.py),
    // which only completes an ACTIVE booking whose slot's end_time has genuinely, actually
    // passed real wall-clock time — there is no management-command or fixture shortcut, by
    // this project's own established "not mocked, not simulated" discipline (see TESTING.md's
    // "Real timing for TTL behavior" section for the same philosophy applied elsewhere). This
    // test therefore performs a REAL sleep of several real minutes here — this is intentional,
    // not a flake to be optimized away. Total real wait is bounded by:
    //   (seeded slot's end_time - now) + up to MAX_COMPLETION_POLL_ATTEMPTS reconciliation
    //   ticks of RECONCILIATION_INTERVAL_SECONDS each (docker-compose.e2e.yml overrides this
    //   to 15s for bpp-backend, down from the real 60s dev/prod default, for this job only).
    const waitForSlotEndMs = Math.max(0, new Date(seed.slotEndIso).getTime() - Date.now());
    // eslint-disable-next-line no-console
    console.log(
      `[e2e] waiting ${Math.round(waitForSlotEndMs / 1000)}s for the slot's real end time ` +
        `(${seed.slotEndIso}), then polling for COMPLETE...`
    );
    await page.waitForTimeout(waitForSlotEndMs);

    let completed = false;
    for (let attempt = 0; attempt < MAX_COMPLETION_POLL_ATTEMPTS; attempt++) {
      await page.reload();
      const completedBadge = page.getByText('Completed', { exact: true });
      if (await completedBadge.isVisible({ timeout: 12_000 }).catch(() => false)) {
        completed = true;
        break;
      }
      await page.waitForTimeout(RECONCILIATION_POLL_MS);
    }
    expect(completed, 'booking never reached COMPLETE via the real reconciliation sweep').toBe(
      true
    );

    // --- Step 6: rate ---
    await expect(page.getByRole('group', { name: 'Rate this booking' })).toBeVisible();
    await page.getByRole('button', { name: 'Rate 5 stars' }).click();
    await expect(page.getByText('Thanks — you rated this 5 stars.')).toBeVisible({
      timeout: 30_000,
    });

    // --- Step 7: support ---
    await page.getByRole('button', { name: 'Get support' }).click();
    await expect(page.getByText(`Email: ${seed.supportContact}`)).toBeVisible({ timeout: 30_000 });

    // --- Step 8: track ---
    // Beauty (this domain) has no live feed by design (protocol_compliance_notes_v1.1.md's
    // Tracking.yaml grounding) — the honest "inactive" response is the correct, expected
    // outcome here, not a partial result.
    await page.getByRole('button', { name: 'Check status' }).click();
    await expect(page.getByText('No live tracking update to show right now.')).toBeVisible({
      timeout: 30_000,
    });
  });
});
