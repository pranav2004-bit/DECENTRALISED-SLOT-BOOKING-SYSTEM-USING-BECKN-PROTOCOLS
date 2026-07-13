import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone', // leaner Docker image — see Dockerfile
};

export default nextConfig;
