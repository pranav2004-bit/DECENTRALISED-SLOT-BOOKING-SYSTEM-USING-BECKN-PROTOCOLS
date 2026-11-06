import { BRAND } from '@/lib/brand';

export default function TermsPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6 lg:px-8">
      <h1 className="text-3xl font-bold tracking-tight text-neutral-900">Terms &amp; Conditions</h1>
      <p className="mt-3 text-sm text-neutral-500">Last updated: {new Date().getFullYear()}</p>

      <div className="mt-8 space-y-6 text-sm leading-relaxed text-neutral-600">
        <p>
          This page is a placeholder for {BRAND.name}&apos;s Terms &amp; Conditions. The full
          terms are being finalized and will set out the rules for listing your clinic, accepting
          bookings, and using the platform.
        </p>
        <p>
          Once published, these terms will cover account responsibilities, payment and
          cancellation handling, and what to expect from the open network your listing is
          published on.
        </p>
        <p>
          In the meantime, for any questions about these terms, please reach out through your
          account console.
        </p>
      </div>
    </div>
  );
}
