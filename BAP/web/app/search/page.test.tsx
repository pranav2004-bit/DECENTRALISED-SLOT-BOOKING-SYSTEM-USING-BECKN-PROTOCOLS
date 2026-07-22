import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SearchPage from './page';
import * as bookingApi from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import type { SearchResultsResponse } from '@/lib/beckn-types';

function catalogResult(): SearchResultsResponse {
  return {
    transaction_id: 'tx-1',
    query: 'haircut',
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
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a submit error when the search trigger fails', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockRejectedValue(
      new ApiError('search is temporarily unavailable', 502, null)
    );
    render(<SearchPage />);

    await user.type(screen.getByLabelText('What are you looking for?'), 'haircut');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('search is temporarily unavailable');
  });

  it('shows a loading state, then renders results with a link to the select screen', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-1');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue(catalogResult());
    render(<SearchPage />);

    await user.type(screen.getByLabelText('What are you looking for?'), 'haircut');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Searching providers');

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.getByText('Glow Salon')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Choose a time' });
    expect(link.getAttribute('href')).toContain('item_id=item-1');
    expect(link.getAttribute('href')).toContain('transaction_id=tx-1');
  });

  it('shows an empty state once polling exhausts its attempts with no results', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSearch').mockResolvedValue('tx-empty');
    vi.spyOn(bookingApi, 'getSearchResults').mockResolvedValue({
      transaction_id: 'tx-empty',
      query: 'haircut',
      domain: 'ONDC:RET13',
      next_cursor: null,
      results: [],
    });
    render(<SearchPage />);

    await user.type(screen.getByLabelText('What are you looking for?'), 'haircut');
    await user.click(screen.getByRole('button', { name: 'Search' }));

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
    render(<SearchPage />);

    await user.type(screen.getByLabelText('What are you looking for?'), 'haircut');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByText("Couldn't load results")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});
