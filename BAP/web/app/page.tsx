import Link from 'next/link';

export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 py-12 sm:px-6 lg:px-8">
      <div className="w-full max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Buyer App</h1>
        <p className="mt-2 text-sm text-neutral-600 sm:text-base">
          Find and book beauty &amp; wellness services near you.
        </p>
        <Link
          href="/search"
          className="mt-6 inline-block rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
        >
          Start searching
        </Link>
      </div>
    </div>
  );
}
