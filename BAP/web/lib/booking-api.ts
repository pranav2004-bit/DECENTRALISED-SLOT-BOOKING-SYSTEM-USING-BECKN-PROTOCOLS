import { apiFetch } from './api-client';
import type { BecknError, Order, SearchResultsResponse, Support, Tracking } from './beckn-types';

/**
 * Thin wrappers around the customer-facing trigger/result endpoints
 * (livetracker2.md §3.1-3.5) for the booking-flow UI (§3.9) — deliberately NOT the
 * raw Beckn wire shape, matching each endpoint's own view docstring ("web-to-backend
 * calls use this project's own simple JSON convention").
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export async function triggerSearch(query: string, domain: string): Promise<string> {
  const resp = await apiFetch('/api/v1/search', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ query, domain }),
  });
  const body = await resp.json();
  return body.transaction_id as string;
}

export async function getSearchResults(transactionId: string): Promise<SearchResultsResponse> {
  const resp = await apiFetch(`/api/v1/search/${transactionId}`);
  return resp.json();
}

export async function triggerSelect(params: {
  transactionId: string;
  itemId: string;
  requestedTimestamp: string;
}): Promise<void> {
  await apiFetch('/api/v1/select', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      transaction_id: params.transactionId,
      item_id: params.itemId,
      requested_timestamp: params.requestedTimestamp,
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
    headers: JSON_HEADERS,
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

export async function triggerConfirm(transactionId: string, idempotencyKey: string): Promise<void> {
  await apiFetch('/api/v1/confirm', {
    method: 'POST',
    headers: { ...JSON_HEADERS, 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ transaction_id: transactionId }),
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

export async function triggerStatus(transactionId: string): Promise<void> {
  await apiFetch('/api/v1/status', {
    method: 'POST',
    headers: JSON_HEADERS,
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

export async function triggerCancel(transactionId: string, cancellationReasonId = ''): Promise<void> {
  await apiFetch('/api/v1/cancel', {
    method: 'POST',
    headers: JSON_HEADERS,
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
    headers: JSON_HEADERS,
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
    headers: JSON_HEADERS,
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
    headers: JSON_HEADERS,
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
