'use client';

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Server-side errors are already logged by the backend per OBSERVABILITY.md;
    // this covers client-side render errors specifically.
    console.error('Client-side render error:', error);
  }, [error]);

  return (
    <main className="flex flex-1 flex-col items-center justify-center px-4 py-12 text-center sm:px-6 lg:px-8">
      <h1 className="text-lg font-semibold sm:text-xl">Something went wrong</h1>
      <p className="mt-2 text-sm text-neutral-600 sm:text-base">
        An unexpected error occurred. Please try again.
      </p>
      <button
        onClick={() => reset()}
        className="mt-6 inline-block rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
      >
        Try again
      </button>
    </main>
  );
}
