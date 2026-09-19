/** @type {import('next').NextConfig} */
const backend = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";

const nextConfig = {
  experimental: {
    instrumentationHook: true,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/health", destination: `${backend}/health` },
      { source: "/v1/:path*", destination: `${backend}/v1/:path*` },
    ];
  },
};

export default nextConfig;
