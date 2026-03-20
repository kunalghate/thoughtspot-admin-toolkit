/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export — generates ts_admin/static/ at build time.
  // Customers need only Python; Node.js is never required at runtime.
  output: "export",
  trailingSlash: true,

  // In dev mode, proxy /api/* to the FastAPI backend on :8000
  async rewrites() {
    return process.env.NODE_ENV === "development"
      ? [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }]
      : [];
  },
};

module.exports = nextConfig;
