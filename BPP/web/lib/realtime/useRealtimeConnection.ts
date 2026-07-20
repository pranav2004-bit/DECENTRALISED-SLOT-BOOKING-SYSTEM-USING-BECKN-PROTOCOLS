'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * Foundation WebSocket transport (livetracker2.md §2.4) — establishes and keeps
 * alive the standing Web App <-> Backend channel both *_details.md files document
 * as part of the Communication Mechanism. The live-inventory-push feature built on
 * top of this transport is Phase 4.4's job, not this hook's.
 */

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'error';

const RECONNECT_DELAY_MS = 3000;

export function useRealtimeConnection(path: string = '/ws/') {
  const baseUrl = process.env.NEXT_PUBLIC_WS_BASE_URL;
  const [status, setStatus] = useState<ConnectionStatus>(baseUrl ? 'connecting' : 'error');
  const [lastMessage, setLastMessage] = useState<unknown>(null);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!baseUrl) return;

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const socket = new WebSocket(`${baseUrl}${path}`);

    socket.addEventListener('open', () => {
      if (!cancelled) setStatus('open');
    });
    socket.addEventListener('message', (event) => {
      if (cancelled) return;
      try {
        setLastMessage(JSON.parse(event.data));
      } catch {
        setLastMessage(event.data);
      }
    });
    socket.addEventListener('close', () => {
      if (cancelled) return;
      setStatus('closed');
      reconnectTimer = setTimeout(() => {
        if (!cancelled) {
          setStatus('connecting');
          setGeneration((g) => g + 1);
        }
      }, RECONNECT_DELAY_MS);
    });
    socket.addEventListener('error', () => {
      if (!cancelled) setStatus('error');
    });

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket.close();
    };
  }, [baseUrl, path, generation]);

  const reconnect = useCallback(() => {
    setStatus('connecting');
    setGeneration((g) => g + 1);
  }, []);

  return { status, lastMessage, reconnect };
}
