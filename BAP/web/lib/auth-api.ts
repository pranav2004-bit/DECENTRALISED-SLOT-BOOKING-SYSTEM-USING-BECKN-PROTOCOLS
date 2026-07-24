import { apiFetch } from './api-client';

/**
 * Customer account (signup/login/logout/me) — added at Phase 3 Exit
 * (livetracker2.md). §2.1/§3.7 built and tested the backend auth+session+CSRF
 * machinery, but no browser UI ever called it: the booking-flow screens (§3.9)
 * always ran anonymously, and `apiFetch` never sent cookies cross-origin (BAP/web
 * on :3000, BAP/backend on :8001), so `resolve_owned_session`'s IDOR protection
 * (§3.7) and the reservation-hold abuse cap were unreachable from the real product.
 * This file is the browser-side half of closing that gap: Django's documented
 * AJAX-CSRF pattern (GET a `csrftoken` cookie, echo it back as `X-CSRFToken`).
 */

export interface Customer {
  id: string;
  name: string;
  contact: string;
  notify_by_email: boolean;
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

export async function signup(name: string, contact: string, password: string): Promise<Customer> {
  const resp = await apiFetch('/api/v1/auth/signup', {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...(await csrfHeader()) },
    body: JSON.stringify({ name, contact, password }),
  });
  return resp.json();
}

export async function login(contact: string, password: string): Promise<Customer> {
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

export async function me(): Promise<Customer | null> {
  try {
    const resp = await apiFetch('/api/v1/auth/me');
    return await resp.json();
  } catch {
    return null;
  }
}
