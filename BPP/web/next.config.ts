import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone', // leaner Docker image — see Dockerfile
  devIndicators: false, // temporarily hidden — re-enable on request
  // Local-dev-only: `npm run dev:carenest`/`dev:stylenest` each copy their own
  // .env.local.<brand> over .env.local before starting, and that file sets
  // NEXT_DIST_DIR — letting two `next dev` instances run against this same
  // source tree at once without fighting over one shared .next build cache
  // (confirmed live: without this, the second instance refuses to start with
  // "Another next dev server is already running"). Never set by Docker's own
  // build args (docker-compose.yml only ever sets NEXT_PUBLIC_*), so a real
  // production build always gets the standard, un-renamed `.next` the
  // Dockerfile's own COPY steps expect — this is additive, not a change to
  // that path.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (!backend) return [];
    // Proxies browser API calls through this app's own origin so the session/CSRF
    // cookie is first-party (browsers drop it as a blocked third-party cookie when
    // the frontend and backend are on different registrable domains). See api-client.ts.
    return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
