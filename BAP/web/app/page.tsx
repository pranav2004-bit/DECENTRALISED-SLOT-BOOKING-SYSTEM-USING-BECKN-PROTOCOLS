import Link from 'next/link';
import { BRAND } from '@/lib/brand';

const DOMAINS = [
  {
    label: 'Beauty',
    description: 'Salons and stylists — haircuts, styling, and personal care appointments.',
  },
  {
    label: 'Healthcare',
    description: 'Clinics and practitioners — in-person and tele-consultation bookings.',
  },
  {
    label: 'Automotive',
    description: 'Service centers and garages — inspections, repairs, and maintenance slots.',
  },
];

const STEPS = [
  {
    label: 'Search',
    description: 'Tell us what you need and where — we check real availability, not guesses.',
  },
  {
    label: 'Select',
    description: 'Pick the exact time that works for you from what’s actually open.',
  },
  {
    label: 'Confirm',
    description: 'Book it. Your slot is held the moment you select it, confirmed the moment you book.',
  },
];

const TRUST_FACTS = [
  {
    label: 'Real availability, not a guess',
    description: 'Every result reflects what a business has actually made bookable right now.',
  },
  {
    label: 'No double-bookings',
    description: 'Once you hold a slot, it’s yours — no one else can book over it while you decide.',
  },
  {
    label: 'Manage it all in one place',
    description: 'Every booking you make — upcoming or past — is tracked and manageable from your account.',
  },
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col">
      <section className="bg-gradient-to-br from-[var(--brand-50)] to-white px-4 pt-14 pb-16 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <h1 className="text-balance text-3xl font-semibold tracking-tight text-neutral-900 sm:text-4xl">
            {BRAND.heroHeadline}
          </h1>
          <p className="mx-auto mt-4 max-w-lg text-lg leading-relaxed text-neutral-600">
            {BRAND.heroSubtext}
          </p>

          <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              href="/search"
              className="w-full rounded-full bg-neutral-900 px-7 py-3 text-center text-sm font-semibold text-white shadow-lg shadow-[var(--brand-600)]/30 transition-colors hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 sm:w-auto sm:text-base"
            >
              Start searching
            </Link>
            <Link
              href="/bookings"
              className="w-full rounded-full border border-neutral-300 bg-white px-7 py-3 text-center text-sm font-semibold text-neutral-800 transition-colors hover:border-neutral-400 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 sm:w-auto sm:text-base"
            >
              View my bookings
            </Link>
          </div>
        </div>
      </section>

      <section
        id="domains"
        className="scroll-mt-20 border-t border-neutral-200 bg-neutral-50 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center text-2xl font-bold tracking-tight text-neutral-900">
            What you can book
          </h2>
          <div className="mt-10 grid gap-5 sm:grid-cols-3">
            {DOMAINS.map((domain) => (
              <div
                key={domain.label}
                className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm"
              >
                <h3 className="text-base font-semibold text-neutral-900">{domain.label}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                  {domain.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        id="how-it-works"
        className="scroll-mt-20 border-t border-neutral-200 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center text-2xl font-bold tracking-tight text-neutral-900">
            How it works
          </h2>
          <div className="mt-10 grid gap-8 sm:grid-cols-3">
            {STEPS.map((step, i) => (
              <div key={step.label}>
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-neutral-900 text-sm font-bold text-white">
                  {i + 1}
                </span>
                <h3 className="mt-3 text-base font-semibold text-neutral-900">{step.label}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                  {step.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        id="trust"
        className="scroll-mt-20 border-t border-neutral-200 bg-neutral-50 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl">
          <h2 className="text-center text-2xl font-bold tracking-tight text-neutral-900">
            Book with confidence
          </h2>
          <div className="mx-auto mt-10 grid max-w-3xl gap-8 text-left sm:grid-cols-3">
            {TRUST_FACTS.map((fact) => (
              <div key={fact.label}>
                <h3 className="text-sm font-semibold text-[var(--brand-700)]">{fact.label}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                  {fact.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        id="open-network"
        className="scroll-mt-20 border-t border-neutral-200 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
            Every business on an open network, not just one company&apos;s picks.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-neutral-600">
            {`${BRAND.name} runs on the Beckn protocol - an open specification for commerce networks. We search across every business connected to the network, not a closed list curated by one company.`}
          </p>
        </div>
      </section>

      <section className="border-t border-neutral-200 bg-neutral-900 px-4 py-16 text-center sm:px-6 lg:px-8">
        <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
          Your next booking is one search away.
        </h2>
        <p className="mx-auto mt-3 max-w-md text-base leading-relaxed text-neutral-300">
          Search real availability near you and book in seconds.
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            href="/search"
            className="w-full rounded-full bg-white px-7 py-3 text-sm font-semibold text-neutral-900 transition-colors hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-neutral-900 sm:w-auto sm:text-base"
          >
            Start searching
          </Link>
        </div>
      </section>
    </div>
  );
}
