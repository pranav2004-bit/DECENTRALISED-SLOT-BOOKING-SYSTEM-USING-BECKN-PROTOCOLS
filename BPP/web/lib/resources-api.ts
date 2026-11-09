import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';
import { getSlots, type ResourceInfo } from './availability-api';

/**
 * livetracker3.md §7.1: the resource-creation/availability-generation half of the
 * dashboard-home page — both server endpoints (`resource_create_view`/
 * `resource_availability_create_view`) already existed and were already tested at the
 * API level (livetracker2.md §2.2), just never wired to any UI until this phase.
 */

export interface CreatedResource {
  id: string;
  name: string;
  domain_data: Record<string, unknown>;
  is_active: boolean;
  price_currency: string;
  price_value: string;
  created_at: string;
}

export async function createResource(
  name: string,
  resourceType: string,
  extraDomainData: Record<string, unknown> = {}
): Promise<CreatedResource> {
  const resp = await apiFetch('/api/v1/resources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({
      name,
      domain_data: { resource_type: resourceType, ...extraDomainData },
    }),
  });
  return resp.json();
}

/** Edit action — partial update, merges into the existing domain_data rather than
 * replacing it (see the backend view's own docstring for why that matters). */
export async function updateResource(
  resourceId: string,
  changes: {
    name?: string;
    domain_data?: Record<string, unknown>;
    price_value?: number;
    price_currency?: string;
  }
): Promise<CreatedResource> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify(changes),
  });
  return resp.json();
}

export async function setResourceStatus(
  resourceId: string,
  isActive: boolean
): Promise<{ id: string; is_active: boolean }> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ is_active: isActive }),
  });
  return resp.json();
}

/** Refuses (409) when the resource has real booking history — see the backend
 * view's own docstring for why a hard delete isn't safe in that case. */
export async function deleteResource(resourceId: string): Promise<void> {
  await apiFetch(`/api/v1/resources/${resourceId}/delete`, {
    method: 'POST',
    headers: await csrfHeader(),
  });
}

export interface CreatedAvailability {
  calendar_id: string;
  slots_created: number;
}

export interface DeletedAvailability {
  deleted_count: number;
  kept_count: number;
}

/** Removes every still-open (AVAILABLE) slot for a resource — leaves any
 * HELD/BOOKED/CANCELLED slot untouched, since those carry real booking
 * history the backend refuses to destroy (see the view's own docstring). */
export async function deleteAvailability(resourceId: string): Promise<DeletedAvailability> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/availability/delete`, {
    method: 'POST',
    headers: await csrfHeader(),
  });
  return resp.json();
}

export async function createAvailability(
  resourceId: string,
  params: {
    rangeStart: string;
    rangeEnd: string;
    times: string[];
    frequencyDays: number;
    /** Comma-separated weekday tokens (MON..SUN), matching AvailabilityCalendar.days
     * exactly — omitted/blank means every day. */
    days?: string;
    slotDurationMinutes: number;
    slotCapacity: number;
    /** Token/queue mode (AutoCare's numbered bays only) — when set, the backend
     * generates this many *identical*-time slots per day instead of one slot per
     * `times` anchor. Omitted for every other brand, matching
     * AvailabilityCalendar.slot_count's own "additive, not a replacement" field. */
    slotCount?: number;
  }
): Promise<CreatedAvailability> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/availability`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({
      range_start: params.rangeStart,
      range_end: params.rangeEnd,
      times: params.times,
      frequency_days: params.frequencyDays,
      days: params.days ?? '',
      slot_duration_minutes: params.slotDurationMinutes,
      slot_capacity: params.slotCapacity,
      ...(params.slotCount !== undefined ? { slot_count: params.slotCount } : {}),
    }),
  });
  return resp.json();
}

/**
 * livetracker3.md §8.1: shared with `app/dashboard/page.tsx` (which introduced this
 * pattern in §7.1) and the new `app/staff/page.tsx` — a business's own resource count
 * is small, and every resource here is already individually fetched again on its own
 * availability page regardless, so no second bulk-listing endpoint is needed for
 * either the dashboard's own resource list or the staff page's resource picker.
 */
export async function fetchOwnedResources(ids: string[]): Promise<ResourceInfo[]> {
  if (ids.length === 0) return [];
  const results = await Promise.all(ids.map((id) => getSlots(id)));
  return results.map((r) => r.resource);
}
