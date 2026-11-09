import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush, mockRefresh } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRefresh: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import ResourcesPage from './page';
import * as authApi from '@/lib/auth-api';
import * as availabilityApi from '@/lib/availability-api';
import * as resourcesApi from '@/lib/resources-api';
import { ToastProvider } from '@/components/ui/Toast';

// StylistsView's row/form components call useToast() (same as DoctorsView's
// own DoctorRow/AddDoctorForm, which this mirrors) — the old GenericResourcesView
// this brand used before never did, so no test here needed a ToastProvider until
// now.
function renderResourcesPage() {
  return render(
    <ToastProvider>
      <ResourcesPage />
    </ToastProvider>
  );
}

const OWNER_ACCOUNT = {
  id: 'biz-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER' as const,
  managed_by: null,
  is_accepting_bookings: true,
  description: '',
  public_phone: '',
  public_email: '',
  address: '',
  state: '',
  division: '',
  pincode: '',
  created_at: '2026-01-01T00:00:00Z',
};

describe('ResourcesPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockRefresh.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    renderResourcesPage();

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('livetracker3.md §7.1: redirects a STAFF login to their own assigned resource, not the owner dashboard', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      role: 'STAFF',
      assigned_resource_ids: ['res-9'],
    });
    renderResourcesPage();

    await waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith('/resources/res-9/availability')
    );
  });

  it('livetracker3.md §7.1: shows the real empty state for a brand-new owner with zero resources', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    renderResourcesPage();

    // StylistsView (the Beauty-specific rich table, mirroring CareNest's own
    // Doctors table) replaced the old bare GenericResourcesView for this brand
    // — "No stylists yet", not "No services yet".
    expect(await screen.findByText('No stylists yet')).toBeInTheDocument();
  });

  it('livetracker3.md §7.1: lists an already-existing owner’s pre-existing resources (regression check)', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({
      ...OWNER_ACCOUNT,
      owned_resource_ids: ['res-1', 'res-2'],
    });
    const getSlotsSpy = vi.spyOn(availabilityApi, 'getSlots').mockImplementation(async (id) => ({
      resource: {
        id,
        name: id === 'res-1' ? 'Asha' : 'Divya',
        average_rating: null,
        rating_count: 0,
        assigned_staff_id: null,
        domain_data: {},
        is_active: true,
        price_currency: 'INR',
        price_value: '0.00',
        created_at: '2026-01-01T00:00:00Z',
      },
      slots: [],
    }));
    renderResourcesPage();

    expect(await screen.findByText('Asha')).toBeInTheDocument();
    expect(screen.getByText('Divya')).toBeInTheDocument();
    expect(getSlotsSpy).toHaveBeenCalledWith('res-1');
    expect(getSlotsSpy).toHaveBeenCalledWith('res-2');
    expect(screen.queryByText('No stylists yet')).not.toBeInTheDocument();
  });

  it("adapts CareNest's doctor-table UI for StyleNest's stylists: adds one with a service/experience/gender, then opens its availability modal", async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    const createResourceSpy = vi.spyOn(resourcesApi, 'createResource').mockResolvedValue({
      id: 'res-new',
      name: 'Asha',
      domain_data: {
        resource_type: 'stylist',
        staff_id: 'STY-0001',
        specialisation: 'Hair Coloring',
        years_of_experience: 4,
        gender: 'female',
      },
      is_active: true,
      price_currency: 'INR',
      price_value: '0.00',
      created_at: '2026-01-01T00:00:00Z',
    });
    vi.spyOn(availabilityApi, 'getSlots').mockResolvedValue({
      resource: {
        id: 'res-new',
        name: 'Asha',
        average_rating: null,
        rating_count: 0,
        assigned_staff_id: null,
        domain_data: {},
        is_active: true,
        price_currency: 'INR',
        price_value: '0.00',
        created_at: '2026-01-01T00:00:00Z',
      },
      slots: [],
    });
    renderResourcesPage();

    expect(await screen.findByText('No stylists yet')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add stylist' }));

    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('Name'), 'Asha');
    await user.selectOptions(within(dialog).getByLabelText('Service'), 'Hair Coloring');
    await user.type(within(dialog).getByLabelText('Years of experience'), '4');
    await user.selectOptions(within(dialog).getByLabelText('Gender'), 'female');
    await user.click(within(dialog).getByRole('button', { name: 'Add stylist' }));

    expect(createResourceSpy).toHaveBeenCalledWith('Asha', 'stylist', {
      specialisation: 'Hair Coloring',
      years_of_experience: 4,
      gender: 'female',
    });
    expect(await screen.findByText('Asha')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('Hair Coloring')).toBeInTheDocument();
    expect(screen.queryByText('No stylists yet')).not.toBeInTheDocument();

    // Availability is managed separately per stylist (the same Availability
    // button/modal CareNest's doctors use) — not an inline form shown
    // immediately after creation the way the old generic view worked.
    await user.click(screen.getByRole('button', { name: 'Availability' }));
    expect(await screen.findByText('Availability for Asha')).toBeInTheDocument();
  });

  it('creating a second stylist does not replace the first one in the table', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER_ACCOUNT, owned_resource_ids: [] });
    vi.spyOn(resourcesApi, 'createResource')
      .mockResolvedValueOnce({
        id: 'res-1',
        name: 'Asha',
        domain_data: { resource_type: 'stylist', staff_id: 'STY-0001' },
        is_active: true,
        price_currency: 'INR',
        price_value: '0.00',
        created_at: '2026-01-01T00:00:00Z',
      })
      .mockResolvedValueOnce({
        id: 'res-2',
        name: 'Divya',
        domain_data: { resource_type: 'stylist', staff_id: 'STY-0002' },
        is_active: true,
        price_currency: 'INR',
        price_value: '0.00',
        created_at: '2026-01-01T00:00:00Z',
      });
    renderResourcesPage();

    expect(await screen.findByText('No stylists yet')).toBeInTheDocument();

    async function addStylist(name: string) {
      await user.click(screen.getAllByRole('button', { name: 'Add stylist' })[0]);
      const dialog = screen.getByRole('dialog');
      await user.type(within(dialog).getByLabelText('Name'), name);
      await user.type(within(dialog).getByLabelText('Years of experience'), '2');
      await user.click(within(dialog).getByRole('button', { name: 'Add stylist' }));
      expect(await screen.findByText(name)).toBeInTheDocument();
    }

    await addStylist('Asha');
    await addStylist('Divya');

    expect(screen.getByText('Asha')).toBeInTheDocument();
    expect(screen.getByText('Divya')).toBeInTheDocument();
  });
});
