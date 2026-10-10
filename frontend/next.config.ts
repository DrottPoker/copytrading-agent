import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The dashboard serves no images, so the image optimization endpoint stays disabled.
  images: { unoptimized: true },
  // Routes from the previous dashboard layout keep working for bookmarks and links.
  async redirects() {
    return [
      { destination: "/trading", permanent: false, source: "/accounts" },
      { destination: "/trading", permanent: false, source: "/paper-trading" },
      { destination: "/activity", permanent: false, source: "/live-feed" },
      { destination: "/system", permanent: false, source: "/ops" },
      { destination: "/system?tab=database", permanent: false, source: "/database" },
    ];
  },
};

export default nextConfig;
