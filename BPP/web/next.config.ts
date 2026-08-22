import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone', // leaner Docker image — see Dockerfile
  devIndicators: false, // temporarily hidden — re-enable on request
};

export default nextConfig;
