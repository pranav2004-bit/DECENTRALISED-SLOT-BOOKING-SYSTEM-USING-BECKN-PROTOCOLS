import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';
import type { BecknError, Order, SearchResultsResponse, Support, Tracking } from './beckn-types';

/**
 * Thin wrappers around the customer-facing trigger/result endpoints
 * (livetracker2.md §3.1-3.5) for the booking-flow UI (§3.9) — deliberately NOT the
 * raw Beckn wire shape, matching each endpoint's own view docstring ("web-to-backend
 * calls use this project's own simple JSON convention").
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export async function triggerSearch(
  query: string,
  domain: string,
  division?: string
): Promise<string> {
  const resp = await apiFetch('/api/v1/search', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ query, domain, ...(division ? { division } : {}) }),
  });
  const body = await resp.json();
  return body.transaction_id as string;
}

export async function getSearchResults(transactionId: string): Promise<SearchResultsResponse> {
  const resp = await apiFetch(`/api/v1/search/${transactionId}`);
  return resp.json();
}

/** Which of `resourceIds` (all belonging to `providerId`, per this transaction's own
 * search results) have a real open slot on `date` (`YYYY-MM-DD`) — used to decide
 * which item cards the search-results UI even renders. An id the response doesn't
 * mention (filtered out server-side, or BPP simply didn't answer for it) is treated
 * the same as `false` by every caller — there's no third state worth representing. */
export async function checkAvailability(
  transactionId: string,
  providerId: string,
  resourceIds: string[],
  date: string
): Promise<Record<string, boolean>> {
  const resp = await apiFetch(`/api/v1/search/${transactionId}/availability`, {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ provider_id: providerId, resource_ids: resourceIds, date }),
    // BAP's own backend->BPP call behind this endpoint allows up to 15s per attempt
    // (shared/resilient_http's ResilientHttpClient) for a legitimately slow Neon
    // cold-start reconnect — the default 5s client timeout was aborting (and
    // retrying, and aborting again) well before that backend chain could ever
    // finish, surfacing "Couldn't check availability" on a request that was
    // actually still succeeding server-side.
    timeoutMs: 20000,
  });
  const body = await resp.json();
  return (body.availability ?? {}) as Record<string, boolean>;
}

export interface AvailableSlot {
  start_time: string;
  end_time: string;
  // 'available' = open; 'booked' = already taken (confirmed, or currently held
  // by someone else) — still returned, not omitted, so the time picker can show
  // every real slot in the day and grey out the ones it can't offer, rather than
  // a taken slot silently vanishing as if it never existed.
  status: 'available' | 'booked';
}

/** The real bookable time windows for one item on `date` — what the booking
 * page's time picker shows, so a customer can only request a time that
 * genuinely exists instead of typing an arbitrary one. */
export async function listAvailableSlots(
  transactionId: string,
  providerId: string,
  resourceId: string,
  date: string
): Promise<AvailableSlot[]> {
  const resp = await apiFetch(`/api/v1/search/${transactionId}/slots`, {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ provider_id: providerId, resource_id: resourceId, date }),
    // Same backend-latency ceiling as checkAvailability above — see its comment.
    timeoutMs: 20000,
  });
  const body = await resp.json();
  return (body.slots ?? []) as AvailableSlot[];
}

/** `holdSeconds` (optional): requests a shorter-than-default hold — the
 * tap-to-reserve UX (search/page.tsx) asks for a tight ~45s window so a 2nd
 * customer isn't locked out for the platform's full normal hold duration
 * just because a 1st customer tapped the same slot and never finished.
 * Customer identity is deliberately NOT sent here anymore — the hold this
 * creates is anonymous; identity is required (and sent) at triggerConfirm,
 * the real, final point it's actually known. */
export async function triggerSelect(params: {
  transactionId: string;
  itemId: string;
  requestedTimestamp: string;
  holdSeconds?: number;
}): Promise<void> {
  await apiFetch('/api/v1/select', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({
      transaction_id: params.transactionId,
      item_id: params.itemId,
      requested_timestamp: params.requestedTimestamp,
      ...(params.holdSeconds ? { hold_seconds: params.holdSeconds } : {}),
    }),
  });
}

export interface SelectResultResponse {
  transaction_id: string;
  selected_order: Order | null;
  selected_error: BecknError | null;
}

export async function getSelectResult(transactionId: string): Promise<SelectResultResponse> {
  const resp = await apiFetch(`/api/v1/select/${transactionId}`);
  return resp.json();
}

export async function triggerInit(transactionId: string): Promise<void> {
  await apiFetch('/api/v1/init', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ transaction_id: transactionId }),
  });
}

export interface InitResultResponse {
  transaction_id: string;
  init_order: Order | null;
  init_error: BecknError | null;
}

export async function getInitResult(transactionId: string): Promise<InitResultResponse> {
  const resp = await apiFetch(`/api/v1/init/${transactionId}`);
  return resp.json();
}

export async function triggerConfirm(
  transactionId: string,
  idempotencyKey: string,
  customer: { name: string; email: string; phone: string }
): Promise<void> {
  await apiFetch('/api/v1/confirm', {
    method: 'POST',
    headers: { ...JSON_HEADERS, 'Idempotency-Key': idempotencyKey, ...(await csrfHeader()) },
    body: JSON.stringify({
      transaction_id: transactionId,
      customer_name: customer.name,
      customer_email: customer.email,
      customer_phone: customer.phone,
    }),
  });
}

export interface ConfirmResultResponse {
  transaction_id: string;
  confirmed_order: Order | null;
  confirmed_error: BecknError | null;
}

export async function getConfirmResult(transactionId: string): Promise<ConfirmResultResponse> {
  const resp = await apiFetch(`/api/v1/confirm/${transactionId}`);
  return resp.json();
}

/** livetracker5.md Phase 3 — deliberately NOT a Beckn wire action, so this response
 * shape doesn't follow the confirmed_order/confirmed_error pattern the other
 * result endpoints above use (Payment Module has no real protocol counterpart
 * beyond the Payment.yaml status/collected_by fields the confirm flow already
 * populates). `status` is the honest source of truth: `PENDING` while a hosted
 * checkout is in progress, not an error. */
export interface PaymentResultResponse {
  transaction_id: string;
  status: 'PENDING' | 'SUCCEEDED' | 'FAILED' | 'REFUNDED' | 'PARTIALLY_REFUNDED';
  amount: string;
  currency: string;
  vendor_txn_id: string;
}

export async function triggerPayment(
  transactionId: string,
  idempotencyKey: string
): Promise<PaymentResultResponse> {
  const resp = await apiFetch('/api/v1/payment', {
    method: 'POST',
    // payment_trigger_view is deliberately NOT csrf_exempt (it moves real
    // money) — same reasoning every other trigger in this file now follows
    // too (2026-11-05 CSRF audit fix): each one's own view requires this
    // token, unlike a real `on_X` network-callback view, which genuinely
    // can't carry one and stays csrf_exempt instead.
    headers: { ...JSON_HEADERS, 'Idempotency-Key': idempotencyKey, ...(await csrfHeader()) },
    body: JSON.stringify({ transaction_id: transactionId }),
    // Real gap found live 2026-11-05: apiFetch's 5s default is sized for this
    // app's other trigger endpoints (an async dispatch to Gateway, acked
    // quickly). This one is different — it makes a real, synchronous call to
    // Razorpay's Orders API through shared/resilient_http's own retry budget
    // (HTTP_CLIENT_TIMEOUT_MS=5s x up to 4 attempts + backoff ≈ 23.5s worst
    // case under genuine network degradation). A 5s client timeout firing
    // first meant the browser gave up and retried while the original request
    // was still legitimately in flight — the retry then collided with that
    // same request's still-held idempotency lock ("already being processed"),
    // not a real duplicate-submission bug. Comfortably clears the backend's
    // own worst case with margin.
    timeoutMs: 30000,
  });
  return resp.json();
}

export async function getPaymentResult(transactionId: string): Promise<PaymentResultResponse> {
  const resp = await apiFetch(`/api/v1/payment/${transactionId}`);
  return resp.json();
}

export async function triggerStatus(transactionId: string): Promise<void> {
  await apiFetch('/api/v1/status', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ transaction_id: transactionId }),
  });
}

export interface StatusResultResponse {
  transaction_id: string;
  status_order: Order | null;
  status_error: BecknError | null;
}

export async function getStatusResult(transactionId: string): Promise<StatusResultResponse> {
  const resp = await apiFetch(`/api/v1/status/${transactionId}`);
  return resp.json();
}

export async function triggerUpdate(transactionId: string, requestedTimestamp: string): Promise<void> {
  await apiFetch('/api/v1/update', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({
      transaction_id: transactionId,
      requested_timestamp: requestedTimestamp,
    }),
  });
}

export interface UpdateResultResponse {
  transaction_id: string;
  updated_order: Order | null;
  updated_error: BecknError | null;
}

export async function getUpdateResult(transactionId: string): Promise<UpdateResultResponse> {
  const resp = await apiFetch(`/api/v1/update/${transactionId}`);
  return resp.json();
}

export async function triggerCancel(transactionId: string, cancellationReasonId = ''): Promise<void> {
  await apiFetch('/api/v1/cancel', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({
      transaction_id: transactionId,
      cancellation_reason_id: cancellationReasonId,
    }),
  });
}

export interface CancelResultResponse {
  transaction_id: string;
  cancelled_order: Order | null;
  cancelled_error: BecknError | null;
}

export async function getCancelResult(transactionId: string): Promise<CancelResultResponse> {
  const resp = await apiFetch(`/api/v1/cancel/${transactionId}`);
  return resp.json();
}

/** livetracker3.md §3.1 — `entityId` defaults server-side to the confirmed order's own id. */
export async function triggerRating(
  transactionId: string,
  ratingCategory: string,
  value: string,
  entityId = ''
): Promise<void> {
  await apiFetch('/api/v1/rating', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({
      transaction_id: transactionId,
      rating_category: ratingCategory,
      value,
      entity_id: entityId,
    }),
  });
}

export interface RatingResultResponse {
  transaction_id: string;
  rating_result: Record<string, unknown> | null;
  rating_error: BecknError | null;
}

export async function getRatingResult(transactionId: string): Promise<RatingResultResponse> {
  const resp = await apiFetch(`/api/v1/rating/${transactionId}`);
  return resp.json();
}

export async function triggerSupport(transactionId: string): Promise<void> {
  await apiFetch('/api/v1/support', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ transaction_id: transactionId }),
  });
}

export interface SupportResultResponse {
  transaction_id: string;
  support_result: Support | null;
  support_error: BecknError | null;
}

export async function getSupportResult(transactionId: string): Promise<SupportResultResponse> {
  const resp = await apiFetch(`/api/v1/support/${transactionId}`);
  return resp.json();
}

export async function triggerTrack(transactionId: string): Promise<void> {
  await apiFetch('/api/v1/track', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ transaction_id: transactionId }),
  });
}

export interface TrackResultResponse {
  transaction_id: string;
  tracking: Tracking | null;
  tracking_error: BecknError | null;
}

export async function getTrackResult(transactionId: string): Promise<TrackResultResponse> {
  const resp = await apiFetch(`/api/v1/track/${transactionId}`);
  return resp.json();
}
