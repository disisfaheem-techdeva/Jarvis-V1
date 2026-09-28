import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Old and guessed URLs that used to 404. /auth/login is the API's POST
  // endpoint, not a page; the client dashboard lives on the API host.
  async redirects() {
    return [
      { source: "/auth/login", destination: "/login", permanent: false },
      { source: "/dashboard", destination: "https://api.techdeva.net/dashboard", permanent: false },
    ];
  },
};

export default nextConfig;
