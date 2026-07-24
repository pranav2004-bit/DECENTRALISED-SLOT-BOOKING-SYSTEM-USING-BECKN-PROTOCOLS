/**
 * API client with timeout + retry, per livetracker1.md Phase 1.3 "API client with
 * timeout/retry" requirement. Deliberately lightweight (fetch + AbortController),
 * not a heavy library — matches the same no-over-engineering call made for the
 * Python-side ResilientHttpClient (shared/resilient_http).
 */

const DEFAULT_TIMEOUT_MS = 5000;
const DEFAULT_MAX_RETRIES = 2;
const RETRYABLE_STATUS = new Set([502, 503, 504]);

export class ApiError extends Error {
  status: number;
  correlationId: string | null;

  constructor(message: string, status: number, correlationId: string | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.correlationId = correlationId;
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
  maxRetries?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    throw new Error('NEXT_PUBLIC_API_BASE_URL is not configured');
  }
  const { timeoutMs = DEFAULT_TIMEOUT_MS, maxRetries = DEFAULT_MAX_RETRIES, ...init } = options;

  let lastError: unknown;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${baseUrl}${path}`, {
        credentials: 'include',
        ...init,
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (RETRYABLE_STATUS.has(response.status) && attempt < maxRetries) {
        await sleep(2 ** attempt * 200);
        continue;
      }

      if (!response.ok) {
        const correlationId = response.headers.get('X-Correlation-Id');
        let message = `Request failed with status ${response.status}`;
        try {
          const body = await response.json();
          if (body?.error?.message) message = body.error.message;
        } catch {
          // response body wasn't JSON — keep the generic message
        }
        throw new ApiError(message, response.status, correlationId);
      }

      return response;
    } catch (err) {
      clearTimeout(timer);
      lastError = err;
      if (err instanceof ApiError) throw err;
      if (attempt < maxRetries) {
        await sleep(2 ** attempt * 200);
        continue;
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error('Request failed after retries');
}
