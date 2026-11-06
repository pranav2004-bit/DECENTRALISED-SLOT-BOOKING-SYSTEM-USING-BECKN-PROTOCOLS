import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Real `useSearchParams()` returns null outside a Next router context (which
// jsdom here doesn't provide) — mocked the same way app/confirm/page.test.tsx
// already does, defaulting to no params so every pre-existing test (none of
// which care about a deep-linked domain) is unaffected.
const { mockUseSearchParams } = vi.hoisted(() => ({ mockUseSearchParams: vi.fn() }));
vi.mock('next/navigation', () => ({ useSearchParams: mockUseSearchParams }));

import SearchPage from './page';
import * as bookingApi from '@/lib/booking-api';
import * as postalApi from '@/lib/postal-api';
import * as geoApi from '@/lib/geo-api';
import type { SearchResultsResponse } from '@/lib/beckn-types';
import { HeaderContentProvider } from '@/components/shell/HeaderContext';
import { formatTime } from '@/lib/format';

// search/page.tsx calls usePageHeader() (components/shell/HeaderContext.tsx),
// which throws outside a HeaderContentProvider — AppShell provides this in the
// real app; tests render SearchPage standalone, so they need the same wrapper.
function renderSearchPage() {
  return render(
    <HeaderContentProvider>
      <SearchPage />
    </HeaderContentProvider>
  );
}

// The State/Division pickers are a custom listbox (components/ui/Dropdown), not a
// native <select> — opens on click, options are real DOM nodes with role="option".
async function chooseOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  optionText: string
) {
  await user.click(screen.getByLabelText(label));
  await user.click(await screen.findByRole('option', { name: optionText }));
}

/** Browsing only starts once a division is actually picked (app/search/page.tsx's
 * own "no division means nothing to show yet" gate) — every test that needs
 * triggerSearch to actually fire drives the real picker first, the same way a
 * customer would, rather than reaching into state directly. */
async function pickLocation(
  user: ReturnType<typeof userEvent.setup>,
  divisionName = 'Visakhapatnam Division'
) {
  await chooseOption(user, 'State', 'Andhra Pradesh');
  await waitFor(() => expect(screen.getByLabelText('Division')).not.toBeDisabled());
  await chooseOption(user, 'Division', divisionName);
}

function catalogResult(): SearchResultsResponse {
  return {
    transaction_id: 'tx-1',
    query: '',
    domain: 'ONDC:RET13',
    next_cursor: null,
    results: [
      {
        bpp_id: 'bpp-1',
        bpp_uri: 'https://bpp.example',
        catalog: {
          descriptor: { name: 'Beauty Catalog' },
          providers: [
            {
              id: 'provider-1',
              descriptor: { name: 'Glow Salon' },
              items: [
                {
                  id: 'item-1',
                  descriptor: { name: 'Haircut', short_desc: 'A classic cut' },
                  price: { currency: 'INR', value: '500.00' },
                },
              ],
            },
          ],
        },
      },
    ],
  };
}

describe('SearchPage', () => {
  beforeEach(() => {
    mockUseSearchParams.mockReturnValue(new URLSearchParams());
    // Default, single-division fixture — enough for every test that just needs
    // *a* location picked; tests exercising the picker itself override this.
    vi.spyOn(postalApi, 'fetchDivisions').mockResolvedValue([
      { id: 1, name: 'Visakhapatnam Division' },
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a "pick a location" prompt and never browses before a division is chosen', async () => {
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    renderSearchPage();

    expect(await screen.findByText('Select a location')).toBeInTheDocument();
    expect(trigger).not.toHaveBeenCalled();
  });

  it('browses with no typed query once a location is picked, drills into a provider, and reserves a slot with a link to the select screen', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    vi.spyOn(bookingApi, 'checkAvailability').mockResolvedValue({ 'item-1': true });
    const slotStart = '2026-08-01T10:00:00+00:00';
    vi.spyOn(bookingApi, 'listAvailableSlots').mockResolvedValue([
      { start_time: slotStart, end_time: '2026-08-01T10:30:00+00:00', status: 'available' },
    ]);
    vi.spyOn(bookingApi, 'triggerSelect').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSelectResult').mockResolvedValue({
      transaction_id: 'tx-1',
      selected_error: null,
      selected_order: { provider: { id: 'provider-1' }, items: [{ id: 'item-1' }], fulfillments: [] },
    });
    renderSearchPage();

    await pickLocation(user);

    expect(await screen.findByRole('status')).toHaveTextContent('Finding providers');
    expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', 'Visakhapatnam Division');

    await waitFor(() => expect(screen.getByText('Glow Salon')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Glow Salon' }));
    // Healthcare (this test's default domain) prepends "Dr." to item names —
    // doctorDisplayName() in app/search/page.tsx.
    await waitFor(() => expect(screen.getByText('Dr. Haircut')).toBeInTheDocument());

    await user.click(await screen.findByRole('button', { name: formatTime(slotStart) }));
    const link = await screen.findByRole('link', { name: 'Book this slot' });
    expect(link.getAttribute('href')).toContain('item_id=item-1');
    expect(link.getAttribute('href')).toContain('transaction_id=tx-1');
  });

  it('shows an error state with retry when the browse trigger fails', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockRejectedValue(new Error('network down'));
    renderSearchPage();

    await pickLocation(user);

    expect(await screen.findByText("Couldn't load providers")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows a rated provider\'s real average rating and count, and omits it for an unrated provider', async () => {
    const user = userEvent.setup({ delay: null });
    const result = catalogResult();
    result.results[0].catalog.providers[0].rating = '4.33';
    result.results[0].catalog.providers[0].rating_count = 3;
    result.results[0].catalog.providers.push({
      id: 'provider-2',
      descriptor: { name: 'Urban Cuts' },
      items: [
        {
          id: 'item-2',
          descriptor: { name: 'Beard Trim' },
          price: { currency: 'INR', value: '200.00' },
        },
      ],
    });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(result);
    renderSearchPage();

    await pickLocation(user);

    await waitFor(() => expect(screen.getByText('Glow Salon')).toBeInTheDocument());
    const glowSalonCard = screen.getByText('Glow Salon').closest('li');
    expect(glowSalonCard?.textContent).toContain('★ 4.33');
    expect(glowSalonCard?.textContent).toContain('(3 ratings)');

    const urbanCutsCard = screen.getByText('Urban Cuts').closest('li');
    expect(urbanCutsCard?.textContent).not.toContain('★');
  });

  it('shows an empty state once polling exhausts its attempts with no results', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-empty');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue({
      transaction_id: 'tx-empty',
      query: '',
      domain: 'ONDC:RET13',
      next_cursor: null,
      results: [],
    });
    renderSearchPage();

    await pickLocation(user);

    // 12 real attempts at a 1.5s interval — a genuine ~18s wall-clock wait,
    // not simulated, so this exercises the same maxAttempts cutoff a real
    // customer would experience.
    await waitFor(() => expect(screen.getByText('No providers found')).toBeInTheDocument(), {
      timeout: 22000,
    });
  }, 25000);

  it('shows a retry-able error state when polling for results fails', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockRejectedValue(new Error('network down'));
    renderSearchPage();

    await pickLocation(user);

    expect(await screen.findByText("Couldn't load results")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('livetracker3.md §6.1: defaults the category to Healthcare', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    renderSearchPage();

    await pickLocation(user);

    await waitFor(() =>
      expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', 'Visakhapatnam Division')
    );
  });

  it('deep-links from the home page: a valid ?domain= browses that domain once a location is picked', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    mockUseSearchParams.mockReturnValue(new URLSearchParams({ domain: 'BECKN:AUTO01' }));
    renderSearchPage();

    await pickLocation(user);

    await waitFor(() =>
      expect(trigger).toHaveBeenCalledWith('', 'BECKN:AUTO01', 'Visakhapatnam Division')
    );
  });

  it('an unrecognized ?domain= value is ignored, falling back to the first real domain', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    mockUseSearchParams.mockReturnValue(new URLSearchParams({ domain: 'NOT-A-REAL-DOMAIN' }));
    renderSearchPage();

    await pickLocation(user);

    await waitFor(() =>
      expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', 'Visakhapatnam Division')
    );
  });

  it('BookMyShow-style location picker: choosing a state then a division browses with the division', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    vi.spyOn(postalApi, 'fetchDivisions').mockResolvedValue([
      { id: 1, name: 'Visakhapatnam Division' },
      { id: 2, name: 'Vijayawada Division' },
    ]);
    renderSearchPage();

    expect(trigger).not.toHaveBeenCalled();

    await chooseOption(user, 'State', 'Andhra Pradesh');
    await waitFor(() => expect(screen.getByLabelText('Division')).not.toBeDisabled());
    await chooseOption(user, 'Division', 'Visakhapatnam Division');

    await waitFor(() =>
      expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', 'Visakhapatnam Division')
    );
  });

  it('"Detect my location" resolves a pincode to its division and state, and auto-fills both', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    vi.spyOn(geoApi, 'detectPincode').mockResolvedValue('530001');
    vi.spyOn(postalApi, 'lookupByPincode').mockResolvedValue({
      division: { id: 1, name: 'Visakhapatnam Division' },
      state: 'Andhra Pradesh',
    });
    vi.spyOn(postalApi, 'fetchDivisions').mockResolvedValue([
      { id: 1, name: 'Visakhapatnam Division' },
      { id: 2, name: 'Vijayawada Division' },
    ]);
    renderSearchPage();

    await user.click(screen.getByRole('button', { name: 'Detect my location' }));

    await waitFor(() => expect(screen.getByLabelText('State')).toHaveTextContent('Andhra Pradesh'));
    await waitFor(() =>
      expect(screen.getByLabelText('Division')).toHaveTextContent('Visakhapatnam Division')
    );
    await waitFor(() =>
      expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', 'Visakhapatnam Division')
    );
  });

  it('"Detect my location" shows a plain error when the location is outside coverage', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    vi.spyOn(geoApi, 'detectPincode').mockResolvedValue('999999');
    vi.spyOn(postalApi, 'lookupByPincode').mockResolvedValue(null);
    renderSearchPage();

    await user.click(screen.getByRole('button', { name: 'Detect my location' }));

    expect(await screen.findByText("That location isn't in our coverage yet")).toBeInTheDocument();
  });
});
