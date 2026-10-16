/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  distDir: "src",
  reactStrictMode: true,
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
