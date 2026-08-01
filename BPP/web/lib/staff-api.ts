import { apiFetch } from './api-client';
import { csrfHeader } from './auth-api';

/**
 * livetracker3.md §8.1: staff-account creation/listing and resource assignment —
 * both server endpoints (`staff_view`/`resource_assign_staff_view`) already existed
 * and were already tested at the API level (livetracker2.md §4.3), just never wired
 * to any UI until this phase, the same "built but unreachable" pattern §7.1 closed
 * for owner signup.
 */

export interface StaffAccount {
  id: string;
  business_name: string;
  contact: string;
  is_active: boolean;
  assigned_resource_ids: string[];
}

export async function listStaff(): Promise<StaffAccount[]> {
  const resp = await apiFetch('/api/v1/staff');
  const body = await resp.json();
  return body.staff;
}

export async function createStaff(
  businessName: string,
  contact: string,
  password: string
): Promise<StaffAccount> {
  const resp = await apiFetch('/api/v1/staff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ business_name: businessName, contact, password }),
  });
  return resp.json();
}

export async function assignStaffToResource(
  resourceId: string,
  staffId: string
): Promise<{ id: string; assigned_staff_id: string }> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/assign-staff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ staff_id: staffId }),
  });
  return resp.json();
}

/**
 * livetracker3.md §8.1's third post-close audit: a real gap found and closed — there
 * was previously no way to clear a resource's assignment once made anywhere in the
 * backend. `staff_id: null` (the key present, explicitly `null`) is the server's own
 * real unassign signal, distinct from omitting the key (still a validation error).
 */
export async function unassignStaffFromResource(
  resourceId: string
): Promise<{ id: string; assigned_staff_id: null }> {
  const resp = await apiFetch(`/api/v1/resources/${resourceId}/assign-staff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ staff_id: null }),
  });
  return resp.json();
}

/**
 * livetracker3.md §8.1's seventh post-close audit: a real gap found and closed —
 * deactivating/reactivating a staff account was previously only possible via Django
 * admin, with no real UI path for an owner to do it themselves. Deactivating also
 * clears any resource that staff account was assigned to (server-side), so the
 * frontend doesn't need a separate unassign call.
 */
export async function setStaffActive(
  staffId: string,
  isActive: boolean
): Promise<{ id: string; is_active: boolean }> {
  const resp = await apiFetch(`/api/v1/staff/${staffId}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ is_active: isActive }),
  });
  return resp.json();
}

/**
 * livetracker3.md §8.1's ninth post-close audit: a real gap found and closed — no
 * staff account's password could ever change after creation anywhere in this app. The
 * owner, who already chooses the password once at creation, can reset it again later —
 * the same real control already established for `is_active`, not a new boundary.
 */
export async function resetStaffPassword(
  staffId: string,
  password: string
): Promise<{ id: string }> {
  const resp = await apiFetch(`/api/v1/staff/${staffId}/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await csrfHeader()) },
    body: JSON.stringify({ password }),
  });
  return resp.json();
}
