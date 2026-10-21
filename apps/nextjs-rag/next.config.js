/** @type {import('next').NextConfig} */

// NOTE: `output: "standalone"` is intentionally NOT used.
// The systemd unit runs `npm run start` -> `next start`, and Next.js prints:
//   '"next start" does not work with "output: standalone" configuration.'
// which leaves the service running an unsupported path. The default output
// plus `next start` is the supported combination for a self-hosted VPS.
// (If you later move to a container image, switch to standalone and change
//  ExecStart to `node .next/standalone/server.js`.)
module.exports = {
  reactStrictMode: true,
  poweredByHeader: false,
  compress: true,
  eslint: { ignoreDuringBuilds: false },
};
