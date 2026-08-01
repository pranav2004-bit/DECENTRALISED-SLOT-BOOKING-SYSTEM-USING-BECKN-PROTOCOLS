import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush, mockRefresh } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import StaffPage from './page';
import * as authApi from '@/lib/auth-api';
import * as availabilityApi from '@/lib/availability-api';
import * as staffApi from '@/lib/staff-api';
import { ApiError } from '@/lib/api-client';

const OWNER_ACCOUNT = {
  id: 'biz-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER' as const,
  managed_by: null,
  owned_resource_ids: ['res-1'],
};

const RESOURCE = {
  id: 'res-1',
  name: 'Stylist A',
  average_rating: null,
  rating_count: 0,
  assigned_staff_id: null,
};

describe('StaffPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<StaffPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('livetracker3.md §8.1: redirects a STAFF login to its own assigned resource, not the staff-management screen', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      role: 'STAFF',
      assigned_resource_ids: ['res-9'],
    });
    render(<StaffPage />);

    await waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith('/resources/res-9/availability')
    );
  });

  it('shows the empty state for a brand-new owner with no staff yet', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([]);
    render(<StaffPage />);

    expect(await screen.findByText('No staff yet')).toBeInTheDocument();
  });

  it('lists pre-existing staff with their current assignment', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER_ACCOUNT);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: ['res-1'],
      },
    ]);
    render(<StaffPage />);

    // "Stylist One" appears twice — the staff row and the assign-picker's own
    // <option> — so this asserts presence, not uniqueness.
    expect(await screen.findAllByText('Stylist One')).not.toHaveLength(0);
    expect(screen.getByText('Assigned to Stylist A')).toBeInTheDocument();
  });

  it('livetracker3.md §8.1: creates a staff account and then assigns it to a resource', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER_ACCOUNT);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([]);
    const createStaffSpy = vi.spyOn(staffApi, 'createStaff').mockResolvedValue({
      id: 'staff-1',
      business_name: 'Stylist One',
      contact: 'stylist1@example.com',
      is_active: true,
      assigned_resource_ids: [],
    });
    const assignSpy = vi
      .spyOn(staffApi, 'assignStaffToResource')
      .mockResolvedValue({ id: 'res-1', assigned_staff_id: 'staff-1' });
    render(<StaffPage />);

    expect(await screen.findByText('No staff yet')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Staff name'), 'Stylist One');
    await user.type(screen.getByLabelText('Email'), 'stylist1@example.com');
    await user.type(screen.getByLabelText('Password'), 'a-strong-passw0rd!');
    await user.click(screen.getByRole('button', { name: 'Add staff' }));

    expect(createStaffSpy).toHaveBeenCalledWith(
      'Stylist One',
      'stylist1@example.com',
      'a-strong-passw0rd!'
    );
    // "Unassigned" appears twice at this point — the new staff row and the
    // still-unassigned resource row — so this asserts presence, not uniqueness.
    expect(await screen.findAllByText('Unassigned')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Assign' }));

    expect(assignSpy).toHaveBeenCalledWith('res-1', 'staff-1');
    expect(await screen.findByText('Assigned to Stylist One')).toBeInTheDocument();
  });

  it('livetracker3.md §8.1 audit fix: a staff account already assigned to one resource is not offered as assignable on another', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      owned_resource_ids: ['res-1', 'res-2'],
    });
    vi.spyOn(availabilityApi, 'getSlots').mockImplementation(async (id) => ({
      resource: {
        id,
        name: id === 'res-1' ? 'Stylist A' : 'Stylist B',
        average_rating: null,
        rating_count: 0,
        assigned_staff_id: id === 'res-1' ? 'staff-1' : null,
      },
      slots: [],
    }));
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: ['res-1'],
      },
    ]);
    render(<StaffPage />);

    expect(await screen.findByText('Assigned to Stylist One')).toBeInTheDocument();

    // SECURITY.md documents the system as one-Resource-per-staff by design; nothing
    // server-side enforces it, so the UI itself must not offer an already-assigned
    // staff account on a *different* resource's own picker. Stylist B has no
    // unassigned staff to offer, so no select/Assign control renders for it at all.
    expect(screen.queryByLabelText('Assign staff to Stylist B')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Assign staff to Stylist A')).toBeInTheDocument();
  });

  it('livetracker3.md §8.1 third audit fix: unassigns a staff account from a resource', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER_ACCOUNT);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({
      resource: { ...RESOURCE, assigned_staff_id: 'staff-1' },
      slots: [],
    });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: ['res-1'],
      },
    ]);
    const unassignSpy = vi
      .spyOn(staffApi, 'unassignStaffFromResource')
      .mockResolvedValue({ id: 'res-1', assigned_staff_id: null });
    render(<StaffPage />);

    expect(await screen.findByText('Assigned to Stylist A')).toBeInTheDocument();
    expect(screen.getByText('Assigned to Stylist One')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Unassign' }));

    expect(unassignSpy).toHaveBeenCalledWith('res-1');
    // "Unassigned" appears twice once cleared — the staff row and the resource row.
    expect(await screen.findAllByText('Unassigned')).toHaveLength(2);
    expect(screen.queryByText('Assigned to Stylist A')).not.toBeInTheDocument();
    expect(screen.queryByText('Assigned to Stylist One')).not.toBeInTheDocument();
    // Newly unassigned, so the picker should offer Stylist One again.
    expect(screen.getByLabelText('Assign staff to Stylist A')).toBeInTheDocument();
  });

  it('livetracker3.md §8.1 seventh audit fix: deactivates a staff account, clearing its resource assignment locally too', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER_ACCOUNT);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({
      resource: { ...RESOURCE, assigned_staff_id: 'staff-1' },
      slots: [],
    });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: ['res-1'],
      },
    ]);
    const setStaffActiveSpy = vi
      .spyOn(staffApi, 'setStaffActive')
      .mockResolvedValue({ id: 'staff-1', is_active: false });
    render(<StaffPage />);

    expect(await screen.findByText('Assigned to Stylist A')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Deactivate' }));

    expect(setStaffActiveSpy).toHaveBeenCalledWith('staff-1', false);
    expect(await screen.findByText('Inactive')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reactivate' })).toBeInTheDocument();
    // Deactivation clears the assignment server-side too — both the staff row and
    // the resource's own row must reflect that locally, without a reload.
    expect(screen.queryByText('Assigned to Stylist A')).not.toBeInTheDocument();
    expect(screen.queryByText('Assigned to Stylist One')).not.toBeInTheDocument();
  });

  it('livetracker3.md §8.1 seventh audit fix: reactivates a deactivated staff account', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: false,
        assigned_resource_ids: [],
      },
    ]);
    const setStaffActiveSpy = vi
      .spyOn(staffApi, 'setStaffActive')
      .mockResolvedValue({ id: 'staff-1', is_active: true });
    render(<StaffPage />);

    expect(await screen.findByText('Inactive')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Reactivate' }));

    expect(setStaffActiveSpy).toHaveBeenCalledWith('staff-1', true);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Deactivate' })).toBeInTheDocument()
    );
    expect(screen.queryByText('Inactive')).not.toBeInTheDocument();
  });

  it('livetracker3.md §8.1 seventh audit fix: a deactivated staff account is not offered as assignable', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER_ACCOUNT);
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({ resource: RESOURCE, slots: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: false,
        assigned_resource_ids: [],
      },
    ]);
    render(<StaffPage />);

    expect(await screen.findByText('Inactive')).toBeInTheDocument();
    expect(screen.queryByLabelText('Assign staff to Stylist A')).not.toBeInTheDocument();
  });

  it("livetracker3.md §8.1 ninth audit fix: resets a staff account's password", async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: [],
      },
    ]);
    const resetSpy = vi
      .spyOn(staffApi, 'resetStaffPassword')
      .mockResolvedValue({ id: 'staff-1' });
    render(<StaffPage />);

    expect(await screen.findByText('Stylist One')).toBeInTheDocument();
    expect(screen.queryByLabelText('New password for Stylist One')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Reset password' }));
    const passwordField = screen.getByLabelText('New password for Stylist One');
    await user.type(passwordField, 'a-different-strong-passw0rd!');
    await user.click(screen.getByRole('button', { name: 'Set password' }));

    expect(resetSpy).toHaveBeenCalledWith('staff-1', 'a-different-strong-passw0rd!');
    expect(await screen.findByText('Password updated.')).toBeInTheDocument();
    // Cleared after a successful reset, not left holding the just-set password.
    expect(passwordField).toHaveValue('');
  });

  it('livetracker3.md §8.1 ninth audit fix: shows the real API error and does not claim success on a failed password reset', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi.spyOn(staffApi, 'listStaff').mockResolvedValue([
      {
        id: 'staff-1',
        business_name: 'Stylist One',
        contact: 'stylist1@example.com',
        is_active: true,
        assigned_resource_ids: [],
      },
    ]);
    vi.spyOn(staffApi, 'resetStaffPassword').mockRejectedValue(
      new ApiError('This password is too common.', 400, null)
    );
    render(<StaffPage />);

    expect(await screen.findByText('Stylist One')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reset password' }));
    await user.type(screen.getByLabelText('New password for Stylist One'), 'password');
    await user.click(screen.getByRole('button', { name: 'Set password' }));

    expect(await screen.findByText('This password is too common.')).toBeInTheDocument();
    expect(screen.queryByText('Password updated.')).not.toBeInTheDocument();
  });
});
