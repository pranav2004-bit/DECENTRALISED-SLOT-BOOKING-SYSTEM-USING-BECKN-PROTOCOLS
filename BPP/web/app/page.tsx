import Link from 'next/link';
import { CustomerSearchDemo } from '@/components/marketing/CustomerSearchDemo';
import { BRAND } from '@/lib/brand';

const VALUE_PROPS = [
  {
    label: 'Real-time orders',
    description: "Every booking lands on your dashboard the instant it's placed - nothing slips through.",
    icon: (
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor" aria-hidden="true">
        <path d="M13 2 5 13h6l-1 9 8-11h-6z" />
      </svg>
    ),
  },
  {
    label: 'Staff & schedules',
    description: 'Assign staff, manage availability, and keep every slot accurate.',
    icon: (
      <svg
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <circle cx="8.5" cy="8" r="3" />
        <path d="M2.5 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
        <circle cx="17" cy="7.5" r="2.5" />
        <path d="M14.8 20c.2-2.9 2.2-5.2 5-5.6" />
      </svg>
    ),
  },
  {
    label: 'Open by design',
    description: 'List once on an open booking network - any compatible app can reach you, not just one.',
    icon: (
      <svg
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" />
        <ellipse cx="12" cy="12" rx="4" ry="9" />
        <path d="M3 12h18" strokeLinecap="round" />
      </svg>
    ),
  },
];

const ACCURACY_FACTS = [
  {
    label: 'No double-bookings, ever',
    description:
      'Two customers can never be confirmed into the same slot - capacity locks the moment a hold is placed.',
  },
  {
    label: 'Multi-resource bookings',
    description:
      'Need two resources together? It only confirms when everything required is actually free.',
  },
  {
    label: 'Updates the instant it happens',
    description: 'Availability updates over a real-time connection - no manual refresh, ever.',
  },
];

const STEPS = [
  {
    label: 'List your services',
    description: 'Add your services, staff, and availability in minutes.',
  },
  {
    label: 'Get discovered & booked',
    description: 'Customers find and book you through any connected app on the network.',
  },
  {
    label: 'Manage from one console',
    description: 'Track orders, manage schedules, and get notified in real time.',
  },
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col">
      <section className="bg-gradient-to-br from-[var(--brand-50)] to-white px-4 pt-14 pb-16 sm:px-6 lg:px-8">
        <div className="max-w-3xl pl-8 sm:pl-12 lg:pl-20">
          <h1 className="max-w-xl text-balance text-3xl font-semibold tracking-tight text-neutral-900 sm:text-4xl">
            {BRAND.heroHeadline}
          </h1>
          <p className="mt-4 max-w-md text-lg leading-relaxed text-neutral-600">
            {BRAND.heroSubtext.split('six months').map((part, i, arr) =>
              i < arr.length - 1 ? (
                <span key={i}>
                  {part}
                  <span className="font-semibold text-[var(--brand-700)]">six months</span>
                </span>
              ) : (
                part
              )
            )}
          </p>

          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link
              href="/signup"
              className="w-full rounded-full bg-neutral-900 px-7 py-3 text-center text-sm font-semibold text-white shadow-lg shadow-[var(--brand-600)]/30 transition-colors hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 sm:w-auto sm:text-base"
            >
              Create a free account
            </Link>
            <Link
              href="/login"
              className="w-full rounded-full border border-neutral-300 bg-white px-7 py-3 text-center text-sm font-semibold text-neutral-800 transition-colors hover:border-neutral-400 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 sm:w-auto sm:text-base"
            >
              Manage in console
            </Link>
          </div>

          <p className="mt-4 flex items-center gap-1.5 text-sm font-medium text-neutral-600">
            <svg
              viewBox="0 0 24 24"
              className="h-4 w-4 text-[var(--brand-600)]"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M20 6 9 17l-5-5" />
            </svg>
            {BRAND.heroBadge}
          </p>
        </div>
      </section>

      <section
        id="customers"
        className="scroll-mt-20 border-t border-neutral-200 bg-neutral-50 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto grid max-w-5xl gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
              Searching now.
            </h2>
            <p className="mt-3 max-w-md text-lg leading-relaxed text-neutral-600">
              Customers are already looking for services like yours. The moment you list your
              business, they can find you and book - no phone calls, no waiting.
            </p>
          </div>

          <CustomerSearchDemo />
        </div>
      </section>

      <section
        id="features"
        className="scroll-mt-20 border-t border-neutral-200 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto grid max-w-3xl gap-5 sm:grid-cols-3">
          {VALUE_PROPS.map((item) => (
            <div
              key={item.label}
              className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--brand-100)] text-[var(--brand-600)]">
                {item.icon}
              </span>
              <h2 className="mt-3 text-base font-semibold text-neutral-900">{item.label}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">{item.description}</p>
            </div>
          ))}
        </div>
      </section>

      <section
        id="how-it-works"
        className="scroll-mt-20 border-t border-neutral-200 bg-neutral-50 px-4 py-16 sm:px-6 lg:px-8"
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
        id="accuracy"
        className="scroll-mt-20 border-t border-neutral-200 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-3xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
            One accurate calendar. Zero double-bookings.
          </h2>
          <p className="mx-auto mt-3 max-w-lg text-base leading-relaxed text-neutral-600">
            Every hold, booking, and cancellation updates your availability instantly.
          </p>
        </div>

        <div className="mx-auto mt-10 max-w-sm rounded-2xl border border-neutral-200 bg-white p-5 shadow-xl shadow-neutral-200/60">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-xs font-semibold text-neutral-500">
              <span className="h-1.5 w-1.5 rounded-full bg-green-500" aria-hidden="true" />
              Live orders
            </span>
            <span className="text-xs text-neutral-400">Today</span>
          </div>
          <div className="mt-4 divide-y divide-neutral-100">
            <div className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-semibold text-neutral-900">{BRAND.demoServiceName}</p>
                <p className="text-xs text-neutral-500">2:30 PM · Priya S.</p>
              </div>
              <span className="rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
                Confirmed
              </span>
            </div>
            <div className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-semibold text-neutral-900">{BRAND.demoResultNames[0]}</p>
                <p className="text-xs text-neutral-500">4:00 PM · Arjun M.</p>
              </div>
              <span className="rounded-full bg-[var(--brand-50)] px-2.5 py-1 text-xs font-medium text-[var(--brand-700)]">
                Pending
              </span>
            </div>
          </div>
        </div>

        <div className="mx-auto mt-10 grid max-w-3xl gap-8 text-left sm:grid-cols-3">
          {ACCURACY_FACTS.map((fact) => (
            <div key={fact.label}>
              <h3 className="text-sm font-semibold text-[var(--brand-700)]">{fact.label}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">{fact.description}</p>
            </div>
          ))}
        </div>
      </section>

      <section
        id="open-network"
        className="scroll-mt-20 border-t border-neutral-200 px-4 py-16 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
            Built on an open network, not a closed platform.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-neutral-600">
            {`${BRAND.name} runs on the Beckn protocol - an open specification for commerce networks. List your services once, and any compatible buyer app on the network can discover and book you. You're not locked into a single company's app or algorithm.`}
          </p>
        </div>
      </section>

      <section className="border-t border-neutral-200 bg-neutral-900 px-4 py-16 text-center sm:px-6 lg:px-8">
        <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
          Your next customer is already searching.
        </h2>
        <p className="mx-auto mt-3 max-w-md text-base leading-relaxed text-neutral-300">
          List your business and start getting discovered today - free for your first 6 months.
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            href="/signup"
            className="w-full rounded-full bg-white px-7 py-3 text-sm font-semibold text-neutral-900 transition-colors hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-neutral-900 sm:w-auto sm:text-base"
          >
            List your business
          </Link>
          <Link
            href="/login"
            className="w-full rounded-md border border-neutral-700 px-7 py-3 text-sm font-semibold text-white transition-colors hover:border-neutral-500 hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-neutral-900 sm:w-auto sm:text-base"
          >
            Sign into console
          </Link>
        </div>
      </section>
    </div>
  );
}
