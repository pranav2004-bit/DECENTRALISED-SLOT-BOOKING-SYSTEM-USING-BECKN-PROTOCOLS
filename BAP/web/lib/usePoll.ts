'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Shared polling primitive for the booking flow's result screens
 * (livetracker2.md §3.9) — search/select/init/status results only ever arrive
 * asynchronously via a real Beckn on_* callback (protocol_compliance_notes_v1.1.md
 * §H.1: async is mandatory), so every result screen needs the same
 * poll-until-`isDone`-or-error shape. Extracted once here rather than duplicated
 * across four screens.
 *
 * Restarts only when `key` changes (e.g. a new transaction_id) — `fetcher`/`isDone`
 * are read from refs so callers can pass fresh closures every render without
 * retriggering the effect.
 */
export function usePoll<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  isDone: (result: T) => boolean,
  { intervalMs = 1500, maxAttempts = Infinity }: { intervalMs?: number; maxAttempts?: number } = {}
): { data: T | null; error: Error | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(Boolean(key));
  const [trackedKey, setTrackedKey] = useState(key);

  // Resets accumulated state the instant `key` changes (e.g. a new
  // transaction_id) — the React-endorsed "adjusting state when a prop
  // changes" render-phase pattern, not an effect, so it bails out before
  // ever painting stale data for the new key.
  if (key !== trackedKey) {
    setTrackedKey(key);
    setData(null);
    setError(null);
    setLoading(Boolean(key));
  }

  const fetcherRef = useRef(fetcher);
  const isDoneRef = useRef(isDone);
  useEffect(() => {
    fetcherRef.current = fetcher;
    isDoneRef.current = isDone;
  });

  useEffect(() => {
    if (!key) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;

    async function tick() {
      attempts += 1;
      try {
        const result = await fetcherRef.current();
        if (cancelled) return;
        setData(result);
        if (isDoneRef.current(result) || attempts >= maxAttempts) {
          setLoading(false);
        } else {
          timer = setTimeout(tick, intervalMs);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error('Request failed'));
        setLoading(false);
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [key, intervalMs, maxAttempts]);

  return { data, error, loading };
}
