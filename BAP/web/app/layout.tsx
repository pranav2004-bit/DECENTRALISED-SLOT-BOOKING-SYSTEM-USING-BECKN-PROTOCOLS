import type { CSSProperties } from 'react';
import type { Metadata, Viewport } from 'next';
import './globals.css';
import { AppShell } from '@/components/shell/AppShell';
import { PWARegister } from '@/components/shell/PWARegister';
import { BRAND, BRAND_ID } from '@/lib/brand';

// livetracker7.md Phase 4.2: real, designer-delivered per-brand icon set. No app/
// favicon.ico static-convention file anymore (can't vary per brand) — the
// `shortcut` entry below is the only favicon source now.
const ICON_DIR = `/icons/${BRAND_ID}`;

export const metadata: Metadata = {
  title: `${BRAND.name} — Buyer App`,
  description: 'Decentralized slot booking — Buyer App Platform',
  icons: {
    icon: [
      { url: `${ICON_DIR}/icon-192.png`, sizes: '192x192', type: 'image/png' },
      { url: `${ICON_DIR}/icon-512.png`, sizes: '512x512', type: 'image/png' },
    ],
    shortcut: `${ICON_DIR}/favicon.ico`,
    apple: `${ICON_DIR}/icon-192.png`,
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'default',
    title: BRAND.name,
  },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: BRAND.colors[600],
};

const brandStyle = {
  '--brand-50': BRAND.colors[50],
  '--brand-100': BRAND.colors[100],
  '--brand-600': BRAND.colors[600],
  '--brand-700': BRAND.colors[700],
} as CSSProperties;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased" style={brandStyle}>
      <body className="min-h-full flex flex-col bg-white text-neutral-900">
        <PWARegister />
        <AppShell appName={BRAND.name}>{children}</AppShell>
      </body>
    </html>
  );
}
