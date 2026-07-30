import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';

/**
 * Phase 4.4 (livetracker2.md §4.4): REST half of the live availability dashboard —
 * the initial slot list (`getSlots`) before the WebSocket connection takes over, and
 * the block action's REST fallback (`blockSlots`, also reachable live over the socket
 * itself via `ResourceAvailabilityConsumer`'s `block_slot` message type).
 */

export interface SlotInfo {
  id: string;
  start_time: string;
  end_time: string;
  status: 'AVAILABLE' | 'HELD' | 'BOOKED' | 'CANCELLED';
  capacity_remaining: number;
  capacity_total: number;
}

/** livetracker3.md §2.2 — the resource's own real rating aggregate, `null` when none exists yet. */
export interface ResourceInfo {
  id: string;
  name: string;
  average_rating: string | null;
  rating_count: number;
}

export interface BlockResult {
  blocked: string[];
  skipped: { slot_id: string; reason: string }[];
}

export async function getSlots(
  resourceId: string
): Promise<{ resource: ResourceInfo; slots: SlotInfo[] }> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/availability`);
  const body = await resp.json();
  return { resource: body.resource, slots: body.slots };
}

export async function blockSlots(resourceId: string, slotIds: string[]): Promise<BlockResult> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/availability/block`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ slot_ids: slotIds }),
  });
  return resp.json();
}
