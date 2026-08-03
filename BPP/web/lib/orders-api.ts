import { apiFetch } from './api-client';

/**
 * livetracker6.md §2.2 — the business-facing Orders list's own REST half (the
 * initial page load the WebSocket then keeps live), mirroring this project's
 * own established `apiFetch`-wrapper convention (`resources-api.ts`/`staff-api.ts`).
 */

export interface Order {
  transaction_id: string;
  resource_id: string;
  resource_name: string;
  slot_time: string;
  status: string;
}

export interface OrdersListResponse {
  orders: Order[];
  next_cursor: string | null;
}

export async function getOrders(cursor?: string | null): Promise<OrdersListResponse> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  const resp = await apiFetch(`/api/v1/orders${qs}`);
  return resp.json();
}
