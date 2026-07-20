export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center gap-3 px-4 py-12 text-center"
    >
      <span
        aria-hidden="true"
        className="h-8 w-8 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-900"
      />
      <p className="text-sm text-neutral-600">{label}</p>
    </div>
  );
}
