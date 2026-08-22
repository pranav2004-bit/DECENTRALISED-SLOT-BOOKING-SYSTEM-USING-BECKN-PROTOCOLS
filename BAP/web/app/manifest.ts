import type { MetadataRoute } from 'next';
import { BRAND, BRAND_ID } from '@/lib/brand';

// livetracker7.md Phase 4.2: each brand ships its own real icon set (designer-
// delivered) under public/icons/<BRAND_ID>/ — same codebase, no forked code.
const ICON_DIR = `/icons/${BRAND_ID}`;

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: BRAND.name,
    short_name: BRAND.name,
    description: 'Decentralized slot booking — search, book, and manage appointments.',
    start_url: '/',
    display: 'standalone',
    background_color: '#ffffff',
    theme_color: BRAND.colors[600],
    icons: [
      {
        src: `${ICON_DIR}/icon-48.png`,
        sizes: '48x48',
        type: 'image/png',
        purpose: 'any',
      },
      {
        src: `${ICON_DIR}/icon-48.png`,
        sizes: '48x48',
        type: 'image/png',
        purpose: 'maskable',
      },
      {
        src: `${ICON_DIR}/icon-192.png`,
        sizes: '192x192',
        type: 'image/png',
        purpose: 'any',
      },
      {
        src: `${ICON_DIR}/icon-192.png`,
        sizes: '192x192',
        type: 'image/png',
        purpose: 'maskable',
      },
      {
        src: `${ICON_DIR}/icon-512.png`,
        sizes: '512x512',
        type: 'image/png',
        purpose: 'any',
      },
      {
        src: `${ICON_DIR}/icon-512.png`,
        sizes: '512x512',
        type: 'image/png',
        purpose: 'maskable',
      },
    ],
  };
}
