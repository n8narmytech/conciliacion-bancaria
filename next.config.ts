import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // En desarrollo el frontend corre en :3000 y la API Python en :8899.
  // En Vercel ambos se sirven del mismo dominio, así que no hace falta proxy.
  async rewrites() {
    if (process.env.NODE_ENV === "development") {
      return [{ source: "/api/:path*", destination: "http://127.0.0.1:8899/api/:path*" }];
    }
    return [];
  },
};

export default nextConfig;
