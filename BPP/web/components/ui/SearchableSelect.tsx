'use client';

import { useEffect, useId, useMemo, useRef, useState } from 'react';

/** A labeled dropdown for picking one value out of a long, fixed list (e.g. the
 * Profile page's Pincode field, ~1,200 options) — a plain <select> makes a list
 * that long unusable, so this adds a search box that filters the option list
 * live, on top of manual scrolling. Closes on outside click or Escape. */
export function SearchableSelect({
  label,
  value,
  onChange,
  options,
  placeholder = 'Select…',
  id,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  id?: string;
}) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.toLowerCase().includes(q));
  }, [options, search]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    // Autofocus the search box the moment the panel opens.
    searchInputRef.current?.focus();
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  function selectOption(option: string) {
    onChange(option);
    setOpen(false);
    setSearch('');
  }

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1.5">
      <label htmlFor={fieldId} className="text-sm font-medium text-neutral-900">
        {label}
      </label>
      <button
        id={fieldId}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex w-full items-center justify-between rounded-md border border-neutral-300 bg-white px-3 py-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900 ${
          value ? 'text-neutral-900' : 'text-neutral-400'
        }`}
      >
        {value || placeholder}
        <svg viewBox="0 0 24 24" className="h-4 w-4 flex-shrink-0 text-neutral-500" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-full overflow-hidden rounded-md border border-neutral-200 bg-white shadow-lg">
          <div className="border-b border-neutral-200 p-2">
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search…"
              aria-label={`Search ${label}`}
              className="w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
          <ul role="listbox" className="max-h-56 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-sm text-neutral-500">No matches</li>
            ) : (
              filtered.map((option) => (
                <li key={option}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={option === value}
                    onClick={() => selectOption(option)}
                    className={`w-full cursor-pointer px-3 py-1.5 text-left text-sm hover:bg-neutral-50 ${
                      option === value ? 'bg-[var(--brand-50)] font-medium text-[var(--brand-700)]' : 'text-neutral-900'
                    }`}
                  >
                    {option}
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
