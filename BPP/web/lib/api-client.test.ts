import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, ApiError } from './api-client';

describe('apiFetch', () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = 'http://test-backend';
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('throws if NEXT_PUBLIC_API_BASE_URL is not configured', async () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    await expect(apiFetch('/health')).rejects.toThrow('NEXT_PUBLIC_API_BASE_URL');
  });

  it('returns the response on success', async () => {
    const mockResponse = new Response(JSON.stringify({ ok: true }), { status: 200 });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse));
    const resp = await apiFetch('/health');
    expect(resp.status).toBe(200);
  });

  it('retries on 503 and eventually succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const resp = await apiFetch('/flaky', { maxRetries: 2, timeoutMs: 1000 });
    expect(resp.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('throws ApiError with correlation id on a non-retryable 4xx error', async () => {
    const errorBody = JSON.stringify({
      error: { code: 'VALIDATION_ERROR', message: 'bad request', correlation_id: 'abc-123' },
    });
    const response = new Response(errorBody, {
      status: 400,
      headers: { 'X-Correlation-Id': 'abc-123' },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    await expect(apiFetch('/bad')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      correlationId: 'abc-123',
      message: 'bad request',
    });
  });

  it('gives up after maxRetries on persistent 503s', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      apiFetch('/always-down', { maxRetries: 1, timeoutMs: 1000 })
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(2); // initial + 1 retry
  });
});
