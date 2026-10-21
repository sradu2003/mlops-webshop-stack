/**
 * In-process Prometheus registry for the RAG service.
 *
 * Lives in `lib/` (not `pages/api/`) so it can be imported both by the
 * Next.js middleware (Edge runtime) and by the API route handlers
 * (Node runtime) without pulling pages-router internals across runtimes.
 *
 * ---------------------------------------------------------------------------
 * WHY HISTOGRAMS ARE KEYED BY FAMILY *AND* LABELS
 * ---------------------------------------------------------------------------
 * The previous implementation kept ONE histogram map keyed by
 * `<metric_name>|<labels>` and then, when rendering, emitted every entry
 * under the `rag_http_request_duration_seconds` family.
 *
 * That silently corrupted the output: the Ollama upstream observations
 * (labels `model`/`status`) were rendered as
 *
 *   rag_http_request_duration_seconds_bucket{model="qwen2.5:0.5b",status="success"} ...
 *
 * i.e. a histogram whose series carry a COMPLETELY DIFFERENT label set
 * from its siblings. Prometheus treats every series of a family as sharing
 * one label schema, so those series could not be aggregated, and
 * `histogram_quantile(0.95, sum by (le) (rate(..._bucket[5m])))` returned
 * garbage or nothing at all.
 *
 * The fix is one map per metric family. Each family is rendered from its
 * OWN entries only, so every series in a family always carries the same
 * label names.
 */

const buckets = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120];

/**
 * @typedef {{count: number, sum: number, buckets: number[]}} HistogramEntry
 */

/**
 * One map per metric family.
 * @type {Map<string, Map<string, {entry: HistogramEntry, labels: Record<string, string>}>>}
 */
const histograms = new Map();

/** @type {Map<string, {value: number, labels: Record<string, string>}>} */
const counters = new Map();

function observe(name, labels, seconds) {
  let family = histograms.get(name);
  if (!family) {
    family = new Map();
    histograms.set(name, family);
  }
  const key = JSON.stringify(labels);
  let record = family.get(key);
  if (!record) {
    record = {
      entry: { count: 0, sum: 0, buckets: new Array(buckets.length).fill(0) },
      labels,
    };
    family.set(key, record);
  }
  record.entry.count += 1;
  record.entry.sum += seconds;
  for (let i = 0; i < buckets.length; i += 1) {
    if (seconds <= buckets[i]) record.entry.buckets[i] += 1;
  }
}

function increment(name, labels, value = 1) {
  const key = `${name}|${JSON.stringify(labels)}`;
  const existing = counters.get(key);
  if (existing) {
    existing.value += value;
  } else {
    counters.set(key, { value, labels });
  }
}

function labelString(labels) {
  const entries = Object.entries(labels || {}).filter(([, v]) => v !== undefined);
  if (entries.length === 0) return "";
  return `{${entries
    .map(([k, v]) => `${k}="${String(v).replace(/"/g, '\\"')}"`)
    .join(",")}}`;
}

/** Render one histogram family. */
function renderHistogram(name, help) {
  const family = histograms.get(name);
  if (!family || family.size === 0) return [];

  const lines = [`# HELP ${name} ${help}`, `# TYPE ${name} histogram`];
  for (const { entry, labels } of family.values()) {
    for (let i = 0; i < buckets.length; i += 1) {
      lines.push(
        `${name}_bucket${labelString({ ...labels, le: String(buckets[i]) })} ${entry.buckets[i]}`
      );
    }
    lines.push(
      `${name}_bucket${labelString({ ...labels, le: "+Inf" })} ${entry.count}`
    );
    lines.push(`${name}_sum${labelString(labels)} ${entry.sum}`);
    lines.push(`${name}_count${labelString(labels)} ${entry.count}`);
  }
  return lines;
}

export function recordRequest(method, route, statusCode, seconds) {
  const labels = { method, route, status_code: String(statusCode) };
  observe("rag_http_request_duration_seconds", labels, seconds);
  increment("rag_http_requests_total", labels);
}

export function recordOllamaRequest(model, seconds, ok) {
  const labels = { model, status: ok ? "success" : "error" };
  observe("rag_ollama_request_duration_seconds", labels, seconds);
  increment("rag_ollama_requests_total", labels);
}

export function renderMetrics() {
  const lines = [];

  lines.push(
    ...renderHistogram(
      "rag_http_request_duration_seconds",
      "Duration of HTTP requests in seconds"
    )
  );
  lines.push(
    ...renderHistogram(
      "rag_ollama_request_duration_seconds",
      "Ollama upstream latency in seconds"
    )
  );

  // Counters are grouped by family so each family emits exactly one
  // `# TYPE` line, regardless of how many label combinations it has.
  const counterFamilies = new Map();
  for (const [key, { value, labels }] of counters.entries()) {
    const name = key.slice(0, key.indexOf("|"));
    if (!counterFamilies.has(name)) {
      counterFamilies.set(name, { series: [] });
    }
    counterFamilies.get(name).series.push({ value, labels });
  }
  for (const [name, family] of counterFamilies.entries()) {
    lines.push(`# TYPE ${name} counter`);
    for (const { value, labels } of family.series) {
      lines.push(`${name}${labelString(labels)} ${value}`);
    }
  }

  return `${lines.join("\n")}\n`;
}
