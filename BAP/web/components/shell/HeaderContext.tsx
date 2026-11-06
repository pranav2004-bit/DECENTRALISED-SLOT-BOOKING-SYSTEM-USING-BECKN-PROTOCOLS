'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

interface HeaderContentContextValue {
  content: ReactNode;
  setContent: (content: ReactNode) => void;
}

const HeaderContentContext = createContext<HeaderContentContextValue | null>(null);

export function HeaderContentProvider({ children }: { children: ReactNode }) {
  const [content, setContent] = useState<ReactNode>(null);
  return (
    <HeaderContentContext.Provider value={{ content, setContent }}>
      {children}
    </HeaderContentContext.Provider>
  );
}

function useHeaderContentContext(): HeaderContentContextValue {
  const ctx = useContext(HeaderContentContext);
  if (!ctx) {
    throw new Error('useHeaderContentContext must be used within HeaderContentProvider');
  }
  return ctx;
}

/** AppShell's own read side — the current page's custom header content, or
 * null when no page has set one (the default tagline shows instead). */
export function useHeaderContent(): ReactNode {
  return useHeaderContentContext().content;
}

/** A page calls this to replace AppShell's default header content with its
 * own for as long as it's mounted — automatically cleared on unmount (e.g.
 * navigating away), so the default tagline comes back on every other page
 * without each of them having to do anything.
 *
 * `content` MUST be referentially stable across renders where it hasn't
 * actually changed (wrap it in `useMemo` at the call site, keyed on the real
 * values it displays) — a real infinite loop was found live here: with no
 * dependency array, this effect re-ran on every render, called `setContent`
 * with a freshly-created JSX object every time (JSX has no identity of its
 * own), which re-rendered this provider's subtree — including the very page
 * calling this hook — producing another new object and running the effect
 * again, forever ("Maximum update depth exceeded"). Depending on `content`
 * directly is correct specifically *because* the call site is required to
 * memoize it: an unchanged memo keeps the same reference, so `setContent`
 * bails out (React skips the update when given `Object.is`-equal state) and
 * the effect doesn't re-fire until something the memo actually depends on
 * changes. */
export function usePageHeader(content: ReactNode) {
  const { setContent } = useHeaderContentContext();
  useEffect(() => {
    setContent(content);
    return () => setContent(null);
  }, [content, setContent]);
}
