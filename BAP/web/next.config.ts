import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // leaner Docker image — see Dockerfile
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (!backend) return [];
    // Proxies browser API calls through this app's own origin so the session/CSRF
    // cookie is first-party (browsers drop it as a blocked third-party cookie when
    // the frontend and backend are on different registrable domains). See api-client.ts.
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
