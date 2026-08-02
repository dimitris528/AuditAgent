/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Base URL of the FastAPI backend (server/main.py). Overridable via env.
  env: {
    API_BASE_URL: process.env.API_BASE_URL || "http://localhost:8000",
    NEXT_PUBLIC_API_BASE:
      process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000",
  },
};

export default nextConfig;
