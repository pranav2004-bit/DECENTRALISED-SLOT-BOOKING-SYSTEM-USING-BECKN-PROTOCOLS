'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { me, type BusinessAccount } from '@/lib/auth-api';
import { fetchOwnedResources } from '@/lib/resources-api';
import type { ResourceInfo } from '@/lib/availability-api';
import { fetchOrders, updateFulfillmentStatus, type OrderRow } from '@/lib/orders-api';
import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';
import { shortId } from '@/lib/format';
import { BRAND } from '@/lib/brand';

/**
 * Orders page — the date-browsing shell (current month strip + year/month/date
 * History drill-down) feeding a per-date Bookings table. Row data now comes
 * from the real `orders_list_view` on page load, kept live afterward by the
 * same `/ws/business/orders/` broadcast the backend already fires on every
 * real confirm (`core/realtime.py`'s `broadcast_order_confirmed`) — no
 * separate "did a new booking land" poll needed.
 */

type BookingStatus = 'PENDING' | 'COMPLETED';
// Mirrors shared/inventory_core/models.py's Booking.payment_status exactly —
// a best-effort mirror of BAP's own PaymentTransaction.Status, '' meaning
// "unknown/not paid yet", not "confirmed unpaid".
type PaymentStatus = 'SUCCEEDED' | 'REFUNDED' | 'PARTIALLY_REFUNDED' | '';

interface BookingRow {
  customerId: string;
  bookingId: string;
  customerName: string;
  customerEmail: string;
  customerPhone: string;
  doctorName: string;
  slotStart: string;
  slotEnd: string;
  amount: number;
  currency: string;
  paymentStatus: PaymentStatus;
  status: BookingStatus;
}

/** `OrderRow` (the real backend shape, project-defined field names kept honest
 * about what they are) -> `BookingRow` (this table's own display shape). A
 * `CANCELLED` booking never reaches this table — it was never a completed
 * transaction a business needs to track here. `NO_SHOW` folds into
 * `COMPLETED` for this table's own deliberately-simplified two-state view
 * (see `order_fulfillment_status_update_view`'s docstring for the full
 * real-state-machine reasoning) — both are terminal, resolved outcomes. */
function toBookingRow(order: OrderRow): BookingRow | null {
  if (order.status === 'CANCELLED') return null;
  return {
    customerId: order.customer_id,
    bookingId: order.booking_id,
    customerName: order.customer_name,
    customerEmail: order.customer_email,
    customerPhone: order.customer_phone,
    doctorName: order.resource_name,
    slotStart: order.slot_time,
    slotEnd: order.slot_end_time,
    amount: order.amount ? Number(order.amount) : 0,
    currency: order.currency || 'INR',
    paymentStatus: (order.payment_status || '') as PaymentStatus,
    status: order.fulfillment_status === 'SCHEDULED' || order.fulfillment_status === 'IN_PROGRESS'
      ? 'PENDING'
      : 'COMPLETED',
  };
}

const PAYMENT_STATUS_LABEL: Record<Exclude<PaymentStatus, ''>, string> = {
  SUCCEEDED: 'Paid',
  REFUNDED: 'Refunded',
  PARTIALLY_REFUNDED: 'Partially refunded',
};

const PAYMENT_STATUS_CLASS: Record<Exclude<PaymentStatus, ''>, string> = {
  SUCCEEDED: 'bg-green-100 text-green-800',
  REFUNDED: 'bg-neutral-100 text-neutral-600',
  PARTIALLY_REFUNDED: 'bg-amber-100 text-amber-800',
};

function formatSlotTime(startIso: string, endIso: string): string {
  const start = new Date(startIso);
  const end = new Date(endIso);
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const durationMinutes = Math.round((end.getTime() - start.getTime()) / 60000);
  return `${time(start)} – ${time(end)} (${durationMinutes} min)`;
}

function BookingsTable({
  rows,
  selectedDate,
  search,
  statusFilter,
  doctorFilter,
  onUpdateStatus,
}: {
  rows: BookingRow[];
  selectedDate: Date;
  search: string;
  statusFilter: 'ALL' | BookingStatus;
  doctorFilter: string;
  onUpdateStatus: (bookingId: string, status: BookingStatus) => void;
}) {
  const dateKey = selectedDate.toDateString();
  const dayRows = useMemo(
    () => rows.filter((r) => new Date(r.slotStart).toDateString() === dateKey),
    [rows, dateKey]
  );

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return dayRows.filter((row) => {
      if (statusFilter !== 'ALL' && row.status !== statusFilter) return false;
      if (doctorFilter !== 'ALL' && row.doctorName !== doctorFilter) return false;
      if (
        query &&
        !row.customerId.toLowerCase().includes(query) &&
        !row.bookingId.toLowerCase().includes(query)
      ) {
        return false;
      }
      return true;
    });
  }, [dayRows, search, statusFilter, doctorFilter]);

  return (
    <div className="flex flex-col gap-3">
      <div className="w-full overflow-x-auto overflow-y-hidden rounded-lg border border-neutral-200 bg-white">
        <table className="w-full min-w-[1200px] border-collapse">
          <thead>
            <tr className="bg-neutral-50 text-left text-xs font-bold uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3">Customer ID</th>
              <th className="px-4 py-3">Booking ID</th>
              <th className="px-4 py-3">Customer name</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Phone</th>
              <th className="px-4 py-3">Consulted {BRAND.resourceNounSingular.toLowerCase()}</th>
              <th className="px-4 py-3">Slot time</th>
              <th className="px-4 py-3">Amount (INR)</th>
              <th className="px-4 py-3">Payment status</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-sm text-neutral-500">
                  {dayRows.length === 0
                    ? 'No bookings for this date.'
                    : 'No bookings match the current search/filters.'}
                </td>
              </tr>
            ) : (
              filteredRows.map((row) => (
                <tr key={row.bookingId} className="border-t border-neutral-200">
                  <td className="px-4 py-3 text-sm font-mono text-neutral-600" title={row.customerId}>
                    {shortId(row.customerId)}
                  </td>
                  <td className="px-4 py-3 text-sm font-mono text-neutral-600" title={row.bookingId}>
                    {shortId(row.bookingId)}
                  </td>
                  <td className="px-4 py-3 text-sm font-medium text-neutral-900">{row.customerName}</td>
                  <td className="px-4 py-3 text-sm text-neutral-700">{row.customerEmail}</td>
                  <td className="px-4 py-3 text-sm text-neutral-700">{row.customerPhone}</td>
                  <td className="px-4 py-3 text-sm text-neutral-700">{row.doctorName}</td>
                  <td className="px-4 py-3 text-sm text-neutral-700">
                    {formatSlotTime(row.slotStart, row.slotEnd)}
                  </td>
                  <td className="px-4 py-3 text-sm text-neutral-700">{row.amount.toFixed(2)}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        row.paymentStatus ? PAYMENT_STATUS_CLASS[row.paymentStatus] : 'bg-neutral-100 text-neutral-500'
                      }`}
                    >
                      {row.paymentStatus ? PAYMENT_STATUS_LABEL[row.paymentStatus] : 'Unknown'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <select
                      value={row.status}
                      onChange={(e) => onUpdateStatus(row.bookingId, e.target.value as BookingStatus)}
                      aria-label={`Status for booking ${row.bookingId}`}
                      className="rounded-md border-2 border-neutral-400 bg-white px-2 py-1 text-sm font-medium text-neutral-900 shadow-sm hover:border-neutral-500 focus:outline-none focus:ring-2 focus:ring-neutral-900"
                    >
                      <option value="PENDING">Pending</option>
                      <option value="COMPLETED">Completed</option>
                    </select>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const WEEKDAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function formatFullDate(date: Date): string {
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

function OrdersCalendar({ account }: { account: BusinessAccount }) {
  // Stable for the component's lifetime — re-deriving "now" on every render
  // would shift the strip/selection out from under the owner mid-interaction.
  const today = useMemo(() => new Date(), []);
  const [view, setView] = useState<'calendar' | 'history'>('calendar');
  const [selectedDateKey, setSelectedDateKey] = useState(today.toDateString());
  // The month the strip itself is showing — starts on today's real month,
  // but follows whatever date was last picked (including from History), so
  // picking e.g. June 2025 actually switches the strip to June 2025 instead
  // of silently staying on today's month while only the placeholder below
  // updates.
  const [viewedYear, setViewedYear] = useState(today.getFullYear());
  const [viewedMonth, setViewedMonth] = useState(today.getMonth());

  const [historyStep, setHistoryStep] = useState<'years' | 'months' | 'dates'>('years');
  const [historyYear, setHistoryYear] = useState<number | null>(null);
  const [historyMonth, setHistoryMonth] = useState<number | null>(null);

  // Lifted up from the table itself so the controls can sit in the header
  // row alongside the month title and History button.
  const [bookingSearch, setBookingSearch] = useState('');
  const [bookingStatusFilter, setBookingStatusFilter] = useState<'ALL' | BookingStatus>('ALL');
  const [bookingDoctorFilter, setBookingDoctorFilter] = useState('ALL');

  const scrollRef = useRef<HTMLDivElement>(null);
  function scrollStrip(direction: 1 | -1) {
    scrollRef.current?.scrollBy({ left: direction * 240, behavior: 'smooth' });
  }

  const viewedMonthDays = useMemo(() => {
    return Array.from(
      { length: daysInMonth(viewedYear, viewedMonth) },
      (_, i) => new Date(viewedYear, viewedMonth, i + 1)
    );
  }, [viewedYear, viewedMonth]);

  const isViewingCurrentMonth = viewedYear === today.getFullYear() && viewedMonth === today.getMonth();

  // History is bounded below by the business's own "date of joining" — never
  // earlier, since no order could predate the account itself — and bounded
  // above by two months ahead of today, not by the calendar year: a booking
  // taken today for a date next month is real data now, and the forward
  // window has to reach it even when that crosses into a new year (e.g. on
  // Dec 15, "2 months ahead" is Feb — month index 11+2=13 rolls Date's year
  // forward automatically, no manual wraparound math needed).
  const joiningDate = new Date(account.created_at);
  const joiningYear = joiningDate.getFullYear();
  const forwardLimit = new Date(today.getFullYear(), today.getMonth() + 2, 1);
  const forwardYear = forwardLimit.getFullYear();
  const forwardMonth = forwardLimit.getMonth();
  const historyYears: number[] = [];
  for (let y = forwardYear; y >= joiningYear; y--) historyYears.push(y);

  function monthsForHistoryYear(year: number): number[] {
    const startMonth = year === joiningYear ? joiningDate.getMonth() : 0;
    const endMonth = year === forwardYear ? forwardMonth : 11;
    const months: number[] = [];
    for (let m = endMonth; m >= startMonth; m--) months.push(m);
    return months;
  }

  function datesForHistoryMonth(year: number, month: number): Date[] {
    return Array.from({ length: daysInMonth(year, month) }, (_, i) => new Date(year, month, i + 1));
  }

  function openHistory() {
    setHistoryStep('years');
    setHistoryYear(null);
    setHistoryMonth(null);
    setView('history');
  }

  function selectDate(date: Date) {
    setSelectedDateKey(date.toDateString());
    setViewedYear(date.getFullYear());
    setViewedMonth(date.getMonth());
    setView('calendar');
  }

  function jumpToToday() {
    selectDate(today);
  }

  const selectedDate = new Date(selectedDateKey);

  // Doctor filter options — owner sees every owned resource, staff only their
  // own assigned one(s). Re-fetched on mount/account change, so adding or
  // removing a doctor on the Doctors page is reflected here automatically
  // the next time this page loads, never a hardcoded list.
  const resourceIds = useMemo(
    () => (account.role === 'OWNER' ? account.owned_resource_ids ?? [] : account.assigned_resource_ids ?? []),
    [account.role, account.owned_resource_ids, account.assigned_resource_ids]
  );
  const [doctors, setDoctors] = useState<ResourceInfo[]>([]);
  useEffect(() => {
    let ignore = false;
    fetchOwnedResources(resourceIds).then((result) => {
      if (!ignore) setDoctors(result);
    });
    return () => {
      ignore = true;
    };
  }, [resourceIds]);
  const doctorNames = useMemo(() => doctors.map((d) => d.name), [doctors]);

  // Real bookings — fetched once on mount, then kept current two ways: the
  // live WebSocket push below (a genuinely new confirm elsewhere, this
  // instant) and this page's own optimistic status-dropdown edits.
  const [rows, setRows] = useState<BookingRow[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchOrders().then((result) => {
      if (cancelled) return;
      setRows(result.orders.map(toBookingRow).filter((r): r is BookingRow => r !== null));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleRealtimeMessage = useRef((message: unknown) => {
    if (
      typeof message !== 'object' ||
      message === null ||
      !('type' in message) ||
      (message as { type: unknown }).type !== 'order.confirmed'
    ) {
      return;
    }
    const row = toBookingRow((message as unknown as { order: OrderRow }).order);
    if (!row) return;
    setRows((prev) => {
      const exists = prev.some((r) => r.bookingId === row.bookingId);
      return exists ? prev.map((r) => (r.bookingId === row.bookingId ? row : r)) : [row, ...prev];
    });
  });
  useRealtimeConnection('/ws/business/orders/', (m) => handleRealtimeMessage.current(m));

  async function handleUpdateStatus(bookingId: string, status: BookingStatus) {
    const previous = rows;
    // Optimistic — the table should feel instant; a failed request below
    // rolls this back rather than leaving a status the server never accepted.
    setRows((prev) => prev.map((r) => (r.bookingId === bookingId ? { ...r, status } : r)));
    try {
      await updateFulfillmentStatus(bookingId, status);
    } catch {
      setRows(previous);
    }
  }

  return (
    <div className="flex w-full flex-1 flex-col gap-4 px-4 py-6 sm:px-6 lg:px-8">
      {view === 'calendar' ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
              {MONTH_NAMES[viewedMonth]} {viewedYear}
            </h1>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <input
                  type="text"
                  value={bookingSearch}
                  onChange={(e) => setBookingSearch(e.target.value)}
                  placeholder="Customer ID or Booking ID"
                  aria-label="Search bookings"
                  className="w-56 rounded-md border border-neutral-300 bg-white px-3 py-1.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
                />
                {bookingSearch && (
                  <button
                    type="button"
                    onClick={() => setBookingSearch('')}
                    aria-label="Clear search"
                    className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer text-neutral-400 hover:text-neutral-700"
                  >
                    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>
              <select
                value={bookingStatusFilter}
                onChange={(e) => setBookingStatusFilter(e.target.value as typeof bookingStatusFilter)}
                aria-label="Filter by status"
                className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
              >
                <option value="ALL">All statuses</option>
                <option value="PENDING">Pending</option>
                <option value="COMPLETED">Completed</option>
              </select>
              <select
                value={bookingDoctorFilter}
                onChange={(e) => setBookingDoctorFilter(e.target.value)}
                aria-label={`Filter by consulted ${BRAND.resourceNounSingular.toLowerCase()}`}
                className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
              >
                <option value="ALL">All {BRAND.resourceNounPlural.toLowerCase()}</option>
                {doctorNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {(bookingSearch || bookingStatusFilter !== 'ALL' || bookingDoctorFilter !== 'ALL') && (
                <button
                  type="button"
                  onClick={() => {
                    setBookingSearch('');
                    setBookingStatusFilter('ALL');
                    setBookingDoctorFilter('ALL');
                  }}
                  className="cursor-pointer rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
                >
                  Clear filters
                </button>
              )}
              {!isViewingCurrentMonth && (
                <button
                  type="button"
                  onClick={jumpToToday}
                  className="cursor-pointer rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
                >
                  Today
                </button>
              )}
              <button
                type="button"
                onClick={openHistory}
                className="cursor-pointer rounded-md border border-[var(--brand-600)] px-3 py-1.5 text-sm font-medium text-[var(--brand-700)] hover:bg-[var(--brand-50)]"
              >
                History
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => scrollStrip(-1)}
              aria-label="Scroll to earlier dates"
              className="flex h-8 w-8 flex-none cursor-pointer items-center justify-center rounded-full border border-neutral-300 text-neutral-600 hover:bg-neutral-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 18l-6-6 6-6" />
              </svg>
            </button>
            <div
              ref={scrollRef}
              className="flex flex-1 gap-2 overflow-x-auto scroll-smooth [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            >
              {viewedMonthDays.map((date) => {
                const key = date.toDateString();
                const active = key === selectedDateKey;
                const isToday = key === today.toDateString();
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => selectDate(date)}
                    aria-label={`${WEEKDAY_SHORT[date.getDay()]} ${date.getDate()}`}
                    aria-pressed={active}
                    className={
                      active
                        ? 'flex min-w-[48px] flex-none cursor-pointer flex-col items-center rounded-lg bg-[var(--brand-600)] py-1.5 text-white'
                        : isToday
                          ? 'flex min-w-[48px] flex-none cursor-pointer flex-col items-center rounded-lg border border-[var(--brand-600)] py-1.5 text-[var(--brand-700)] hover:bg-[var(--brand-50)]'
                          : 'flex min-w-[48px] flex-none cursor-pointer flex-col items-center rounded-lg bg-neutral-100 py-1.5 text-neutral-700 hover:bg-neutral-200'
                    }
                  >
                    <span className="text-xs font-medium tracking-wide">
                      {WEEKDAY_SHORT[date.getDay()]}
                    </span>
                    <span className="text-sm font-bold">{date.getDate()}</span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => scrollStrip(1)}
              aria-label="Scroll to later dates"
              className="flex h-8 w-8 flex-none cursor-pointer items-center justify-center rounded-full border border-neutral-300 text-neutral-600 hover:bg-neutral-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 18l6-6-6-6" />
              </svg>
            </button>
          </div>

          <BookingsTable
            rows={rows}
            selectedDate={selectedDate}
            search={bookingSearch}
            statusFilter={bookingStatusFilter}
            doctorFilter={bookingDoctorFilter}
            onUpdateStatus={handleUpdateStatus}
          />
        </>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => {
                if (historyStep === 'years') {
                  setView('calendar');
                } else if (historyStep === 'months') {
                  setHistoryStep('years');
                  setHistoryYear(null);
                } else {
                  setHistoryStep('months');
                  setHistoryMonth(null);
                }
              }}
              className="flex cursor-pointer items-center gap-1 rounded-md py-1 pr-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 18l-6-6 6-6" />
              </svg>
              Back
            </button>
            <span className="h-4 w-px bg-neutral-300" aria-hidden="true" />
            <p className="text-sm font-semibold text-neutral-900">
              {historyStep === 'years' && 'Select a year'}
              {historyStep === 'months' && historyYear}
              {historyStep === 'dates' && historyYear !== null && historyMonth !== null &&
                `${MONTH_NAMES[historyMonth]} ${historyYear}`}
            </p>
          </div>

          {historyStep === 'years' && (
            <div className="flex flex-wrap gap-2">
              {historyYears.map((year) => (
                <button
                  key={year}
                  type="button"
                  onClick={() => {
                    setHistoryYear(year);
                    setHistoryStep('months');
                  }}
                  className="cursor-pointer rounded-full bg-neutral-100 px-5 py-2 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                >
                  {year}
                </button>
              ))}
            </div>
          )}

          {historyStep === 'months' && historyYear !== null && (
            <div className="flex flex-wrap gap-2">
              {monthsForHistoryYear(historyYear).map((month) => (
                <button
                  key={month}
                  type="button"
                  onClick={() => {
                    setHistoryMonth(month);
                    setHistoryStep('dates');
                  }}
                  className="cursor-pointer rounded-full bg-neutral-100 px-4 py-2 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                >
                  {MONTH_NAMES[month].slice(0, 3)}
                </button>
              ))}
            </div>
          )}

          {historyStep === 'dates' && historyYear !== null && historyMonth !== null && (
            <div className="flex flex-wrap gap-2">
              {datesForHistoryMonth(historyYear, historyMonth).map((date) => (
                <button
                  key={date.toDateString()}
                  type="button"
                  onClick={() => selectDate(date)}
                  aria-label={formatFullDate(date)}
                  className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-full bg-neutral-100 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                >
                  {date.getDate()}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function OrdersPage() {
  const router = useRouter();
  const [account, setAccount] = useState<BusinessAccount | null | 'loading'>('loading');

  useEffect(() => {
    let cancelled = false;
    me().then((result) => {
      if (cancelled) return;
      if (result === null) {
        router.push('/login');
        return;
      }
      setAccount(result);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (account === 'loading' || account === null) return <LoadingState label="Checking session…" />;
  return <OrdersCalendar account={account} />;
}
