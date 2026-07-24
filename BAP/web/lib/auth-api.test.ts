import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { login, logout, me, signup } from './auth-api';

function clearCookies() {
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
  });
}

describe('auth-api', () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = 'http://test-backend';
    clearCookies();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    clearCookies();
  });

  it('fetches a CSRF cookie before signup if none is set yet, and echoes it back', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/api/v1/auth/csrf')) {
        document.cookie = 'csrftoken=abc123';
        return Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({ id: 'c-1', name: 'Ada', contact: 'ada@example.com', notify_by_email: true }),
          { status: 201 }
        )
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    await signup('Ada', 'ada@example.com', 'Passw0rd!23');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, signupCallArgs] = fetchMock.mock.calls;
    const [signupUrl, signupInit] = signupCallArgs;
    expect(signupUrl).toBe('http://test-backend/api/v1/auth/signup');
    expect((signupInit.headers as Record<string, string>)['X-CSRFToken']).toBe('abc123');
  });

  it('reuses an already-set CSRF cookie without a fresh GET for login', async () => {
    document.cookie = 'csrftoken=already-here';
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ id: 'c-1', name: 'Ada', contact: 'ada@example.com', notify_by_email: true }),
        { status: 200 }
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    await login('ada@example.com', 'Passw0rd!23');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, loginInit] = fetchMock.mock.calls[0];
    expect((loginInit.headers as Record<string, string>)['X-CSRFToken']).toBe('already-here');
  });

  it('logout sends the CSRF header and no body', async () => {
    document.cookie = 'csrftoken=xyz';
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await logout();

    const [, logoutInit] = fetchMock.mock.calls[0];
    expect((logoutInit.headers as Record<string, string>)['X-CSRFToken']).toBe('xyz');
  });

  it('me() returns the customer on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ id: 'c-1', name: 'Ada', contact: 'ada@example.com', notify_by_email: true }),
          { status: 200 }
        )
      )
    );
    const customer = await me();
    expect(customer?.name).toBe('Ada');
  });

  it('me() returns null when unauthenticated (401)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'not logged in' } }), {
          status: 401,
        })
      )
    );
    const customer = await me();
    expect(customer).toBeNull();
  });
});
