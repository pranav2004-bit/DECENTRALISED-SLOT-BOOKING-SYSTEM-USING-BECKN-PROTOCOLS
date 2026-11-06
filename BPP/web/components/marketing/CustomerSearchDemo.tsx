'use client';

import { useEffect, useState } from 'react';
import { BRAND } from '@/lib/brand';

const STEPS = ['search', 'explore', 'select', 'confirmed'] as const;
const STEP_DURATION_MS = 2300;
const SEARCH_TEXT = BRAND.demoSearchQuery;
const TYPE_SPEED_MS = 100;
const RESULTS = BRAND.demoResultNames;
// Per-brand business name + staff/profile list + slot statuses, so CareNest
// (doctors), StyleNest (stylists) and AutoCare (mechanics) each show their
// own relevant content here instead of one hardcoded hospital/doctor set.
const DEMO_CLINIC = BRAND.demoWidgetBusinessName;
const PROFILES = BRAND.demoWidgetProfiles.map((p) => ({ doctor: p.name, slots: p.slots }));
const DEMO_DATES = [
  { day: 'SAT', date: '1' },
  { day: 'SUN', date: '2' },
  { day: 'MON', date: '3', active: true },
  { day: 'TUE', date: '4' },
  { day: 'WED', date: '5' },
  { day: 'THU', date: '6' },
  { day: 'FRI', date: '7' },
];
// Per-profile booked/selected states (matching the Accuracy section's card on
// the home page) — first profile has 2 booked + 1 selected, second 4 booked,
// third 5 booked, fourth 3 booked, each taking their own earliest slots.
const SLOT_STATUS_BY_DOCTOR = BRAND.demoWidgetSlotStatus;

// Resumed (2026-10-19) — customization is done, auto-play is back on.
const PAUSED = false;

export function CustomerSearchDemo() {
  const [step, setStep] = useState(PAUSED ? 1 : 0);
  const [typedText, setTypedText] = useState('');

  useEffect(() => {
    if (PAUSED) return;
    const id = setInterval(() => {
      setStep((s) => (s + 1) % STEPS.length);
    }, STEP_DURATION_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (step !== 0) return;
    let i = 0;
    const typeId = setInterval(() => {
      i += 1;
      setTypedText(SEARCH_TEXT.slice(0, i));
      if (i >= SEARCH_TEXT.length) clearInterval(typeId);
    }, TYPE_SPEED_MS);
    const resetId = setTimeout(() => setTypedText(''), 0);
    return () => {
      clearInterval(typeId);
      clearTimeout(resetId);
    };
  }, [step]);

  return (
    <div className="aspect-square w-full overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-xl shadow-neutral-200/60">
      <div className="flex h-full flex-col justify-center px-10 py-10">
        {step === 0 && (
          <div className="flex flex-col items-center text-center">
            <svg
              viewBox="0 0 24 24"
              className="h-14 w-14 text-[var(--brand-600)]"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <p className="mt-4 text-sm font-semibold text-neutral-500">Searching on BAP</p>
            <div className="mt-3 flex w-full max-w-sm items-center rounded-full border border-neutral-200 bg-neutral-50 px-5 py-3.5 text-base text-neutral-700">
              <span>{typedText}</span>
              <span
                className="ml-0.5 h-5 w-0.5 animate-pulse bg-neutral-400"
                aria-hidden="true"
              />
            </div>
          </div>
        )}

        {step === 1 && (
          <div>
            <p className="text-sm font-semibold text-neutral-500">Results near you</p>
            <div className="mt-3 space-y-2.5">
              {RESULTS.map((name) => (
                <div
                  key={name}
                  className="flex items-center justify-between rounded-lg border border-neutral-200 px-5 py-3.5"
                >
                  <span className="text-base font-medium text-neutral-900">{name}</span>
                  <span className="rounded-full border border-neutral-300 px-3.5 py-1.5 text-sm font-medium text-neutral-700">
                    Book
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="flex h-full flex-col">
            <div className="-mx-10 -mt-10 flex divide-x divide-neutral-200 rounded-t-2xl border-b border-neutral-200 bg-white">
              {DEMO_DATES.map((d) => (
                <div
                  key={d.date}
                  className={
                    d.active
                      ? 'flex flex-1 flex-col items-center bg-[var(--brand-100)] py-1 text-[var(--brand-700)]'
                      : 'flex flex-1 flex-col items-center py-1 text-neutral-600'
                  }
                >
                  <span className="text-xs font-medium tracking-wide">{d.day}</span>
                  <span className="text-xs font-bold">{d.date}</span>
                </div>
              ))}
            </div>

            <p className="mt-4 text-sm font-bold text-neutral-900">{DEMO_CLINIC}</p>
            <div className="mt-3 space-y-4">
              {PROFILES.map((profile) => (
                <div key={profile.doctor}>
                  <p className="text-xs text-neutral-900">{profile.doctor}</p>
                  <div className="mt-2 grid grid-cols-4 gap-1.5">
                    {profile.slots.map((time) => {
                      const status = SLOT_STATUS_BY_DOCTOR[profile.doctor]?.[time];
                      return (
                        <span
                          key={time}
                          className={
                            status === 'taken'
                              ? 'rounded-lg bg-neutral-400 px-1.5 py-1.5 text-center text-xs font-semibold text-white'
                              : status === 'selected'
                                ? 'rounded-lg bg-[var(--brand-100)] px-1.5 py-1.5 text-center text-xs font-semibold text-[var(--brand-700)]'
                                : 'rounded-lg border border-neutral-200 px-1.5 py-1.5 text-center text-xs font-medium text-neutral-700'
                          }
                        >
                          {time}
                        </span>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            <p className="-mb-8 mt-auto flex items-center gap-3 text-xs text-neutral-500">
              <span className="flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-sm bg-neutral-400" aria-hidden="true" />
                Already booked
              </span>
              <span className="flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-sm bg-[var(--brand-100)]" aria-hidden="true" />
                Selected
              </span>
              <span className="flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-sm border border-neutral-300 bg-white" aria-hidden="true" />
                Available
              </span>
            </p>
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col items-center text-center">
            <span
              className="flex h-16 w-16 items-center justify-center rounded-full bg-green-600 text-white"
              aria-hidden="true"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-8 w-8"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <p className="mt-4 text-base font-semibold text-green-900">Booking confirmed</p>
            <p className="text-sm text-neutral-500">
              {DEMO_CLINIC} · Today, 2:30 PM
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
