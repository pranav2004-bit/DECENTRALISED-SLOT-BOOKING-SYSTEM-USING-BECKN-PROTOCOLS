/** Capitalizes the first letter of every word, leaving the rest of each word
 * untouched (so an acronym like "XYZ Clinic" stays "XYZ Clinic", not "Xyz Clinic"). */
export function capitalizeWords(value: string): string {
  if (!value) return value;
  return value.replace(/\b\w/g, (c) => c.toUpperCase());
}

/** A short, human-friendly reference for a long backend id (a UUID's first 8
 * hex chars, uppercased) — for display only, never used as the real id in any
 * API call. Still traceable back to the full id (same prefix) if support ever
 * needs it. Same helper as BAP/web/lib/format.ts's own `shortId`. */
export function shortId(id: string | undefined | null): string {
  if (!id) return '';
  return id.replace(/-/g, '').slice(0, 8).toUpperCase();
}

/** Indian commerce convention (Razorpay/PhonePe/Zomato etc.): a whole-rupee amount
 * shows as "₹500", not "₹500.00" — decimals only appear when there are real paise.
 * Same convention as BAP/web/lib/format.ts's own `formatPrice`, generalized to the
 * plain `(value, currency)` pair this app's own APIs return instead of a `Price`
 * object shape. */
export function formatAmount(value: string | number, currency = 'INR'): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return `${currency} ${value}`;
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount);
}
