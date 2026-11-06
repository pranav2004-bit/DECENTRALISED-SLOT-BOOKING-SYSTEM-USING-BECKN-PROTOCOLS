export function formatPrice(price: { currency: string; value: string } | undefined | null): string {
  if (!price) return '';
  const amount = Number(price.value);
  if (Number.isNaN(amount)) return `${price.currency} ${price.value}`;
  // Indian commerce convention (Razorpay/PhonePe/Zomato etc.): a whole-rupee amount
  // shows as "₹500", not "₹500.00" — decimals only appear when there are real paise.
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: price.currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatDateTime(isoTimestamp: string | undefined | null): string {
  if (!isoTimestamp) return '';
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return isoTimestamp;
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

/** Time-of-day only (e.g. "2:00 pm") — for a real slot-time picker, where the
 * date is already shown separately (the date strip above it). */
export function formatTime(isoTimestamp: string | undefined | null): string {
  if (!isoTimestamp) return '';
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return isoTimestamp;
  return new Intl.DateTimeFormat('en-IN', { timeStyle: 'short' }).format(date);
}

/**
 * livetracker3.md §6.1: a single-resource order's quote has exactly one breakup
 * line, so this reduces to that line's title — unchanged behavior for Beauty/
 * Healthcare. Automotive's multi-resource (bay+mechanic) orders carry one breakup
 * line per resource (select_service.py's `_hold_multi_resource_selection`); joining
 * them is what makes that combination render sensibly instead of silently showing
 * only the first resource's name.
 */
/** A short, human-friendly reference for a long backend id (a UUID's first 8
 * hex chars, uppercased) — for customer-facing display only (booking
 * confirmation, invoice), never used as the real id in any API call. Still
 * traceable back to the full id (same prefix) if support ever needs it. */
export function shortId(id: string | undefined | null): string {
  if (!id) return '';
  return id.replace(/-/g, '').slice(0, 8).toUpperCase();
}

export function formatOrderItemName(
  quote: { breakup?: { title: string }[] } | undefined | null,
  fallback: string
): string {
  const titles = quote?.breakup?.map((line) => line.title).filter(Boolean) ?? [];
  return titles.length > 0 ? titles.join(' + ') : fallback;
}
