import { renderMetrics } from "../../lib/metrics";

/**
 * Prometheus scrape endpoint for the RAG service.
 *
 * NOTE: request timing is recorded inside the API route handlers rather
 * than in a Next.js middleware. The Edge runtime that `middleware.js` runs
 * in does not expose `req.res`, so a middleware-based implementation
 * cannot observe the response status code and crashes with
 * "Cannot read properties of undefined (reading 'end')".
 */
export default function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).end();
  }
  res.setHeader("Content-Type", "text/plain; version=0.0.4; charset=utf-8");
  res.status(200).send(renderMetrics());
}
