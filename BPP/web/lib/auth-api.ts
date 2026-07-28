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

export async function me(): Promise<BusinessAccount | null> {
  try {
    const resp = await apiFetch('/api/v1/auth/me');
    return await resp.json();
  } catch {
    return null;
  }
}

export { csrfHeader };
