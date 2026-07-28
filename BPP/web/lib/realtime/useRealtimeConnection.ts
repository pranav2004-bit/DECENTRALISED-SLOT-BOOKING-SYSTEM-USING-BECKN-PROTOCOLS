'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Foundation WebSocket transport (livetracker2.md §2.4) — establishes and keeps
 * alive the standing Web App <-> Backend channel both *_details.md files document
 * as part of the Communication Mechanism. Phase 4.4 is this hook's first real
 * client -> server usage (`send`, below) — every caller before it only ever read
 * `lastMessage`.
 */

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'error';

const RECONNECT_DELAY_MS = 3000;

export function useRealtimeConnection(
  path: string = '/ws/',
  onMessage?: (message: unknown) => void
) {
  const baseUrl = process.env.NEXT_PUBLIC_WS_BASE_URL;
  const [status, setStatus] = useState<ConnectionStatus>(baseUrl ? 'connecting' : 'error');
  const [lastMessage, setLastMessage] = useState<unknown>(null);
  const [generation, setGeneration] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  // Real callers (Phase 4.4) react to a message the instant it arrives, straight from
  // this native WebSocket event handler — not via a `useEffect([lastMessage])` in the
  // consuming component, which would set state synchronously inside an effect body.
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    if (!baseUrl) return;

    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const socket = new WebSocket(`${baseUrl}${path}`);
    socketRef.current = socket;

    socket.addEventListener('open', () => {
      if (!cancelled) setStatus('open');
    });
    socket.addEventListener('message', (event) => {
      if (cancelled) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        parsed = event.data;
      }
      setLastMessage(parsed);
      onMessageRef.current?.(parsed);
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
      if (socketRef.current === socket) socketRef.current = null;
    };
  }, [baseUrl, path, generation]);

  const reconnect = useCallback(() => {
    setStatus('connecting');
    setGeneration((g) => g + 1);
  }, []);

  const send = useCallback((message: unknown) => {
    const socket = socketRef.current;
    if (socket == null || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(typeof message === 'string' ? message : JSON.stringify(message));
    return true;
  }, []);

  return { status, lastMessage, reconnect, send };
}
