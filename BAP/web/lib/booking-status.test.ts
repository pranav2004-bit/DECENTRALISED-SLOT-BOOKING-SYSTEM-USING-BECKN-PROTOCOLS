import { describe, expect, it } from 'vitest';
import { deriveBookingListStatus } from './booking-status';
import type { Order } from './beckn-types';

function order(status: string): Order {
  return { id: 'o-1', status, provider: { id: 'p-1' }, items: [{ id: 'i-1' }], fulfillments: [] };
}

const CONFIRMED = order('ACTIVE');

describe('deriveBookingListStatus', () => {
  it('returns CANCELLED when cancelled_order is present, even if updated_order also is', () => {
    expect(
      deriveBookingListStatus({
        confirmed_order: CONFIRMED,
        cancelled_order: order('CANCELLED'),
        updated_order: order('ACTIVE'),
      })
    ).toBe('CANCELLED');
  });

  it('returns RESCHEDULED when only updated_order is present', () => {
    expect(
      deriveBookingListStatus({
        confirmed_order: CONFIRMED,
        cancelled_order: null,
        updated_order: order('ACTIVE'),
      })
    ).toBe('RESCHEDULED');
  });

  it('returns ACTIVE for a plain confirmed booking', () => {
    expect(
      deriveBookingListStatus({
        confirmed_order: CONFIRMED,
        cancelled_order: null,
        updated_order: null,
      })
    ).toBe('ACTIVE');
  });

  it('returns COMPLETE when confirmed_order.status is COMPLETE', () => {
    expect(
      deriveBookingListStatus({
        confirmed_order: order('COMPLETE'),
        cancelled_order: null,
        updated_order: null,
      })
    ).toBe('COMPLETE');
  });
});
