import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center px-4 py-12 text-center sm:px-6 lg:px-8">
      <h1 className="text-lg font-semibold sm:text-xl">Page not found</h1>
      <p className="mt-2 text-sm text-neutral-600 sm:text-base">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
      >
        Back to home
      </Link>
    </main>
  );
}
