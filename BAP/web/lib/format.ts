export function formatPrice(price: { currency: string; value: string } | undefined | null): string {
  if (!price) return '';
  const amount = Number(price.value);
  if (Number.isNaN(amount)) return `${price.currency} ${price.value}`;
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: price.currency }).format(
    amount
  );
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

/**
 * livetracker3.md §6.1: a single-resource order's quote has exactly one breakup
 * line, so this reduces to that line's title — unchanged behavior for Beauty/
 * Healthcare. Automotive's multi-resource (bay+mechanic) orders carry one breakup
 * line per resource (select_service.py's `_hold_multi_resource_selection`); joining
 * them is what makes that combination render sensibly instead of silently showing
 * only the first resource's name.
 */
export function formatOrderItemName(
  quote: { breakup?: { title: string }[] } | undefined | null,
  fallback: string
): string {
  const titles = quote?.breakup?.map((line) => line.title).filter(Boolean) ?? [];
  return titles.length > 0 ? titles.join(' + ') : fallback;
}
