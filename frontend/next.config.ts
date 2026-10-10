import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The dashboard serves no images, so the image optimization endpoint stays disabled.
  images: { unoptimized: true },
};

export default nextConfig;
