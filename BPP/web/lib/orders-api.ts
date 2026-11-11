import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';

/** The real Orders list (BPP/backend/core/views.py's `orders_list_view`) —
 * scoped server-side to exactly the resources this logged-in account can
 * access (every owned resource for an owner, the one assigned resource for
 * staff), same resource set the live `/ws/business/orders/` broadcast uses. */
export interface OrderRow {
  booking_id: string;
  transaction_id: string;
  resource_id: string;
  resource_name: string;
  slot_time: string;
  slot_end_time: string;
  slot_number: number;
  status: string;
  fulfillment_status: 'SCHEDULED' | 'IN_PROGRESS' | 'COMPLETED' | 'NO_SHOW';
  payment_status: string;
  amount: string | null;
  currency: string;
  customer_id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string;
}

export interface OrdersListResponse {
  orders: OrderRow[];
  next_cursor: string | null;
}

export async function fetchOrders(cursor?: string, limit = 200): Promise<OrdersListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  const resp = await apiFetch(`/api/v1/orders?${params.toString()}`);
  return resp.json();
}

export async function updateFulfillmentStatus(
  bookingId: string,
  fulfillmentStatus: 'PENDING' | 'COMPLETED'
): Promise<{ booking_id: string; fulfillment_status: string }> {
  const resp = await apiFetch(`/api/v1/orders/${bookingId}/fulfillment-status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ fulfillment_status: fulfillmentStatus }),
  });
  return resp.json();
}
