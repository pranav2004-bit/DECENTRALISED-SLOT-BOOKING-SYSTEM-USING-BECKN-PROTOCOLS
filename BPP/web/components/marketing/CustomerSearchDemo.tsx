'use client';

import { useEffect, useState } from 'react';
import { BRAND } from '@/lib/brand';

const STEPS = ['search', 'explore', 'select', 'confirmed'] as const;
const STEP_DURATION_MS = 2800;
const SEARCH_TEXT = BRAND.demoSearchQuery;
const TYPE_SPEED_MS = 130;
const RESULTS = BRAND.demoResultNames;
const TIME_SLOTS = ['1:30 PM', '2:00 PM', '2:30 PM', '3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM'];

export function CustomerSearchDemo() {
  const [step, setStep] = useState(0);
  const [typedText, setTypedText] = useState('');

  useEffect(() => {
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
    <div className="aspect-video w-full overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-xl shadow-neutral-200/60">
      <div className="flex h-full flex-col justify-center px-8 py-8">
        {step === 0 && (
          <div className="flex flex-col items-center text-center">
            <svg
              viewBox="0 0 24 24"
              className="h-8 w-8 text-[var(--brand-600)]"
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
            <p className="mt-3 text-xs font-semibold text-neutral-500">Searching on BAP</p>
            <div className="mt-2 flex w-full max-w-xs items-center rounded-full border border-neutral-200 bg-neutral-50 px-4 py-2.5 text-sm text-neutral-700">
              <span>{typedText}</span>
              <span
                className="ml-0.5 h-4 w-0.5 animate-pulse bg-neutral-400"
                aria-hidden="true"
              />
            </div>
          </div>
        )}

        {step === 1 && (
          <div>
            <p className="text-xs font-semibold text-neutral-500">Results near you</p>
            <div className="mt-2 space-y-1.5">
              {RESULTS.map((name) => (
                <div
                  key={name}
                  className="flex items-center justify-between rounded-lg border border-neutral-200 px-4 py-2"
                >
                  <span className="text-sm font-medium text-neutral-900">{name}</span>
                  <span className="rounded-full border border-neutral-300 px-3 py-1 text-xs font-medium text-neutral-700">
                    Book
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <div>
            <p className="text-sm font-semibold text-neutral-900">{RESULTS[0]}</p>
            <p className="text-xs text-neutral-500">{BRAND.demoServiceName} · Today</p>
            <div className="mt-3 grid grid-cols-4 gap-1.5">
              {TIME_SLOTS.map((time) => (
                <span
                  key={time}
                  className={`rounded-lg px-2 py-2 text-center text-xs font-medium ${
                    time === '2:30 PM'
                      ? 'bg-neutral-900 text-white'
                      : 'border border-neutral-200 text-neutral-700'
                  }`}
                >
                  {time}
                </span>
              ))}
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col items-center text-center">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-full bg-green-600 text-white"
              aria-hidden="true"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <p className="mt-3 text-sm font-semibold text-green-900">Booking confirmed</p>
            <p className="text-xs text-neutral-500">{RESULTS[0]} · Today, 2:30 PM</p>
          </div>
        )}
      </div>
    </div>
  );
}
