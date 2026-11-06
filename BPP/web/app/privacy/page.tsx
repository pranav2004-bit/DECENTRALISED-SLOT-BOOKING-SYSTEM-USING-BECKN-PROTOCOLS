import { BRAND } from '@/lib/brand';

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6 lg:px-8">
      <h1 className="text-3xl font-bold tracking-tight text-neutral-900">Privacy Policy</h1>
      <p className="mt-3 text-sm text-neutral-500">Last updated: {new Date().getFullYear()}</p>

      <div className="mt-8 space-y-6 text-sm leading-relaxed text-neutral-600">
        <p>
          This page is a placeholder for {BRAND.name}&apos;s Privacy Policy. The full policy is
          being finalized and will explain what information we collect, how it is used, and the
          choices you have around it.
        </p>
        <p>
          Once published, this policy will cover account and booking information, how it is
          shared with the clinics and patients you interact with through the network, and how to
          contact us with privacy questions or requests.
        </p>
        <p>
          In the meantime, for any privacy-related questions, please reach out through your
          account console.
        </p>
      </div>
    </div>
  );
}
