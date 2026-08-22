/**
 * livetracker7.md Phase 4 — per-instance brand config. Same BPP/web codebase for all
 * 3 BPP instances (StyleNest/CareNest/AutoCare), differentiated only by
 * NEXT_PUBLIC_BRAND_ID (a build arg, baked in per Docker image — see docker-compose.yml)
 * selecting one of the entries below. No forked code, no per-instance component trees.
 *
 * Colors are raw hex values (not Tailwind color-name classes like `bg-amber-600`)
 * because Tailwind v4's JIT scanner can't detect a dynamically-constructed class name
 * (`bg-${color}-600`) — the values here are wired into CSS custom properties in
 * `app/layout.tsx`, and JSX uses the literal, scanner-visible arbitrary-value syntax
 * `bg-[var(--brand-600)]` throughout. The hex values themselves are Tailwind's own
 * default palette values for amber/teal/blue (confirmed against Tailwind's published
 * palette, not guessed), so StyleNest's rendered color is pixel-identical to before
 * this refactor — purely additive, not a visual change for the existing brand.
 */

export type BrandId = 'beauty' | 'medical' | 'automotive';

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
  /** livetracker7.md Phase 2/3 real finding: all 3 BPP instances are single-domain
   * now (BPP-Beauty's own SUPPORTED_DOMAINS was narrowed to Beauty-only in Phase 2,
   * not just Medical/Automotive as Phase 4.3's own drafted text assumed) — so every
   * brand has a real domainLabel, and the landing page's "Industries" section /
   * "3+ service categories" claim is removed for all 3, not made conditional. */
  domainLabel: string;
  heroHeadline: string;
  heroSubtext: string;
  heroBadge: string;
  demoServiceName: string;
  demoSearchQuery: string;
  demoResultNames: string[];
  colors: BrandColors;
}

const BRANDS: Record<BrandId, Brand> = {
  beauty: {
    id: 'beauty',
    name: 'StyleNest',
    monogram: 'S',
    tagline: 'Keep every chair glowing.',
    domainLabel: 'Beauty',
    heroHeadline: 'Never let a chair sit empty.',
    heroSubtext:
      "List your salon on an open booking network and get discovered by real customers - no cost to start, you're covered for six months.",
    heroBadge: 'Built for beauty & salon businesses',
    demoServiceName: 'Haircut & Styling',
    demoSearchQuery: 'Haircut near me',
    demoResultNames: ['Style & Grace Salon', 'Glow Beauty Studio', 'The Clip Bar', 'Urban Cuts Studio'],
    colors: { 50: '#fffbeb', 100: '#fef3c7', 600: '#d97706', 700: '#b45309' },
  },
  medical: {
    id: 'medical',
    name: 'CareNest',
    monogram: 'C',
    tagline: 'Run your practice, without the chaos.',
    domainLabel: 'Healthcare',
    heroHeadline: 'Never miss a patient booking again.',
    heroSubtext:
      "List your practice on an open booking network and get discovered by real patients - no cost to start, you're covered for six months.",
    heroBadge: 'Built for clinics & practitioners',
    demoServiceName: 'General Consultation',
    demoSearchQuery: 'Doctor near me',
    demoResultNames: ['Downtown Family Clinic', 'CareNest Wellness Center', 'Dr. Mehta’s Practice', 'Sunrise Health Clinic'],
    colors: { 50: '#f0fdfa', 100: '#ccfbf1', 600: '#0d9488', 700: '#0f766e' },
  },
  automotive: {
    id: 'automotive',
    name: 'AutoCare',
    monogram: 'A',
    tagline: 'Keep every bay booked.',
    domainLabel: 'Automotive',
    heroHeadline: 'Never let a bay sit idle.',
    heroSubtext:
      "List your garage on an open booking network and get discovered by real customers - no cost to start, you're covered for six months.",
    heroBadge: 'Built for garages & service centers',
    demoServiceName: 'Brake Inspection',
    demoSearchQuery: 'Brake inspection near me',
    demoResultNames: ['Downtown Auto Garage', 'AutoCare Service Center', 'Reliable Motors', 'QuickFix Garage'],
    colors: { 50: '#eff6ff', 100: '#dbeafe', 600: '#2563eb', 700: '#1d4ed8' },
  },
};

function resolveBrandId(): BrandId {
  const raw = process.env.NEXT_PUBLIC_BRAND_ID;
  if (raw === 'medical' || raw === 'automotive' || raw === 'beauty') return raw;
  return 'beauty';
}

export const BRAND_ID: BrandId = resolveBrandId();
export const BRAND: Brand = BRANDS[BRAND_ID];
