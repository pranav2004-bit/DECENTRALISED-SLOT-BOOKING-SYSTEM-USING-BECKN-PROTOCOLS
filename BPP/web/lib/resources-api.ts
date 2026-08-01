import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';

/**
 * livetracker3.md §7.1: the resource-creation/availability-generation half of the
 * dashboard-home page — both server endpoints (`resource_create_view`/
 * `resource_availability_create_view`) already existed and were already tested at the
 * API level (livetracker2.md §2.2), just never wired to any UI until this phase.
 */

export interface CreatedResource {
  id: string;
  name: string;
}

export async function createResource(
  name: string,
  resourceType: string
): Promise<CreatedResource> {
  const resp = await apiFetch('/api/v1/resources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ name, domain_data: { resource_type: resourceType } }),
  });
  return resp.json();
}

export interface CreatedAvailability {
  calendar_id: string;
  slots_created: number;
}

export async function createAvailability(
  resourceId: string,
  params: {
    rangeStart: string;
    rangeEnd: string;
    times: string[];
    frequencyDays: number;
    slotDurationMinutes: number;
    slotCapacity: number;
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
      slot_duration_minutes: params.slotDurationMinutes,
      slot_capacity: params.slotCapacity,
    }),
  });
  return resp.json();
}
