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
