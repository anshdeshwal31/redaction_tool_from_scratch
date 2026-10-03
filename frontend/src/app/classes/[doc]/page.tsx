"use client";
// Page-class verification (plan §9.2 P1): a person confirms or corrects the classifier's class for every
// page. Verifications are stored apart from the metadata, so re-running the classifier never erases them;
// the evaluation reports classifier accuracy against them (E0). A1 or the adjudicator records them.
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, errorText, getViewer } from "@/lib/api";

type Row = { page: number; classifier: string; verified: string | null; verified_by: string | null };
type Status = { document_id: string; page_count: number; verified_pages: number; pages: Row[] };
const KINDS = ["born_digital", "scanned", "vector_outlined", "hybrid", "blank", "unknown"];

export default function Classes() {
  const { doc } = useParams<{ doc: string }>();
  const [st, setSt] = useState<Status | null>(null);
  const [by, setBy] = useState("");
  const [msg, setMsg] = useState("");
  const [choice, setChoice] = useState<Record<number, string>>({});

  const load = useCallback(() => {
    api<Status>(`/page-classes/${doc}`).then((s) => {
      setSt(s);
      setChoice(Object.fromEntries(s.pages.map((p) => [p.page, p.verified ?? p.classifier])));
    }).catch((e) => setMsg(errorText(e)));
  }, [doc]);
  useEffect(load, [load]);

  async function save(page: number) {
    try {
      await api(`/page-classes/${doc}/${page}`, { method: "PUT", json: { content_kind: choice[page], verified_by: by } });
      setMsg(`page ${page} verified as ${choice[page]}`);
      load();
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  return (
    <div>
      <h2>Page classes · {doc}</h2>
      <div className="toolbar">
        viewer {getViewer()} · initials <input value={by} onChange={(e) => setBy(e.target.value)} size={6} />
        <span className="muted">{st ? `${st.verified_pages}/${st.page_count} pages verified` : ""} {msg}</span>
      </div>
      <p className="muted">Look at each page and confirm the class or pick the right one. "unknown" pages always go to human review.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
        {(st?.pages ?? []).map((p) => (
          <div key={p.page} className="ent" style={{ cursor: "default" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/api/documents/${doc}/pages/${p.page}/image?dpi=50`} alt={`page ${p.page}`} style={{ width: "100%", border: "1px solid #ddd" }} />
            <div className="row">
              <b>p{p.page}</b> classifier: {p.classifier}
              {p.verified && <span className={`badge ${p.verified === p.classifier ? "verified" : "silver"}`}>verified {p.verified} ({p.verified_by})</span>}
            </div>
            <div className="row">
              <select value={choice[p.page] ?? p.classifier} onChange={(e) => setChoice({ ...choice, [p.page]: e.target.value })}>
                {KINDS.map((k) => <option key={k}>{k}</option>)}
              </select>
              <button onClick={() => save(p.page)} disabled={!by}>Verify</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
