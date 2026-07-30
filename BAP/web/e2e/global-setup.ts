import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

/**
 * livetracker3.md §5.2 — real setup step run once before the Playwright suite: ensures BAP,
 * BPP, and beckn-gateway are genuinely SUBSCRIBED with the real Registry (a real Ed25519 trust
 * handshake, not mocked — BPP's own `verify_participant_signature()` does a real Registry
 * Lookup and hard-requires `status == "SUBSCRIBED"` for whichever subscriber_id signed the
 * inbound request, confirmed by reading BPP/backend/core/trust.py, so Gateway itself, not just
 * BAP/BPP, must reach real SUBSCRIBED status or every relayed action is rejected 401), then
 * seeds one real, distinctively-named business + resource + availability slot on the live BPP
 * stack via real HTTP calls — NOT through BPP's own web UI, since this Test Gate is about the
 * *customer* golden path, not the business dashboard.
 *
 * **Real, live-reproduced fragility found while building this suite, fixed here rather than
 * routed around:** re-onboarding beckn-gateway against an already-running stack hit a real,
 * reproducible failure cascade the first time — Registry's `handle_subscribe()` synchronously
 * dispatches the on_subscribe challenge back to the participant's own callback URL before
 * responding (registry/core/registry_service.py's `_dispatch_on_subscribe_challenge`); on a
 * cold first exercise of that exact code path in this long-running container, the round trip
 * was slow enough to blow Registry's own gunicorn worker timeout (`WORKER TIMEOUT` -> SIGKILL
 * -> worker restart -> Registry regenerates an ephemeral signing/encryption keypair), and the
 * resulting failure tripped Gateway's own Redis-backed circuit breaker to Registry
 * (`gateway-registry-client:cb:*` in gateway-cache — confirmed directly), which then fast-
 * failed every immediate retry for its ~130s cooldown, making the underlying problem look like
 * a permanent deadlock. **It is not**: once the circuit breaker's cooldown genuinely elapsed
 * and a clean attempt was made with nothing else in flight, `onboarding_subscribe` completed
 * and Registry's own `Participant.status` (the authoritative record BPP's trust check actually
 * reads) correctly reached `SUBSCRIBED` — confirmed live, and confirmed that `/search` worked
 * again immediately after. `ensureOnboarded()` below builds in a bounded retry-with-backoff for
 * exactly this transient failure mode, since a genuinely fresh CI stack's first-ever exercise
 * of this code path is precisely the "cold" scenario that triggered it here.
 *
 * **Second, distinct real bug found in the same investigation, NOT worked around, reported as-
 * is:** beckn-gateway's own local onboarding-state file (beckn-gateway/core/onboarding_state.py
 * — a plain JSON file, since Gateway is deliberately stateless/DB-less) never actually reflects
 * `SUBSCRIBED` even after Registry's own real Participant record correctly does — confirmed by
 * querying it directly immediately after a verified-successful subscribe; it stayed at
 * `UNDER_SUBSCRIPTION` indefinitely. RUNBOOK.md documents this exact bug class ("local
 * OnboardingStatus row can get stuck at UNDER_SUBSCRIPTION") as found and FIXED for BAP/BPP's
 * own Django-model-backed `onboarding_service.py` — that fix was evidently never extended to
 * Gateway's separate, file-backed implementation. Harmless for this suite's own purposes (only
 * Registry's real Participant record gates trust, confirmed above), which is why the status
 * check below queries Registry directly instead of trusting any participant's own local
 * mirror — but a real, live, previously-undocumented gap worth a dedicated fix outside this
 * task's own scope.
 *
 * - `docker compose exec <service> python manage.py onboarding_approve/onboarding_subscribe
 *   <domain>` is the real one-time trust bootstrap (management commands identical in shape
 *   across all three apps).
 * - Business signup/login/resource/availability are real, CSRF-protected (except the two
 *   `@csrf_exempt` write endpoints below) BPP endpoints — BPP/backend/core/views.py.
 */

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const DOMAIN = 'ONDC:RET13'; // settings.DOMAIN_BEAUTY — see BPP/backend/bpp/settings.py
const BPP_BASE_URL = process.env.BPP_BASE_URL || 'http://localhost:8002';

// How far in the future the seeded slot starts, and how long it runs. This is the one
// deliberately-real-time-dependent constant in this whole suite: a booking only ever reaches
// Booking.Status.COMPLETE via the real background reconciliation sweep
// (shared/inventory_core/reconciliation.py::sweep_completed_bookings), which only completes a
// booking once its slot's real end_time has genuinely passed — there is no fixture/shortcut.
// 8 minutes gives a real ~3-minute safety margin over /select's own client-side
// `min = now + 5 minutes` constraint (the golden path spends some real seconds/tens-of-seconds
// on the zero-result search assertion and the real search before it ever reaches /select), and
// a short (1 minute) slot duration keeps the total real wait for COMPLETE close to the
// theoretical minimum. See golden-path.spec.ts's own comment for the full wait-time derivation
// once this file's numbers reach that point.
const SLOT_START_OFFSET_MINUTES = 8;
const SLOT_DURATION_MINUTES = 1;

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

class CookieJar {
  private cookies = new Map<string, string>();

  absorb(headers: Headers): void {
    // Node 22's undici `Headers` exposes every real `Set-Cookie` line via `getSetCookie()` —
    // `headers.get('set-cookie')` would silently fold multiple cookies into one comma-joined
    // string and corrupt attribute parsing, so this is not just a style preference.
    const raw = (headers as Headers & { getSetCookie?: () => string[] }).getSetCookie?.() ?? [];
    for (const line of raw) {
      const pair = line.split(';', 1)[0];
      const eq = pair.indexOf('=');
      if (eq > -1) this.cookies.set(pair.slice(0, eq).trim(), pair.slice(eq + 1).trim());
    }
  }

  get(name: string): string | undefined {
    return this.cookies.get(name);
  }

  header(): string {
    return Array.from(this.cookies.entries())
      .map(([k, v]) => `${k}=${v}`)
      .join('; ');
  }
}

async function bpp(
  jar: CookieJar,
  method: string,
  urlPath: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {}
): Promise<Response> {
  const resp = await fetch(`${BPP_BASE_URL}${urlPath}`, {
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      Cookie: jar.header(),
      ...extraHeaders,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  jar.absorb(resp.headers);
  return resp;
}

// Maps each service to the real `subscriber_id` docker-compose.e2e.yml gives it — matches
// that overlay's own SUBSCRIBER_ID values exactly (see its header comment). Deliberately
// coupled: this whole suite only runs against the stack brought up with that overlay.
const SUBSCRIBER_IDS: Record<string, string> = {
  'bap-backend': 'bap-backend.local',
  'bpp-backend': 'bpp-backend.local',
  'beckn-gateway': 'beckn-gateway.local',
};

/** Queries Registry's own authoritative `Participant.status` directly, rather than trusting
 * any participant's own local onboarding-state mirror (BAP/BPP: a real Django model; Gateway:
 * a plain JSON file — see this file's own top docstring for the real, confirmed bug in
 * Gateway's copy of that mirror). Registry's record is what actually gates trust — BPP's
 * `verify_participant_signature()` (BPP/backend/core/trust.py) does a real Lookup against
 * this exact table and hard-requires `SUBSCRIBED` — so this is the only status worth reading. */
function currentOnboardingStatus(service: string, domain: string): string {
  const subscriberId = SUBSCRIBER_IDS[service];
  const pyCode = `from core.models import Participant; p = Participant.objects.filter(subscriber_id='${subscriberId}', domain='${domain}').first(); print(p.status if p else 'NOT_STARTED')`;
  try {
    const out = execFileSync(
      'docker',
      ['compose', 'exec', '-T', 'registry', 'python', 'manage.py', 'shell', '-c', pyCode],
      { cwd: REPO_ROOT, encoding: 'utf8' }
    );
    return out.trim().split('\n').pop() ?? 'UNKNOWN';
  } catch {
    return 'UNKNOWN';
  }
}

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Bounded retry-with-backoff for the transient failure mode documented in this file's own top
// comment: a cold first exercise of Registry's synchronous nested on_subscribe dispatch can be
// slow enough to trip Gateway's own circuit breaker to Registry, which then fast-fails for its
// own ~130s cooldown. 3 attempts with a real 45s gap comfortably clears that cooldown even on
// the least favorable timing, without masking a genuine, persistent failure (which would still
// exhaust all 3 attempts and raise below).
const ONBOARD_MAX_ATTEMPTS = 3;
const ONBOARD_RETRY_DELAY_MS = 45_000;

async function ensureOnboarded(service: string, domain: string): Promise<void> {
  const before = currentOnboardingStatus(service, domain);
  if (before === 'SUBSCRIBED') {
    console.log(`[e2e-setup] ${service} already SUBSCRIBED for ${domain} (per Registry) — skipping.`);
    return;
  }

  for (let attempt = 1; attempt <= ONBOARD_MAX_ATTEMPTS; attempt++) {
    console.log(
      `[e2e-setup] onboarding ${service} for ${domain}, attempt ${attempt}/${ONBOARD_MAX_ATTEMPTS}...`
    );
    try {
      execFileSync(
        'docker',
        ['compose', 'exec', '-T', service, 'python', 'manage.py', 'onboarding_approve', domain],
        { cwd: REPO_ROOT, stdio: 'inherit' }
      );
      execFileSync(
        'docker',
        ['compose', 'exec', '-T', service, 'python', 'manage.py', 'onboarding_subscribe', domain],
        { cwd: REPO_ROOT, stdio: 'inherit' }
      );
    } catch (err) {
      // A failed attempt is expected/tolerated here (see the cold-start/circuit-breaker
      // finding above) — what matters is Registry's own status check right after, not
      // whether this particular command exited cleanly.
      console.warn(`[e2e-setup] attempt ${attempt} for ${service} raised: ${(err as Error).message}`);
    }

    const after = currentOnboardingStatus(service, domain);
    if (after === 'SUBSCRIBED') {
      console.log(`[e2e-setup] ${service} is now SUBSCRIBED for ${domain} (per Registry).`);
      return;
    }
    if (attempt < ONBOARD_MAX_ATTEMPTS) {
      console.log(
        `[e2e-setup] ${service} status is ${after} after attempt ${attempt} — waiting ` +
          `${ONBOARD_RETRY_DELAY_MS / 1000}s (real cooldown for any tripped circuit breaker) before retrying...`
      );
      await sleepMs(ONBOARD_RETRY_DELAY_MS);
    }
  }

  throw new Error(
    `[e2e-setup] ${service} failed to reach SUBSCRIBED for ${domain} after ${ONBOARD_MAX_ATTEMPTS} ` +
      'attempts (checked against Registry\'s own Participant record). Real Registry trust ' +
      'handshake did not complete — check container logs.'
  );
}

function isoUtcDateBounds(target: Date): { rangeStart: string; rangeEnd: string; timeOfDay: string } {
  const y = target.getUTCFullYear();
  const m = target.getUTCMonth();
  const d = target.getUTCDate();
  const rangeStart = new Date(Date.UTC(y, m, d, 0, 0, 0));
  const rangeEnd = new Date(Date.UTC(y, m, d, 23, 59, 59));
  const pad = (n: number) => String(n).padStart(2, '0');
  const timeOfDay = `${pad(target.getUTCHours())}:${pad(target.getUTCMinutes())}`;
  return { rangeStart: rangeStart.toISOString(), rangeEnd: rangeEnd.toISOString(), timeOfDay };
}

async function seedBusinessAndResource(): Promise<SeedData> {
  const jar = new CookieJar();
  const runId = randomBytes(4).toString('hex'); // uniqueness across runs, avoids colliding
  // with any other seeded/demo data already sitting in this domain's catalog.
  const businessName = `Playwright E2E Salon ${runId}`;
  const itemName = `Playwright E2E Haircut ${runId}`;
  const contact = `pw-e2e-${runId}@example.com`;
  const password = `Playwright-E2E-Pass-${runId}!`; // pragma: allowlist secret

  // --- csrf cookie (required for signup/login, neither of which is @csrf_exempt) ---
  await bpp(jar, 'GET', '/api/v1/auth/csrf');
  const csrfToken = jar.get('csrftoken');
  if (!csrfToken) throw new Error('[e2e-setup] did not receive a csrftoken cookie from BPP');

  // --- signup ---
  const signupResp = await bpp(
    jar,
    'POST',
    '/api/v1/auth/signup',
    { business_name: businessName, contact, password, domain_code: DOMAIN },
    { 'X-CSRFToken': csrfToken }
  );
  if (signupResp.status !== 201) {
    throw new Error(`[e2e-setup] business signup failed: ${signupResp.status} ${await signupResp.text()}`);
  }

  // --- login (establishes the real session cookie subsequent owner-only calls need) ---
  const loginResp = await bpp(
    jar,
    'POST',
    '/api/v1/auth/login',
    { contact, password },
    { 'X-CSRFToken': csrfToken }
  );
  if (loginResp.status !== 200) {
    throw new Error(`[e2e-setup] business login failed: ${loginResp.status} ${await loginResp.text()}`);
  }

  // --- resource (a Beauty "stylist" — the only two allowed by BeautyDomainAdapter are
  // "stylist"/"chair", confirmed directly against BPP/backend/core/beauty_adapter.py's
  // `validate_resource_domain_data`) ---
  const resourceResp = await bpp(jar, 'POST', '/api/v1/resources', {
    name: itemName,
    domain_data: { resource_type: 'stylist' },
    price_currency: 'INR',
    price_value: '500.00',
  });
  if (resourceResp.status !== 201) {
    throw new Error(`[e2e-setup] resource create failed: ${resourceResp.status} ${await resourceResp.text()}`);
  }
  const { id: resourceId } = (await resourceResp.json()) as { id: string };

  // --- availability -> real generated Slot ---
  // AvailabilityCalendar's real fields, confirmed by directly reading
  // resource_availability_create_view (BPP/backend/core/views.py) and
  // AvailabilityCalendar.generate_slots() (shared/inventory_core/models.py): `range_start`/
  // `range_end` bound which *calendar days* the rule applies to (day granularity, not a
  // sub-day cutoff — see that method's own comment); `days` is a comma-separated weekday
  // filter, blank = every day; `times` is a list of "HH:MM" time-of-day anchors, each combined
  // with every allowed day's own date to produce one Slot per anchor per day.
  // `frequency_days`/`slot_duration_minutes`/`slot_capacity` map directly to
  // `frequency`(timedelta)/`slot_duration`(timedelta)/`slot_capacity` — there is no hidden
  // translation step beyond that direct field-by-field pass-through in the view.
  //
  // The target start time is pinned to *its own* calendar day's 00:00:00-23:59:59 UTC range
  // (rather than "now" .. "now + 1 day") so the day used to combine with `times` is
  // unambiguously the target's own date regardless of exactly when "now" is evaluated.
  const target = new Date(Date.now() + SLOT_START_OFFSET_MINUTES * 60_000);
  const { rangeStart, rangeEnd, timeOfDay } = isoUtcDateBounds(target);

  const availabilityResp = await bpp(jar, 'POST', `/api/v1/resources/${resourceId}/availability`, {
    range_start: rangeStart,
    range_end: rangeEnd,
    days: '',
    times: [timeOfDay],
    holidays: [],
    frequency_days: 1,
    slot_duration_minutes: SLOT_DURATION_MINUTES,
    slot_capacity: 1,
  });
  if (availabilityResp.status !== 201) {
    throw new Error(
      `[e2e-setup] availability create failed: ${availabilityResp.status} ${await availabilityResp.text()}`
    );
  }
  const availabilityBody = (await availabilityResp.json()) as { slots_created: number };
  if (availabilityBody.slots_created !== 1) {
    throw new Error(
      `[e2e-setup] expected exactly 1 generated slot, got ${availabilityBody.slots_created}`
    );
  }

  // Read back the real, DB-persisted slot to get its exact start_time/end_time — trusting the
  // real stored values rather than re-deriving them client-side.
  const listResp = await bpp(jar, 'GET', `/api/v1/resources/${resourceId}/availability`);
  const listBody = (await listResp.json()) as {
    slots: { start_time: string; end_time: string }[];
  };
  const slot = listBody.slots[0];
  if (!slot) throw new Error('[e2e-setup] no slot found after availability creation');

  return {
    businessName,
    itemName,
    // Word-tokenized, case-insensitive AND-match (livetracker3.md §1.1's `filter_catalog`) —
    // every word here must appear in the combined item+provider descriptor text, which it
    // does ("Playwright"/"E2E" appear in both names, runId is unique to this seeded pair).
    query: `Playwright E2E ${runId}`,
    nonsenseQuery: `zzz-unmatched-${runId}-xyz`,
    resourceId,
    slotStartIso: slot.start_time,
    slotEndIso: slot.end_time,
    supportContact: contact,
  };
}

export default async function globalSetup(): Promise<void> {
  console.log('[e2e-setup] ensuring real Registry trust (onboarding) for BAP, BPP, and beckn-gateway...');
  // Order matters only in that beckn-gateway is the one that can genuinely take multiple
  // attempts (see this file's own top-of-file comment) — doing it first means BAP/BPP's own
  // (normally instant) checks aren't needlessly delayed by gateway's retry/backoff loop.
  await ensureOnboarded('beckn-gateway', DOMAIN);
  await ensureOnboarded('bap-backend', DOMAIN);
  await ensureOnboarded('bpp-backend', DOMAIN);

  console.log('[e2e-setup] seeding a real business + resource + availability slot on BPP...');
  const seed = await seedBusinessAndResource();
  console.log(`[e2e-setup] seeded resource ${seed.resourceId} ("${seed.itemName}"), slot ${seed.slotStartIso} -> ${seed.slotEndIso}`);

  writeFileSync(path.join(__dirname, '.seed-data.json'), JSON.stringify(seed, null, 2));
}
