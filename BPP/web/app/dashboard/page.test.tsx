import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush, mockRefresh } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import DashboardPage from './page';
import * as authApi from '@/lib/auth-api';
import * as availabilityApi from '@/lib/availability-api';
import * as resourcesApi from '@/lib/resources-api';

const OWNER_ACCOUNT = {
  id: 'biz-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER' as const,
  managed_by: null,
};

describe('DashboardPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<DashboardPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('livetracker3.md §7.1: redirects a STAFF login to their own assigned resource, not the owner dashboard', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      role: 'STAFF',
      assigned_resource_ids: ['res-9'],
    });
    render(<DashboardPage />);

    await waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith('/resources/res-9/availability')
    );
  });

  it('livetracker3.md §7.1: shows the real empty state for a brand-new owner with zero resources', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    render(<DashboardPage />);

    expect(await screen.findByText('No services yet')).toBeInTheDocument();
    expect(screen.getByText('Glow Salon')).toBeInTheDocument();
  });

  it('livetracker3.md §7.1: lists an already-existing owner’s pre-existing resources (regression check)', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      owned_resource_ids: ['res-1', 'res-2'],
    });
    const getSlotsSpy = vi.spyOn(availabilityApi, 'getSlots').mockImplementation(async (id) => ({
      resource: { id, name: id === 'res-1' ? 'Stylist A' : 'Stylist B', average_rating: null, rating_count: 0 },
      slots: [],
    }));
    render(<DashboardPage />);

    expect(await screen.findByText('Stylist A')).toBeInTheDocument();
    expect(screen.getByText('Stylist B')).toBeInTheDocument();
    expect(getSlotsSpy).toHaveBeenCalledWith('res-1');
    expect(getSlotsSpy).toHaveBeenCalledWith('res-2');
    expect(screen.queryByText('No services yet')).not.toBeInTheDocument();
  });

  it('livetracker3.md §7.1: the full new-business reachability chain — creates a resource, then generates real availability for it', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    const createResourceSpy = vi
      .spyOn(resourcesApi, 'createResource')
      .mockResolvedValue({ id: 'res-new', name: 'Stylist A' });
    const createAvailabilitySpy = vi
      .spyOn(resourcesApi, 'createAvailability')
      .mockResolvedValue({ calendar_id: 'cal-1', slots_created: 4 });
    render(<DashboardPage />);

    expect(await screen.findByText('No services yet')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Name'), 'Stylist A');
    await user.selectOptions(screen.getByLabelText('Type'), 'stylist');
    await user.click(screen.getByRole('button', { name: 'Add resource' }));

    expect(createResourceSpy).toHaveBeenCalledWith('Stylist A', 'stylist');
    expect(await screen.findByText('Set up availability for Stylist A')).toBeInTheDocument();

    // livetracker3.md §7.1: real bug found live — the new resource used to never
    // appear in the list (loadResources() re-fetched using a stale, pre-creation
    // owned_resource_ids snapshot from account, which never included the new id).
    // The resource card must appear and the empty state must be gone, with no
    // page reload — proving the list updates from the create response directly.
    expect(screen.getAllByText('Stylist A').length).toBeGreaterThan(0);
    expect(screen.queryByText('No services yet')).not.toBeInTheDocument();

    // jsdom does not reliably simulate keystroke-by-keystroke typing into a native
    // <input type="date"> the way userEvent.type() does for text inputs — set the
    // value directly, matching this well-known testing-library/jsdom limitation.
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-08-10' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-08-11' } });
    await user.clear(screen.getByLabelText('Times (comma-separated, e.g. 09:00, 14:00)'));
    await user.type(
      screen.getByLabelText('Times (comma-separated, e.g. 09:00, 14:00)'),
      '09:00, 10:00'
    );
    await user.click(screen.getByRole('button', { name: 'Generate availability' }));

    expect(createAvailabilitySpy).toHaveBeenCalledWith(
      'res-new',
      expect.objectContaining({ times: ['09:00', '10:00'], slotDurationMinutes: 30, slotCapacity: 1 })
    );
    expect(await screen.findByText(/Availability generated for/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View availability dashboard' })).toHaveAttribute(
      'href',
      '/resources/res-new/availability'
    );
  });

  it('livetracker3.md §7.1 audit fix: creating a second resource does not discard the first one’s still-pending availability setup', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi
      .spyOn(resourcesApi, 'createResource')
      .mockResolvedValueOnce({ id: 'res-1', name: 'Stylist A' })
      .mockResolvedValueOnce({ id: 'res-2', name: 'Stylist B' });
    render(<DashboardPage />);

    expect(await screen.findByText('No services yet')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Name'), 'Stylist A');
    await user.click(screen.getByRole('button', { name: 'Add resource' }));
    expect(await screen.findByText('Set up availability for Stylist A')).toBeInTheDocument();

    // Real gap found on this phase's own post-close audit: creating a second
    // resource before finishing the first's availability setup used to silently
    // replace (not add to) the pending-setup form, permanently orphaning the
    // first resource with no UI path to ever generate its availability.
    await user.type(screen.getByLabelText('Name'), 'Stylist B');
    await user.click(screen.getByRole('button', { name: 'Add resource' }));

    expect(await screen.findByText('Set up availability for Stylist B')).toBeInTheDocument();
    expect(screen.getByText('Set up availability for Stylist A')).toBeInTheDocument();
  });
});
