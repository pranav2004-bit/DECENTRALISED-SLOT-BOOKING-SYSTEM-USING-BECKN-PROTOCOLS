/**
 * livetracker7.md Phase 4 — per-instance brand config. Same BAP/web codebase for both
 * BAP instances (OnSlot/GoFetch), differentiated only by NEXT_PUBLIC_BRAND_ID (a build
 * arg, baked in per Docker image — see docker-compose.yml). No forked code.
 *
 * Colors are raw hex values, not Tailwind color-name classes — see BPP/web/lib/brand.ts's
 * identical comment for why (Tailwind v4 JIT can't detect a dynamically-constructed
 * class name). Wired into CSS custom properties in app/layout.tsx, read via the
 * literal `bg-[var(--brand-600)]` arbitrary-value syntax throughout.
 */

export type BrandId = 'x' | 'y';

interface BrandColors {
  50: string;
  100: string;
  600: string;
  700: string;
}

export interface Brand {
  id: BrandId;
  name: string;
  monogram: string;
  tagline: string;
  heroHeadline: string;
  heroSubtext: string;
}

const COLORS: BrandColors = { 50: '#fff1f2', 100: '#ffe4e6', 600: '#e11d48', 700: '#be123c' };
const GOFETCH_COLORS: BrandColors = { 50: '#f0fdf4', 100: '#dcfce7', 600: '#16a34a', 700: '#15803d' };

const BRANDS: Record<BrandId, Brand & { colors: BrandColors }> = {
  x: {
    id: 'x',
    name: 'OnSlot',
    monogram: 'O',
    tagline: 'Search less, book more.',
    heroHeadline: 'Search less, book more.',
    heroSubtext:
      'Find real availability at beauty salons, clinics, and garages near you - and book it in seconds, no phone calls.',
    colors: COLORS,
  },
  y: {
    id: 'y',
    name: 'GoFetch',
    monogram: 'G',
    tagline: 'Find it, book it, go.',
    heroHeadline: 'Find it, book it, go.',
    heroSubtext:
      'Discover real availability at beauty salons, clinics, and garages near you - and book it in seconds, no phone calls.',
    colors: GOFETCH_COLORS,
  },
};

function resolveBrandId(): BrandId {
  const raw = process.env.NEXT_PUBLIC_BRAND_ID;
  if (raw === 'x' || raw === 'y') return raw;
  return 'x';
}

export const BRAND_ID: BrandId = resolveBrandId();
export const BRAND = BRANDS[BRAND_ID];
