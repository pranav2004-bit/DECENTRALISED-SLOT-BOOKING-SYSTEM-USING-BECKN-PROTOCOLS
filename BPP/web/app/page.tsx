import Link from 'next/link';
import { BRAND } from '@/lib/brand';
import { capitalizeWords } from '@/lib/format';

const VALUE_PROPS = [
  {
    label: 'Real-time orders',
    description: "Every booking lands on your dashboard the instant it's placed - nothing slips through.",
  },
  {
    label: 'Staff & schedules',
    description: 'Assign staff, manage their availability and keep every slot accurate across your whole team.',
  },
  {
    label: 'Open by design',
    description: 'List once on an open booking network - any compatible app can reach you, not just one.',
  },
  {
    label: 'Built-in payments',
    description: 'Collect and track payments directly through the platform, with no separate tools needed.',
  },
  {
    label: 'Live notifications',
    description: 'Get notified the instant a booking, cancellation or payment happens, wherever you are.',
  },
];

const ACCURACY_DATES = [
  { day: 'SAT', date: '1' },
  { day: 'SUN', date: '2' },
  { day: 'MON', date: '3', active: true },
  { day: 'TUE', date: '4' },
  { day: 'WED', date: '5' },
  { day: 'THU', date: '6' },
  { day: 'FRI', date: '7' },
];

const ACCURACY_SLOTS = [
  { time: '1:30 PM', status: 'taken' },
  { time: '2:00 PM', status: 'taken' },
  { time: '2:30 PM', status: 'selected' },
  { time: '3:00 PM', status: 'available' },
  { time: '3:30 PM', status: 'available' },
  { time: '4:00 PM', status: 'available' },
  { time: '4:30 PM', status: 'available' },
  { time: '5:00 PM', status: 'available' },
];

const STEPS = [
  {
    label: 'List your services',
    description: 'Add your services, staff and hours, then start taking real bookings in just a few short minutes today.',
  },
  {
    label: 'Get discovered & booked',
    description: `${capitalizeWords(BRAND.customerNoun)}s searching nearby can find your ${BRAND.businessNoun} right away and book you through any connected app.`,
  },
  {
    label: 'Manage from one console',
    description:
      'Track every booking, manage staff schedules and get notified the moment anything changes at all.',
  },
  {
    label: 'Get paid automatically',
    description:
      'Payments are collected and settled through the platform, so you are not chasing anyone for money.',
  },
  {
    label: 'Build your reputation',
    description: `${capitalizeWords(BRAND.customerNoun)}s rate their visit afterwards, helping more people discover and trust your ${BRAND.businessNoun} over time.`,
  },
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col">
      {/* StyleNest-only: its own `--brand-50` (amber `#fffbeb`) reads as near-
          white, too close to the plain white header above and the white
          section below it for real separation — confirmed live. Scoped to
          `BRAND.id === 'beauty'` deliberately (not changed for every brand):
          CareNest's/AutoCare's own `--brand-50` tints are untouched per the
          user's own standing instruction to report, not silently apply,
          anything that would affect the other two brand instances. */}
      <section
        className={
          BRAND.id === 'beauty'
            ? 'bg-gradient-to-br from-[var(--brand-100)] to-white px-4 py-16 sm:px-6 lg:px-8'
            : 'bg-gradient-to-br from-[var(--brand-50)] to-white px-4 py-16 sm:px-6 lg:px-8'
        }
      >
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
              href="/signup"
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
        id="accuracy"
        className="scroll-mt-20 px-4 py-12 sm:px-6 lg:px-8"
      >
        <div className="mx-auto grid max-w-5xl gap-12 lg:grid-cols-2 lg:items-center">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
              One calendar. Updated in real time.
            </h2>
            <p className="mt-3 max-w-md text-base leading-relaxed text-neutral-600">
              Every hold, booking and cancellation updates your availability instantly, keeping your calendar accurate at all times, so your staff never have to double check or guess.
            </p>
          </div>

          <div className="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
              <div className="-mx-4 -mt-4 flex divide-x divide-neutral-200 rounded-t-2xl border-b border-neutral-200 bg-white">
                {ACCURACY_DATES.map((d) => (
                  <div
                    key={d.date}
                    className={
                      d.active
                        ? 'flex flex-1 flex-col items-center bg-[var(--brand-100)] py-1.5 text-[var(--brand-700)]'
                        : 'flex flex-1 flex-col items-center py-1.5 text-neutral-600'
                    }
                  >
                    <span className="text-xs font-medium tracking-wide">{d.day}</span>
                    <span className={`text-sm font-bold ${d.active ? '' : 'text-neutral-900'}`}>
                      {d.date}
                    </span>
                  </div>
                ))}
              </div>

              <div className="mt-2.5 flex items-center justify-between border-t border-neutral-100 pt-2.5">
                <div>
                  <p className="text-sm font-semibold text-neutral-900">{BRAND.demoResultNames[0]}</p>
                  <p className="text-xs font-medium text-neutral-900">{BRAND.demoServiceName}</p>
                </div>
                <span className="flex items-center gap-1.5 text-xs text-neutral-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-green-500" aria-hidden="true" />
                  Live
                </span>
              </div>

              <div className="mt-2.5 grid grid-cols-4 gap-1.5">
                {ACCURACY_SLOTS.map((slot) => (
                  <div
                    key={slot.time}
                    className={
                      slot.status === 'taken'
                        ? 'rounded-lg bg-neutral-400 py-1.5 text-center text-xs font-semibold text-white'
                        : slot.status === 'selected'
                          ? 'rounded-lg bg-[var(--brand-100)] py-1.5 text-center text-xs font-semibold text-[var(--brand-700)]'
                          : 'rounded-lg border border-[var(--brand-600)]/30 bg-white py-1.5 text-center text-xs font-medium text-neutral-600'
                    }
                  >
                    {slot.time}
                  </div>
                ))}
              </div>
              <p className="mt-2.5 flex items-center gap-3 text-xs text-neutral-500">
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm bg-neutral-400" aria-hidden="true" />
                  Already booked
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-2 w-2 rounded-sm bg-[var(--brand-100)]"
                    aria-hidden="true"
                  />
                  Selected
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm border border-[var(--brand-600)]/30 bg-white" aria-hidden="true" />
                  Available
                </span>
              </p>
            </div>
        </div>
      </section>

      <section
        id="features"
        className="scroll-mt-20 px-4 py-10 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-7xl">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">Features</h2>
          <div className="mt-8 grid gap-8 lg:grid-cols-[1.3fr_2fr] lg:items-start">
            <div className="rounded-2xl border border-neutral-200 bg-neutral-50 p-6 shadow-sm">
              <h3 className="text-lg font-semibold text-neutral-900">{VALUE_PROPS[0].label}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                {VALUE_PROPS[0].description}
              </p>
              <div className="mt-4 flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-3.5 py-2.5">
                <div>
                  <p className="text-sm font-semibold text-neutral-900">New booking</p>
                  <p className="text-xs text-neutral-500">2:30 PM · just now</p>
                </div>
                <span className="rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
                  Confirmed
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-2">
              {VALUE_PROPS.slice(1).map((item) => (
                <div key={item.label} className="min-w-0">
                  <h3 className="text-base font-semibold text-neutral-900">{item.label}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-neutral-600">{item.description}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* "Searching now" section hidden from the home page per request (2026-10-18) —
          will be reused elsewhere, not deleted. Re-enable by uncommenting this block,
          and re-add "#customers" to AppShell.tsx's landing nav if it returns here.
      <section
        id="customers"
        className="scroll-mt-20 bg-neutral-50 px-4 py-10 sm:px-6 lg:px-8"
      >
        <div className="mx-auto grid max-w-5xl gap-10 lg:grid-cols-2 lg:items-center">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
              Searching now.
            </h2>
            <p className="mt-3 max-w-md text-base leading-relaxed text-neutral-600">
              Customers are already looking for services like yours. The moment you list your
              business, they can find you and book - no phone calls, no waiting.
            </p>
          </div>

          <CustomerSearchDemo />
        </div>
      </section>
      */}

      <section
        id="how-it-works"
        className="scroll-mt-20 px-4 py-10 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-7xl">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
            How it works
          </h2>
          <div className="mt-10 grid gap-x-6 gap-y-10 sm:grid-cols-2 lg:grid-cols-5">
            {STEPS.map((step, i) => (
              <div key={step.label}>
                <span className="font-[family-name:var(--font-title)] text-3xl font-extrabold text-[var(--brand-600)]">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <h3 className="mt-2 text-base font-semibold text-neutral-900">{step.label}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                  {step.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        id="open-network"
        className="scroll-mt-20 bg-neutral-50 px-4 py-8 sm:px-6 lg:px-8"
      >
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-bold tracking-tight text-neutral-900">
            Built on an open network.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-neutral-600">
            {`${BRAND.name} runs on an open specification for commerce networks. List your services once and any compatible buyer app on the network can discover and book you. One listing, visible everywhere on the network.`}
          </p>
        </div>
      </section>

      <footer className="border-t border-neutral-800 bg-neutral-900 px-4 py-12 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="grid grid-cols-2 gap-10 sm:grid-cols-2 lg:grid-cols-4">
            <div className="col-span-2 sm:col-span-1">
              <span className="font-[family-name:var(--font-title)] text-xl font-extrabold text-white">
                {BRAND.name}
              </span>
              <p className="mt-3 max-w-xs text-sm leading-relaxed text-neutral-400">{BRAND.tagline}</p>
              <p className="mt-3 text-xs text-neutral-500">A {BRAND.name} Industries Ltd company</p>
              <p className="mt-1 text-xs text-neutral-500">
                Vishalakshi Nagar, Visakhapatnam, Andhra Pradesh 531162
              </p>
            </div>

            <div>
              <h3 className="text-sm font-semibold text-white">Product</h3>
              <ul className="mt-3 space-y-2 text-sm text-neutral-400">
                <li>
                  <a href="#accuracy" className="transition-colors hover:text-white">
                    Accuracy
                  </a>
                </li>
                <li>
                  <a href="#features" className="transition-colors hover:text-white">
                    Features
                  </a>
                </li>
                <li>
                  <a href="#how-it-works" className="transition-colors hover:text-white">
                    How it works
                  </a>
                </li>
                <li>
                  <a href="#open-network" className="transition-colors hover:text-white">
                    Open Network
                  </a>
                </li>
              </ul>
            </div>

            {/* Mobile-only: Company + Legal grouped into one grid cell (stacked
                vertically) so they sit together on the right, beside Product on the
                left. `sm:contents` makes this wrapper disappear from the grid at sm+,
                so Company/Legal go back to being independent columns there — tablet
                and desktop layout is unchanged by this. */}
            <div className="flex flex-col gap-8 sm:contents">
              <div>
                <h3 className="text-sm font-semibold text-white">Company</h3>
                <ul className="mt-3 space-y-2 text-sm text-neutral-400">
                  <li>
                    <Link href="/signup" className="transition-colors hover:text-white">
                      Sign into console
                    </Link>
                  </li>
                  <li>
                    <Link href="/signup" className="transition-colors hover:text-white">
                      List your business
                    </Link>
                  </li>
                </ul>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-white">Legal</h3>
                <ul className="mt-3 space-y-2 text-sm text-neutral-400">
                  <li>
                    <Link href="/privacy" className="transition-colors hover:text-white">
                      Privacy Policy
                    </Link>
                  </li>
                  <li>
                    <Link href="/terms" className="transition-colors hover:text-white">
                      Terms &amp; Conditions
                    </Link>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <div className="mt-10 border-t border-neutral-800 pt-6">
            <p className="text-xs text-neutral-500">
              © {new Date().getFullYear()} {BRAND.name}. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
