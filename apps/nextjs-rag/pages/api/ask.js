/**
 * RAG ask endpoint.
 *
 * Body parsing: Next.js only auto-parses JSON when the request carries a
 * `Content-Type: application/json` header. When it does not, `req.body` is a
 * raw string and `JSON.parse(req.body)` throws, producing a 500.
 * Handle both shapes explicitly.
 */
import { recordOllamaRequest, recordRequest } from "../../lib/metrics";

export default async function handler(req, res) {
  const startedAt = Date.now();
  const route = "/api/ask";

  // Record the request once the handler finishes, whatever the outcome.
  const finish = (statusCode) => {
    recordRequest(req.method, route, statusCode, (Date.now() - startedAt) / 1000);
  };

  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    finish(405);
    return res.status(405).json({ error: "method not allowed" });
  }

  let body = req.body;
  if (typeof body === "string") {
    try {
      body = body ? JSON.parse(body) : {};
    } catch {
      finish(400);
      return res.status(400).json({ error: "invalid JSON body" });
    }
  }
  body = body || {};

  const q = typeof body.q === "string" ? body.q.trim() : "";
  if (!q) {
    finish(422);
    return res.status(422).json({ error: "field 'q' is required" });
  }

  const url = process.env.OLLAMA_URL || "http://127.0.0.1:11434";
  const model = process.env.OLLAMA_MODEL || "qwen2.5:0.5b";
  const timeoutMs = Number(process.env.OLLAMA_TIMEOUT_MS || 120000);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const ollamaStartedAt = Date.now();

  try {
    const r = await fetch(`${url}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, prompt: q, stream: false }),
      signal: controller.signal,
    });

    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      recordOllamaRequest(model, (Date.now() - ollamaStartedAt) / 1000, false);
      finish(502);
      return res.status(502).json({
        error: `ollama upstream error (${r.status})`,
        detail: detail.slice(0, 500),
      });
    }

    const j = await r.json();
    recordOllamaRequest(model, (Date.now() - ollamaStartedAt) / 1000, true);
    finish(200);
    return res.status(200).json({ answer: j.response ?? "", model });
  } catch (e) {
    const msg = e?.name === "AbortError" ? "ollama request timed out" : e.message;
    recordOllamaRequest(model, (Date.now() - ollamaStartedAt) / 1000, false);
    finish(502);
    return res.status(502).json({ error: msg });
  } finally {
    clearTimeout(timer);
  }
}
