'use client';

import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

type ToastType = 'success' | 'error';

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

const TOAST_DURATION_MS = 3000;

const ToastContext = createContext<{
  showToast: (message: string, type?: ToastType) => void;
} | null>(null);

/** Confirmation popups for account actions (signup/login/logout) — mounted once at
 * the root layout so any page can fire one via useToast() without prop drilling. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const showToast = useCallback((message: string, type: ToastType = 'success') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, TOAST_DURATION_MS);
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed right-4 top-20 z-[100] flex w-full max-w-sm flex-col gap-2 px-4 sm:px-0"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className="pointer-events-auto flex items-center gap-2.5 rounded-md border border-neutral-200 bg-white px-4 py-3 text-sm font-medium text-neutral-900 shadow-lg"
          >
            <span
              aria-hidden="true"
              className={`h-2 w-2 flex-shrink-0 rounded-full ${
                t.type === 'success' ? 'bg-green-500' : 'bg-red-500'
              }`}
            />
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within a ToastProvider');
  return ctx;
}
