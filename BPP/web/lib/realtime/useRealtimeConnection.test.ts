import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useRealtimeConnection } from './useRealtimeConnection';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  listeners: Record<string, ((event: unknown) => void)[]> = {};
  closed = false;
  readyState = MockWebSocket.CONNECTING;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, handler: (event: unknown) => void) {
    (this.listeners[type] ??= []).push(handler);
  }

  removeEventListener() {}

  close() {
    this.closed = true;
    this.readyState = MockWebSocket.CLOSED;
  }

  send(data: string) {
    this.sent.push(data);
  }

  emit(type: string, event: unknown = {}) {
    if (type === 'open') this.readyState = MockWebSocket.OPEN;
    for (const handler of this.listeners[type] ?? []) handler(event);
  }
}

describe('useRealtimeConnection', () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_WS_BASE_URL = 'ws://test-backend';
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete process.env.NEXT_PUBLIC_WS_BASE_URL;
  });

  it('connects to NEXT_PUBLIC_WS_BASE_URL + path and reports open on the open event', async () => {
    const { result } = renderHook(() => useRealtimeConnection('/ws/'));
    expect(result.current.status).toBe('connecting');
    expect(MockWebSocket.instances[0].url).toBe('ws://test-backend/ws/');

    act(() => {
      MockWebSocket.instances[0].emit('open');
    });

    await waitFor(() => expect(result.current.status).toBe('open'));
  });

  it('parses JSON messages into lastMessage', async () => {
    const { result } = renderHook(() => useRealtimeConnection('/ws/'));

    act(() => {
      MockWebSocket.instances[0].emit('message', { data: JSON.stringify({ type: 'connected' }) });
    });

    await waitFor(() => expect(result.current.lastMessage).toEqual({ type: 'connected' }));
  });

  it('reports closed when the socket closes', async () => {
    const { result } = renderHook(() => useRealtimeConnection('/ws/'));

    act(() => {
      MockWebSocket.instances[0].emit('close');
    });

    await waitFor(() => expect(result.current.status).toBe('closed'));
  });

  it('reports error without a configured base URL', () => {
    delete process.env.NEXT_PUBLIC_WS_BASE_URL;
    const { result } = renderHook(() => useRealtimeConnection('/ws/'));
    expect(result.current.status).toBe('error');
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('reconnect() opens a fresh socket', async () => {
    const { result } = renderHook(() => useRealtimeConnection('/ws/'));
    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      result.current.reconnect();
    });

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(2));
    expect(MockWebSocket.instances[0].closed).toBe(true);
  });

  it('invokes onMessage with the parsed payload the instant a message arrives', async () => {
    const onMessage = vi.fn();
    renderHook(() => useRealtimeConnection('/ws/', onMessage));

    act(() => {
      MockWebSocket.instances[0].emit('message', {
        data: JSON.stringify({ type: 'slot.update', slot: { id: 's1' } }),
      });
    });

    await waitFor(() =>
      expect(onMessage).toHaveBeenCalledWith({ type: 'slot.update', slot: { id: 's1' } })
    );
  });

  describe('send', () => {
    it('sends JSON over the socket and returns true when open', async () => {
      const { result } = renderHook(() => useRealtimeConnection('/ws/'));
      act(() => {
        MockWebSocket.instances[0].emit('open');
      });
      await waitFor(() => expect(result.current.status).toBe('open'));

      let sent: boolean | undefined;
      act(() => {
        sent = result.current.send({ type: 'block_slot', slot_id: 'abc' });
      });

      expect(sent).toBe(true);
      expect(MockWebSocket.instances[0].sent).toEqual([
        JSON.stringify({ type: 'block_slot', slot_id: 'abc' }),
      ]);
    });

    it('returns false and sends nothing when the socket is not open', () => {
      const { result } = renderHook(() => useRealtimeConnection('/ws/'));

      let sent: boolean | undefined;
      act(() => {
        sent = result.current.send({ type: 'block_slot', slot_id: 'abc' });
      });

      expect(sent).toBe(false);
      expect(MockWebSocket.instances[0].sent).toEqual([]);
    });
  });
});
