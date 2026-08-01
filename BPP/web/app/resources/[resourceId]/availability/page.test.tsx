import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockUseParams } = vi.hoisted(() => ({ mockUseParams: vi.fn() }));
vi.mock('next/navigation', () => ({ useParams: mockUseParams }));

const { mockUseRealtimeConnection } = vi.hoisted(() => ({
  mockUseRealtimeConnection: vi.fn(),
}));
vi.mock('@/lib/realtime/useRealtimeConnection', () => ({
  useRealtimeConnection: mockUseRealtimeConnection,
}));

import ResourceAvailabilityPage from './page';
import * as authApi from '@/lib/auth-api';
import * as availabilityApi from '@/lib/availability-api';

const OWNER: authApi.BusinessAccount = {
  id: 'owner-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER',
  managed_by: null,
};

const SLOTS: availabilityApi.SlotInfo[] = [
  {
    id: 'slot-1',
    start_time: '2026-08-05T09:00:00+00:00',
    end_time: '2026-08-05T09:30:00+00:00',
    status: 'AVAILABLE',
    capacity_remaining: 1,
    capacity_total: 1,
  },
  {
    id: 'slot-2',
    start_time: '2026-08-05T10:00:00+00:00',
    end_time: '2026-08-05T10:30:00+00:00',
    status: 'HELD',
    capacity_remaining: 0,
    capacity_total: 1,
  },
];

const RESOURCE: availabilityApi.ResourceInfo = {
  id: 'resource-1',
  name: 'Stylist A',
  average_rating: null,
  rating_count: 0,
  assigned_staff_id: null,
};

const RATED_RESOURCE: availabilityApi.ResourceInfo = {
  id: 'resource-1',
  name: 'Stylist A',
  average_rating: '4.33',
  rating_count: 3,
  assigned_staff_id: null,
};

describe('ResourceAvailabilityPage', () => {
  let capturedOnMessage: ((message: unknown) => void) | undefined;

  beforeEach(() => {
    mockUseParams.mockReturnValue({ resourceId: 'resource-1' });
    capturedOnMessage = undefined;
    mockUseRealtimeConnection.mockImplementation((_path: string, onMessage?: (m: unknown) => void) => {
      capturedOnMessage = onMessage;
      return { status: 'open', lastMessage: null, reconnect: vi.fn(), send: vi.fn(() => true) };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a login form when no session is active', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Business login' })).toBeInTheDocument());
    expect(screen.queryByText('Availability')).not.toBeInTheDocument();
  });

  it('logs in and then shows the resource slot list', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    vi.spyOn(authApi, 'login').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Business login' })).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText('Contact'), 'owner@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'a-strong-passw0rd!');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Availability' })).toBeInTheDocument());
    expect(screen.getByText('Available')).toBeInTheDocument();
    expect(screen.getByText('Held')).toBeInTheDocument();
  });

  it('shows "No ratings yet" for an unrated resource, and the real average for a rated one', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    const { unmount } = render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByText('No ratings yet')).toBeInTheDocument());
    unmount();

    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({
      resource: RATED_RESOURCE,
      slots: SLOTS,
    });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByText('★ 4.33')).toBeInTheDocument());
    expect(screen.getByText('(3 ratings)')).toBeInTheDocument();
  });

  it('already-logged-in: lists slots and patches a slot live on a slot.update message', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByText('Available')).toBeInTheDocument());
    expect(screen.getAllByText('Held')).toHaveLength(1);

    // Simulate the real-time push a second browser tab would receive over the socket
    // the instant another action (hold/confirm/cancel/reschedule/block) touches this
    // slot — no manual refresh, matching Phase 4.4's own Test Gate wording.
    expect(capturedOnMessage).toBeDefined();
    capturedOnMessage!({
      type: 'slot.update',
      slot: { ...SLOTS[0], status: 'HELD', capacity_remaining: 0 },
    });

    await waitFor(() => expect(screen.getAllByText('Held')).toHaveLength(2));
  });

  it('blocks a slot over the socket when open, without calling the REST endpoint', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    const blockSlotsSpy = vi.spyOn(availabilityApi, 'blockSlots');
    const send = vi.fn(() => true);
    mockUseRealtimeConnection.mockImplementation((_path: string, onMessage?: (m: unknown) => void) => {
      capturedOnMessage = onMessage;
      return { status: 'open', lastMessage: null, reconnect: vi.fn(), send };
    });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Block' })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Block' }));

    expect(send).toHaveBeenCalledWith({ type: 'block_slot', slot_id: 'slot-1' });
    expect(blockSlotsSpy).not.toHaveBeenCalled();
  });

  it('falls back to the REST endpoint when the socket is not open', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    vi.spyOn(availabilityApi, 'blockSlots').mockResolvedValue({ blocked: ['slot-1'], skipped: [] });
    mockUseRealtimeConnection.mockImplementation((_path: string, onMessage?: (m: unknown) => void) => {
      capturedOnMessage = onMessage;
      return { status: 'closed', lastMessage: null, reconnect: vi.fn(), send: vi.fn(() => false) };
    });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Block' })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Block' }));

    await waitFor(() => expect(availabilityApi.blockSlots).toHaveBeenCalledWith('resource-1', ['slot-1']));
  });

  it('livetracker3.md §8.1 audit fix: shows "Access ended", not a perpetual "Connecting…", once the socket reports forbidden', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: SLOTS });
    mockUseRealtimeConnection.mockImplementation((_path: string, onMessage?: (m: unknown) => void) => {
      capturedOnMessage = onMessage;
      return { status: 'forbidden', lastMessage: null, reconnect: vi.fn(), send: vi.fn(() => false) };
    });
    render(<ResourceAvailabilityPage />);

    await waitFor(() => expect(screen.getByText('Access ended')).toBeInTheDocument());
    expect(screen.queryByText('Connecting…')).not.toBeInTheDocument();
  });
});
