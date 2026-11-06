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
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('browses automatically on load, with no typed query, and renders results with a link to the select screen', async () => {
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    render(<SearchPage />);

    expect(await screen.findByRole('status')).toHaveTextContent('Finding providers');
    expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', undefined);

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.getByText('Glow Salon')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Choose a time' });
    expect(link.getAttribute('href')).toContain('item_id=item-1');
    expect(link.getAttribute('href')).toContain('transaction_id=tx-1');
  });

  it('shows an error state with retry when the initial browse trigger fails', async () => {
    vi.spyOn(bookingApi, 'triggerSearch').mockRejectedValue(new Error('network down'));
    render(<SearchPage />);

    expect(await screen.findByText("Couldn't load providers")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows a rated item\'s real average rating and count, and omits it for an unrated item', async () => {
    const result = catalogResult();
    result.results[0].catalog.providers[0].items[0].rating = '4.33';
    result.results[0].catalog.providers[0].items[0].rating_count = 3;
    result.results[0].catalog.providers[0].items.push({
      id: 'item-2',
      descriptor: { name: 'Beard Trim' },
      price: { currency: 'INR', value: '200.00' },
    });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(result);
    render(<SearchPage />);

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.getByText('★ 4.33')).toBeInTheDocument();
    expect(screen.getByText('(3 ratings)')).toBeInTheDocument();

    const beardTrimItem = screen.getByText('Beard Trim').closest('li');
    expect(beardTrimItem?.textContent).not.toContain('★');
  });

  it('shows an empty state once polling exhausts its attempts with no results', async () => {
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-empty');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue({
      transaction_id: 'tx-empty',
      query: '',
      domain: 'ONDC:RET13',
      next_cursor: null,
      results: [],
    });
    render(<SearchPage />);

    // 12 real attempts at a 1.5s interval — a genuine ~18s wall-clock wait,
    // not simulated, so this exercises the same maxAttempts cutoff a real
    // customer would experience.
    await waitFor(() => expect(screen.getByText('No providers found')).toBeInTheDocument(), {
      timeout: 22000,
    });
  }, 25000);

  it('shows a retry-able error state when polling for results fails', async () => {
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockRejectedValue(new Error('network down'));
    render(<SearchPage />);

    expect(await screen.findByText("Couldn't load results")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('livetracker3.md §6.1: defaults the category to Healthcare and browses it on load', async () => {
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    render(<SearchPage />);

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', undefined));
  });

  it('deep-links from the home page: a valid ?domain= browses that domain on load', async () => {
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    mockUseSearchParams.mockReturnValue(new URLSearchParams({ domain: 'BECKN:AUTO01' }));
    render(<SearchPage />);

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('', 'BECKN:AUTO01', undefined));
  });

  it('an unrecognized ?domain= value is ignored, falling back to the first real domain', async () => {
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    mockUseSearchParams.mockReturnValue(new URLSearchParams({ domain: 'NOT-A-REAL-DOMAIN' }));
    render(<SearchPage />);

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', undefined));
  });

  it('BookMyShow-style location picker: choosing a state then a division re-browses with the division', async () => {
    const user = userEvent.setup({ delay: null });
    const trigger = vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    vi.spyOn(postalApi, 'fetchDivisions').mockResolvedValue([
      { id: 1, name: 'Visakhapatnam Division' },
      { id: 2, name: 'Vijayawada Division' },
    ]);
    render(<SearchPage />);

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', undefined));

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
    render(<SearchPage />);

    await waitFor(() => expect(trigger).toHaveBeenCalledWith('', 'ONDC:SRV13', undefined));

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
    render(<SearchPage />);

    await user.click(screen.getByRole('button', { name: 'Detect my location' }));

    expect(await screen.findByText("That location isn't in our coverage yet")).toBeInTheDocument();
  });
});
