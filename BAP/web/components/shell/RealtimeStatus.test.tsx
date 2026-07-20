import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RealtimeStatus } from './RealtimeStatus';
import * as realtimeModule from '@/lib/realtime/useRealtimeConnection';

function mockConnection(overrides: Partial<ReturnType<typeof realtimeModule.useRealtimeConnection>>) {
  return vi.spyOn(realtimeModule, 'useRealtimeConnection').mockReturnValue({
    status: 'connecting',
    lastMessage: null,
    reconnect: vi.fn(),
    ...overrides,
  });
}

describe('RealtimeStatus', () => {
  it('shows "Live" with no retry button when open', () => {
    mockConnection({ status: 'open' });
    render(<RealtimeStatus />);
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('shows a retry button when disconnected, and calls reconnect on click', async () => {
    const reconnect = vi.fn();
    mockConnection({ status: 'closed', reconnect });
    render(<RealtimeStatus />);
    const retryButton = screen.getByRole('button', { name: 'Retry' });
    await userEvent.click(retryButton);
    expect(reconnect).toHaveBeenCalledOnce();
  });

  it('shows a retry button on error status too', () => {
    mockConnection({ status: 'error' });
    render(<RealtimeStatus />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
