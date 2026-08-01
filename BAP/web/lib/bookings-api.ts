import { apiFetch } from './api-client';
import type { Order } from './beckn-types';

/**
 * livetracker3.md §9.1: the browser-side wrapper for `bookings_list_view`
 * (`BAP/backend/core/views.py`) — a real, working, IDOR-safe, cursor-paginated
 * endpoint that had zero frontend consumers anywhere in this app before this phase.
 */
export interface BookingListItem {
  transaction_id: string;
  domain: string;
  confirmed_order: Order | null;
  cancelled_order: Order | null;
  updated_order: Order | null;
  created_at: string;
}

export interface BookingsListResponse {
  bookings: BookingListItem[];
  next_cursor: string | null;
}

export async function listBookings(
  cursor: string | null = null,
  limit = 20
): Promise<BookingsListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  const resp = await apiFetch(`/api/v1/bookings?${params.toString()}`);
  return resp.json();
}
