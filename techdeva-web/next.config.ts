import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Old and guessed URLs that used to 404. /auth/login is the API's POST
  // endpoint, not a page; the client dashboard lives on the API host.
  async redirects() {
    return [
      { source: "/auth/login", destination: "/login", permanent: false },
      { source: "/dashboard", destination: "https://api.techdeva.net/dashboard", permanent: false },
      // The old /portal/* screens duplicated the client dashboard and went
      // stale; every client now works in the one dashboard on the API host.
      { source: "/portal/onboarding", destination: "https://api.techdeva.net/onboarding", permanent: false },
      { source: "/portal/:path+", destination: "https://api.techdeva.net/dashboard", permanent: false },
      { source: "/signup", destination: "https://api.techdeva.net/", permanent: false },
      // The old /founder pages never listed clients; the founder console
      // (clients, requests, plans, money) lives on the API host.
      { source: "/founder", destination: "https://api.techdeva.net/techdeva", permanent: false },
      { source: "/founder/:path*", destination: "https://api.techdeva.net/techdeva", permanent: false },
    ];
  },
};

export default nextConfig;
