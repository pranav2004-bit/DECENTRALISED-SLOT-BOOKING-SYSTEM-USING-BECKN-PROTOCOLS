export function ErrorState({
  title = 'Something went wrong',
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <h2 className="text-base font-semibold text-neutral-900 sm:text-lg">{title}</h2>
      {description && <p className="text-sm text-neutral-600 sm:text-base">{description}</p>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
        >
          Try again
        </button>
      )}
    </div>
  );
}
