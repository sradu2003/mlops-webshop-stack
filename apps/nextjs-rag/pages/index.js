import { useState } from "react";

export default function Home() {
  const [q, setQ] = useState("");
  const [a, setA] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(e) {
    e.preventDefault();
    setBusy(true); setA("");
    const r = await fetch("/api/ask", { method: "POST", body: JSON.stringify({ q }) });
    const j = await r.json();
    setA(j.answer || j.error || "");
    setBusy(false);
  }

  return (
    <main style={{ maxWidth: 720, margin: "3rem auto", fontFamily: "system-ui" }}>
      <h1>RAG Assistant</h1>
      <form onSubmit={ask}>
        <input value={q} onChange={e => setQ(e.target.value)}
               placeholder="Ask anything..."
               style={{ width: "100%", padding: 12, fontSize: 16 }} />
        <button disabled={busy || !q} style={{ marginTop: 8, padding: "8px 16px" }}>
          {busy ? "Thinking..." : "Ask"}
        </button>
      </form>
      {a && <pre style={{ marginTop: 24, whiteSpace: "pre-wrap" }}>{a}</pre>}
    </main>
  );
}
