'use client';

import { useEffect, useRef, useState } from 'react';

export interface DropdownOption {
  value: string;
  label: string;
}

interface DropdownProps {
  label: string;
  id: string;
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
  placeholder: string;
  disabled?: boolean;
}

/**
 * A native `<select>`'s dropdown panel is rendered by the OS/browser, not this
 * page — it can't be width- or position-constrained by CSS, and on a narrow
 * mobile viewport a long option list (e.g. ALL_INDIA_STATES) renders wider than
 * the screen itself. This is a fully custom listbox instead: the panel is a
 * normal positioned element in this page's own DOM, so it's always clamped to
 * the trigger's width and scrolls internally instead of overflowing.
 */
export function Dropdown({ label, id, value, onChange, options, placeholder, disabled }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);

  return (
    <div className="flex flex-col gap-1.5" ref={containerRef}>
      <label htmlFor={id} className="text-sm font-medium text-neutral-900">
        {label}
      </label>
      <div className="relative">
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-between gap-2 rounded-md border border-neutral-300 bg-white px-3 py-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900 disabled:bg-neutral-100 disabled:text-neutral-400"
        >
          <span className={`truncate ${selected ? 'text-neutral-900' : 'text-neutral-500'}`}>
            {selected ? selected.label : placeholder}
          </span>
          <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none text-neutral-500" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        {open && !disabled && (
          <ul
            role="listbox"
            aria-labelledby={id}
            className="absolute left-0 right-0 top-full z-20 mt-1 max-h-60 overflow-y-auto rounded-md border border-neutral-200 bg-white py-1 text-sm shadow-lg"
          >
            {options.map((option) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className={`cursor-pointer truncate px-3 py-2 hover:bg-neutral-100 ${
                  option.value === value ? 'bg-neutral-50 font-medium text-neutral-900' : 'text-neutral-700'
                }`}
              >
                {option.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
