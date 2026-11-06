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
  /** The one real Beckn/ONDC domain code this brand's own backend actually serves
   * (matches BUSINESS_DOMAINS in lib/constants.ts — kept as an explicit code here,
   * not derived from domainLabel by string-matching, since 'Beauty' vs 'Beauty &
   * Wellness' don't match verbatim). Used to lock signup's category picker to the
   * only domain this instance's backend can actually accept. */
  domainCode: string;
  heroHeadline: string;
  heroSubtext: string;
  heroBadge: string;
  demoServiceName: string;
  demoSearchQuery: string;
  demoResultNames: string[];
  /** "How ___ find you" widget on signup/login/home (components/marketing/CustomerSearchDemo.tsx) */
  demoWidgetHeading: string;
  demoWidgetBusinessName: string;
  demoWidgetProfiles: { name: string; slots: string[] }[];
  demoWidgetSlotStatus: Record<string, Record<string, 'taken' | 'selected'>>;
  colors: BrandColors;
}

const BRANDS: Record<BrandId, Brand> = {
  beauty: {
    id: 'beauty',
    name: 'StyleNest',
    monogram: 'S',
    tagline: 'Keep every chair glowing.',
    domainLabel: 'Beauty',
    domainCode: 'ONDC:RET13',
    heroHeadline: 'Never let a chair sit empty.',
    heroSubtext:
      "List your salon on an open booking network and get discovered by real customers - no cost to start, you're covered for six months.",
    heroBadge: 'Built for beauty & salon businesses',
    demoServiceName: 'Kavya Sanjana - Hair Stylist',
    demoSearchQuery: 'Haircut near me',
    demoResultNames: ['Queens Choice', 'Glow Beauty Studio', 'The Clip Bar', 'Urban Cuts Studio'],
    demoWidgetHeading: 'How customers find you',
    demoWidgetBusinessName: 'Queens Choice',
    demoWidgetProfiles: [
      {
        name: 'Kavya Sanjana - Hair Stylist',
        slots: ['1:30 PM', '2:00 PM', '2:30 PM', '3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM'],
      },
      {
        name: 'Laxmisree - Makeup Artist',
        slots: ['10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '1:00 PM', '1:30 PM'],
      },
      {
        name: 'Meghana - Nail Technician',
        slots: ['3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM'],
      },
      {
        name: 'Niharika - Skincare Specialist',
        slots: ['5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM', '7:00 PM', '7:30 PM', '8:00 PM', '8:30 PM'],
      },
    ],
    demoWidgetSlotStatus: {
      'Kavya Sanjana - Hair Stylist': { '1:30 PM': 'taken', '2:00 PM': 'taken', '2:30 PM': 'selected' },
      'Laxmisree - Makeup Artist': {
        '10:00 AM': 'taken',
        '10:30 AM': 'taken',
        '11:00 AM': 'taken',
        '11:30 AM': 'taken',
      },
      'Meghana - Nail Technician': {
        '3:00 PM': 'taken',
        '3:30 PM': 'taken',
        '4:00 PM': 'taken',
        '4:30 PM': 'taken',
        '5:00 PM': 'taken',
      },
      'Niharika - Skincare Specialist': { '5:00 PM': 'taken', '5:30 PM': 'taken', '6:00 PM': 'taken' },
    },
    colors: { 50: '#fffbeb', 100: '#fef3c7', 600: '#d97706', 700: '#b45309' },
  },
  medical: {
    id: 'medical',
    name: 'CareNest',
    monogram: 'C',
    tagline: 'Run your practice, without the chaos.',
    domainLabel: 'Healthcare',
    domainCode: 'ONDC:SRV13',
    heroHeadline: 'Never miss a patient booking again.',
    heroSubtext:
      "List your clinic so patients can find and book you right away, at no cost for your first six months.",
    heroBadge: 'Built for clinics & practitioners',
    demoServiceName: 'Dr. Kavya - Gynaecologist',
    demoSearchQuery: 'Doctor near me',
    demoResultNames: [
      'Rainbow Hospitals',
      'CareNest Wellness Center',
      'Dr. Mehta’s Practice',
      'Sunrise Health Clinic',
      'Medicare Multispeciality',
      'Apollo Clinic',
    ],
    demoWidgetHeading: 'How patients find you',
    demoWidgetBusinessName: 'Rainbow Hospitals',
    demoWidgetProfiles: [
      {
        name: 'Dr. Kavya - Gynaecologist',
        slots: ['1:30 PM', '2:00 PM', '2:30 PM', '3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM'],
      },
      {
        name: 'Dr. Laxmisree - Cardiology',
        slots: ['10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '1:00 PM', '1:30 PM'],
      },
      {
        name: 'Dr. Meghana - Neurology',
        slots: ['3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM'],
      },
      {
        name: 'Dr. Niharika - Pediatrics',
        slots: ['5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM', '7:00 PM', '7:30 PM', '8:00 PM', '8:30 PM'],
      },
    ],
    demoWidgetSlotStatus: {
      'Dr. Kavya - Gynaecologist': { '1:30 PM': 'taken', '2:00 PM': 'taken', '2:30 PM': 'selected' },
      'Dr. Laxmisree - Cardiology': {
        '10:00 AM': 'taken',
        '10:30 AM': 'taken',
        '11:00 AM': 'taken',
        '11:30 AM': 'taken',
      },
      'Dr. Meghana - Neurology': {
        '3:00 PM': 'taken',
        '3:30 PM': 'taken',
        '4:00 PM': 'taken',
        '4:30 PM': 'taken',
        '5:00 PM': 'taken',
      },
      'Dr. Niharika - Pediatrics': { '5:00 PM': 'taken', '5:30 PM': 'taken', '6:00 PM': 'taken' },
    },
    colors: { 50: '#f0fdfa', 100: '#ccfbf1', 600: '#0d9488', 700: '#0f766e' },
  },
  automotive: {
    id: 'automotive',
    name: 'AutoCare',
    monogram: 'A',
    tagline: 'Keep every bay booked.',
    domainLabel: 'Automotive',
    domainCode: 'BECKN:AUTO01',
    heroHeadline: 'Never let a bay sit idle.',
    heroSubtext:
      "List your garage on an open booking network and get discovered by real customers - no cost to start, you're covered for six months.",
    heroBadge: 'Built for garages & service centers',
    demoServiceName: 'Brake Inspection',
    demoSearchQuery: 'Brake inspection near me',
    demoResultNames: ['Downtown Auto Garage', 'AutoCare Service Center', 'Reliable Motors', 'QuickFix Garage'],
    demoWidgetHeading: 'How customers find you',
    demoWidgetBusinessName: 'Downtown Auto Garage',
    demoWidgetProfiles: [
      {
        name: 'Ravi Teja - Brake Specialist',
        slots: ['1:30 PM', '2:00 PM', '2:30 PM', '3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM'],
      },
      {
        name: 'Suresh - Engine Diagnostics',
        slots: ['10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM', '12:00 PM', '12:30 PM', '1:00 PM', '1:30 PM'],
      },
      {
        name: 'Kiran - Tyre & Alignment',
        slots: ['3:00 PM', '3:30 PM', '4:00 PM', '4:30 PM', '5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM'],
      },
      {
        name: 'Arjun - AC & Electrical',
        slots: ['5:00 PM', '5:30 PM', '6:00 PM', '6:30 PM', '7:00 PM', '7:30 PM', '8:00 PM', '8:30 PM'],
      },
    ],
    demoWidgetSlotStatus: {
      'Ravi Teja - Brake Specialist': { '1:30 PM': 'taken', '2:00 PM': 'taken', '2:30 PM': 'selected' },
      'Suresh - Engine Diagnostics': {
        '10:00 AM': 'taken',
        '10:30 AM': 'taken',
        '11:00 AM': 'taken',
        '11:30 AM': 'taken',
      },
      'Kiran - Tyre & Alignment': {
        '3:00 PM': 'taken',
        '3:30 PM': 'taken',
        '4:00 PM': 'taken',
        '4:30 PM': 'taken',
        '5:00 PM': 'taken',
      },
      'Arjun - AC & Electrical': { '5:00 PM': 'taken', '5:30 PM': 'taken', '6:00 PM': 'taken' },
    },
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
