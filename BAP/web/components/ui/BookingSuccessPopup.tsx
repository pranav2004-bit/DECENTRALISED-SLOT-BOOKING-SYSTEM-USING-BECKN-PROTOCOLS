'use client';

import { formatDateTime, shortId } from '@/lib/format';
import type { Order } from '@/lib/beckn-types';

/** Shown once a booking is genuinely done — originally shown right after
 * /confirm itself; now shown only after a real payment has also succeeded
 * (see app/payment/page.tsx), since a booking isn't actually "done" from the
 * customer's side until both have happened. Extracted here (was previously
 * local to app/select/page.tsx) so both callers render the exact same thing. */
export function BookingSuccessPopup({
  order,
  providerName,
  itemName,
  fee,
  onClose,
}: {
  order: Order;
  providerName: string;
  itemName: string;
  fee: string | null;
  onClose: () => void;
}) {
  const fulfillment = order.fulfillments[0];
  const stop = fulfillment?.stops?.[0];
  const bookingId = fulfillment?.id ?? order.id ?? '';
  const customerId = fulfillment?.customer_id ?? '';
  const customerName = stop?.person?.name ?? '';
  const timestamp = stop?.time.timestamp;
  // Full ids are kept (not shortened) for anything sent anywhere — this is
  // purely what the customer sees, on-screen and on the downloaded invoice.
  const shortBookingId = shortId(bookingId);
  const shortCustomerId = shortId(customerId);

  async function downloadInvoice() {
    const { jsPDF } = await import('jspdf');
    const rows: [string, string][] = [
      ['Booking ID', shortBookingId],
      ['Customer ID', shortCustomerId],
      ['Customer name', customerName],
      ['Hospital', providerName],
      ['Doctor', itemName],
      ['Date & time', formatDateTime(timestamp)],
      ['Fee', fee ?? ''],
    ];

    const doc = new jsPDF({ unit: 'pt', format: 'a4' });
    const marginX = 48;
    let y = 64;

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(18);
    doc.text('Booking confirmed', marginX, y);
    y += 20;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(11);
    doc.setTextColor(115, 115, 115);
    doc.text('Appointment invoice', marginX, y);
    y += 32;

    const rowHeight = 28;
    const labelX = marginX;
    const valueX = 547; // right-aligned column, within the A4 page's margins
    doc.setFontSize(12);
    for (const [label, value] of rows) {
      doc.setDrawColor(229, 229, 229);
      doc.line(marginX, y + 8, valueX, y + 8);
      doc.setTextColor(115, 115, 115);
      doc.setFont('helvetica', 'normal');
      doc.text(label, labelX, y);
      doc.setTextColor(23, 23, 23);
      doc.setFont('helvetica', 'bold');
      doc.text(value || '—', valueX, y, { align: 'right' });
      y += rowHeight;
    }

    doc.save(`invoice-${shortBookingId || 'booking'}.pdf`);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-label="Booking confirmed" className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-100">
            <svg viewBox="0 0 24 24" className="h-6 w-6 text-green-600" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m5 12 5 5 9-10" />
            </svg>
          </div>
          <h2 className="mt-3 text-lg font-semibold text-neutral-900">Booking confirmed</h2>
          <p className="mt-1 text-sm text-neutral-600">
            {itemName} — {providerName}
          </p>
        </div>
        <dl className="mt-5 flex flex-col gap-2 border-t border-neutral-200 pt-4 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-neutral-500">Date &amp; time</dt>
            <dd className="text-right font-medium text-neutral-900">{formatDateTime(timestamp)}</dd>
          </div>
          {bookingId && (
            <div className="flex justify-between gap-4">
              <dt className="text-neutral-500">Booking ID</dt>
              <dd className="truncate text-right font-medium text-neutral-900">{shortBookingId}</dd>
            </div>
          )}
          {customerId && (
            <div className="flex justify-between gap-4">
              <dt className="text-neutral-500">Customer ID</dt>
              <dd className="truncate text-right font-medium text-neutral-900">{shortCustomerId}</dd>
            </div>
          )}
        </dl>
        <div className="mt-6 flex flex-col gap-2">
          <button
            type="button"
            onClick={downloadInvoice}
            className="rounded-md bg-[var(--brand-600)] px-4 py-2.5 text-sm font-semibold text-white"
          >
            Download invoice
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-neutral-300 px-4 py-2.5 text-sm font-medium text-neutral-700"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
