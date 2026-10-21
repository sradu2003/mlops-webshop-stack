import express from "express";
import cors from "cors";
import morgan from "morgan";
import pg from "pg";
import promClient from "@prometheus-io/client";

const { Pool } = pg;
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

// --- Metrics ---------------------------------------------------------------
// The Prometheus job `node_api` scrapes /metrics on this port.
const registry = new promClient.Registry();
promClient.collectDefaultMetrics({ register: registry, prefix: "node_api_" });

const httpRequestDuration = new promClient.Histogram({
  name: "node_api_http_request_duration_seconds",
  help: "Duration of HTTP requests in seconds",
  labelNames: ["method", "route", "status_code"],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
  registers: [registry],
});

const httpRequestsTotal = new promClient.Counter({
  name: "node_api_http_requests_total",
  help: "Total number of HTTP requests",
  labelNames: ["method", "route", "status_code"],
  registers: [registry],
});

const dbQueryDuration = new promClient.Histogram({
  name: "node_api_db_query_duration_seconds",
  help: "Duration of database queries in seconds",
  labelNames: ["operation"],
  buckets: [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5],
  registers: [registry],
});

const app = express();
app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(morgan("tiny"));

// --- Instrument every request ----------------------------------------------
app.use((req, res, next) => {
  const started = process.hrtime.bigint();
  res.on("finish", () => {
    // Use the matched route pattern (not the raw path) to keep cardinality
    // bounded — /orders/123 and /orders/456 must not create two series.
    const route = req.route?.path
      ? `${req.baseUrl || ""}${req.route.path}`
      : req.path;
    const labels = {
      method: req.method,
      route,
      status_code: String(res.statusCode),
    };
    const duration = Number(process.hrtime.bigint() - started) / 1e9;
    httpRequestDuration.observe(labels, duration);
    httpRequestsTotal.inc(labels);
  });
  next();
});

// --- Routes ----------------------------------------------------------------

app.get("/health", (_req, res) => res.json({ ok: true }));

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", registry.contentType);
  res.end(await registry.metrics());
});

app.get("/orders", async (_req, res) => {
  const stop = dbQueryDuration.startTimer({ operation: "orders_probe" });
  try {
    const { rows } = await pool.query("SELECT 1 as ok");
    stop();
    res.json({ orders: [], db: rows[0] });
  } catch (e) {
    stop();
    res.status(500).json({ error: e.message });
  }
});

const port = process.env.PORT || 8000;
const host = process.env.HOST || "127.0.0.1";

const server = app.listen(port, host, () =>
  console.log(`node-api on ${host}:${port}`)
);

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    console.log(`${signal} received, closing server`);
    server.close(() => process.exit(0));
  });
}
