import { apiFetch } from './api-client';

/**
 * Business account (signup/login/logout/me) — BPP/web's mirror of BAP/web's
 * `auth-api.ts` (ADR-0004: duplicated, not shared). Phase 4.4's live availability
 * dashboard is this file's first real caller; see `api-client.ts`'s own
 * `credentials: 'include'` fix, added alongside this for the same reason BAP's
 * comment already documents: without both pieces, `request.user` on the backend
 * never resolves to a real session.
 */

export interface BusinessAccount {
  id: string;
  business_name: string;
  contact: string;
  domain_code: string;
  role: 'OWNER' | 'STAFF';
  managed_by: string | null;
  assigned_resource_ids?: string[];
  /** livetracker3.md §7.1 — an owner's own resources, mirroring assigned_resource_ids' shape. */
  owned_resource_ids?: string[];
  /** Profile page's Live/Offline toggle — NOT the same as account-active/login-gating;
   * see BPP/backend/core/models.py's own field comment for why these are kept separate. */
  is_accepting_bookings: boolean;
  /** Profile page's public-facing fields — always a string (never null), empty when unset. */
  description: string;
  public_phone: string;
  public_email: string;
  address: string;
  /** Profile page's cascading State -> Division -> Pincode picker — each '' when
   * unset. See BPP/backend/core/models.py's own BusinessAccount field comments. */
  state: string;
  division: string;
  pincode: string;
  /** "Date of joining" — bounds how far back the Orders page's history browser goes. */
  created_at: string;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function csrfHeader(): Promise<Record<string, string>> {
  if (!readCookie('csrftoken')) {
    await apiFetch('/api/v1/auth/csrf');
  }
  const token = readCookie('csrftoken');
  return token ? { 'X-CSRFToken': token } : {};
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** livetracker3.md §7.1 — real business signup, mirroring BAP/web's own signup(). */
export async function signup(
  businessName: string,
  contact: string,
  password: string,
  domainCode: string
): Promise<BusinessAccount> {
  const resp = await apiFetch('/api/v1/auth/signup', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({
      business_name: businessName,
      contact,
      password,
      domain_code: domainCode,
    }),
  });
  return resp.json();
}

export async function login(contact: string, password: string): Promise<BusinessAccount> {
  const resp = await apiFetch('/api/v1/auth/login', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ contact, password }),
  });
  return resp.json();
}

export async function logout(): Promise<void> {
  await apiFetch('/api/v1/auth/logout', {
    method: 'POST',
    headers: await csrfHeader(),
  });
}

/** Profile page — partial update; only send the fields that actually changed. */
export async function updateProfile(
  changes: Partial<
    Pick<
      BusinessAccount,
      | 'business_name'
      | 'is_accepting_bookings'
      | 'description'
      | 'public_phone'
      | 'public_email'
      | 'address'
      | 'state'
      | 'division'
      | 'pincode'
    >
  >
): Promise<BusinessAccount> {
  const resp = await apiFetch('/api/v1/auth/profile', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify(changes),
  });
  return resp.json();
}

/** Profile page's Password section — the current session stays valid after this
 * (the backend calls Django's update_session_auth_hash), so no re-login is needed
 * anywhere else the same session cookie is already in use. */
export async function changePassword(
  currentPassword: string,
  newPassword: string
): Promise<void> {
  await apiFetch('/api/v1/auth/password', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export async function me(): Promise<BusinessAccount | null> {
  try {
    const resp = await apiFetch('/api/v1/auth/me');
    return await resp.json();
  } catch {
    return null;
  }
}

export { csrfHeader };
