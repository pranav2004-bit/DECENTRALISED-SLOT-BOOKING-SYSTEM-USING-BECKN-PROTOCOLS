import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mockPush }) }));

const { mockUseRealtimeConnection } = vi.hoisted(() => ({
  mockUseRealtimeConnection: vi.fn(),
}));
vi.mock('@/lib/realtime/useRealtimeConnection', () => ({
  useRealtimeConnection: mockUseRealtimeConnection,
}));

import OrdersPage from './page';
import * as authApi from '@/lib/auth-api';
import * as ordersApi from '@/lib/orders-api';

const OWNER: authApi.BusinessAccount = {
  id: 'owner-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER',
  managed_by: null,
};

const STAFF: authApi.BusinessAccount = {
  id: 'staff-1',
  business_name: 'Stylist One',
  contact: 'staff@example.com',
  domain_code: 'ONDC:RET13',
  role: 'STAFF',
  managed_by: 'owner-1',
  assigned_resource_ids: ['resource-a'],
};

const ORDER_A: ordersApi.Order = {
  transaction_id: 'tx-a',
  resource_id: 'resource-a',
  resource_name: 'Stylist A',
  slot_time: '2026-08-05T09:00:00+00:00',
  status: 'ACTIVE',
};

const ORDER_B: ordersApi.Order = {
  transaction_id: 'tx-b',
  resource_id: 'resource-b',
  resource_name: 'Stylist B',
  slot_time: '2026-08-05T10:00:00+00:00',
  status: 'ACTIVE',
};

describe('OrdersPage', () => {
  let capturedOnMessage: ((message: unknown) => void) | undefined;

  beforeEach(() => {
    mockPush.mockClear();
    capturedOnMessage = undefined;
    mockUseRealtimeConnection.mockImplementation((_path: string, onMessage?: (m: unknown) => void) => {
      capturedOnMessage = onMessage;
      return { status: 'open', lastMessage: null, reconnect: vi.fn(), send: vi.fn(() => true) };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<OrdersPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('does not redirect a STAFF account away — orders is reachable for both roles', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(STAFF);
    vi.spyOn(ordersApi, 'getOrders').mockResolvedValue({ orders: [ORDER_A], next_cursor: null });
    render(<OrdersPage />);

    await waitFor(() => expect(screen.getByText('Stylist A')).toBeInTheDocument());
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('shows an empty state with zero orders', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(ordersApi, 'getOrders').mockResolvedValue({ orders: [], next_cursor: null });
    render(<OrdersPage />);

    expect(await screen.findByText('No orders yet')).toBeInTheDocument();
  });

  it('lists real orders from the initial page load', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(ordersApi, 'getOrders').mockResolvedValue({
      orders: [ORDER_A, ORDER_B],
      next_cursor: null,
    });
    render(<OrdersPage />);

    await waitFor(() => expect(screen.getByText('Stylist A')).toBeInTheDocument());
    expect(screen.getByText('Stylist B')).toBeInTheDocument();
  });

  it('livetracker6.md §2.2: a live order.confirmed broadcast prepends a new row without a reload', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(ordersApi, 'getOrders').mockResolvedValue({ orders: [ORDER_A], next_cursor: null });
    render(<OrdersPage />);

    await waitFor(() => expect(screen.getByText('Stylist A')).toBeInTheDocument());
    expect(capturedOnMessage).toBeDefined();

    capturedOnMessage?.({ type: 'order.confirmed', order: ORDER_B });

    await waitFor(() => expect(screen.getByText('Stylist B')).toBeInTheDocument());
    expect(screen.getByText('Stylist A')).toBeInTheDocument();
  });

  it('a live broadcast for an order already on the page is not shown twice', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(ordersApi, 'getOrders').mockResolvedValue({ orders: [ORDER_A], next_cursor: null });
    render(<OrdersPage />);

    await waitFor(() => expect(screen.getByText('Stylist A')).toBeInTheDocument());
    capturedOnMessage?.({ type: 'order.confirmed', order: ORDER_A });

    expect(screen.getAllByText('Stylist A')).toHaveLength(1);
  });

  it('loads a second page via cursor pagination', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    const getOrders = vi.spyOn(ordersApi, 'getOrders');
    getOrders.mockResolvedValueOnce({ orders: [ORDER_A], next_cursor: 'cursor-1' });
    getOrders.mockResolvedValueOnce({ orders: [ORDER_B], next_cursor: null });
    render(<OrdersPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Load more' }));

    await waitFor(() => expect(screen.getByText('Stylist B')).toBeInTheDocument());
    expect(getOrders).toHaveBeenLastCalledWith('cursor-1');
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
  });

  it('shows an error state with retry when loading orders fails', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    const { ApiError } = await import('@/lib/api-client');
    vi.spyOn(ordersApi, 'getOrders').mockRejectedValue(new ApiError('backend unreachable', 502, null));
    render(<OrdersPage />);

    expect(await screen.findByText('backend unreachable')).toBeInTheDocument();
  });
});
