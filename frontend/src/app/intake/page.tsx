"use client";
// Document upload (plan §11 U1). The PDF goes to the local API only; its filename is not sent or stored
// (filenames can hold PII). It is registered under the `intake` split (never scored), classified and
// extracted in a background job; then "Run pipeline" runs detection, replacement and the release gate,
// and the preview shows what an export would contain.
import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "@/lib/api";

type Row = { matter_id: string; document_id: string; state: string; pages?: number; extracted_pages?: number; job: string | null };

export default function Intake() {
  const [matter, setMatter] = useState("matter_001");
  const [rows, setRows] = useState<Row[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Row[]>("/intake").then(setRows).catch((e) => setMsg(errorText(e)));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const body = await file.arrayBuffer();               // bytes only: the name stays in the browser
      const r = await api<Row & { new: boolean }>(`/intake/${matter}`, { method: "POST", body, headers: { "Content-Type": "application/pdf" } });
      setMsg(`${r.document_id}: ${r.new ? "registered" : "already registered"}, ${r.state}`);
      load();
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function runPipeline() {
    try {
      await api(`/review/${matter}/rerun`, { method: "POST" });
      setMsg("pipeline started for the matter (detection, replacement, release gate); see the review queue");
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  return (
    <div>
      <h2>Document intake</h2>
      <div className="toolbar">
        matter <input value={matter} onChange={(e) => setMatter(e.target.value)} size={12} />
        <input type="file" accept="application/pdf" disabled={busy} onChange={(e) => upload(e.target.files?.[0])} />
        <button onClick={runPipeline}>Run pipeline for matter</button>
        <a href="/review"><button>Review queue</button></a>
        <span className="muted">{msg}</span>
      </div>
      <p className="muted">Uploads stay on this machine. Intake documents are never scored; export follows the release gate.</p>
      <table>
        <thead><tr><th>Document</th><th>Matter</th><th>State</th><th className="num">Pages</th><th>Job</th><th>Preview</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.document_id}>
              <td>{r.document_id}</td>
              <td>{r.matter_id}</td>
              <td>{r.state}</td>
              <td className="num">{r.extracted_pages ?? 0}/{r.pages ?? "?"}</td>
              <td>{r.job ?? ""}</td>
              <td><a href={`/preview/${r.matter_id}/${r.document_id}`}>preview</a></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
