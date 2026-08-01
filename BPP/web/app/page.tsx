import Link from 'next/link';

/** livetracker3.md §7.1: this used to say "Foundation stage... land in a future
 * phase" — stale as of this phase, which is exactly that future phase. */
export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 py-12 sm:px-6 lg:px-8">
      <div className="w-full max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Provider App</h1>
        <p className="mt-2 text-sm text-neutral-600 sm:text-base">
          List your services and manage bookings on this platform.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Link
            href="/signup"
            className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
          >
            Sign up
          </Link>
          <Link
            href="/login"
            className="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700"
          >
            Log in
          </Link>
        </div>
      </div>
    </div>
  );
}
