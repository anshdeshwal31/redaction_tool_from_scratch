"use client";
// Review queue (plan §4.11): page image with the item boxes, the reasons, and the reviewer decisions.
// Decisions are appended to the log (reviewer, time, reason); "Re-run" applies them through the pipeline.
import { useCallback, useEffect, useState } from "react";
import { api, errorText, getViewer } from "@/lib/api";

type Box = { x0: number; y0: number; x1: number; y1: number };
type Item = { item_id: string; document_id: string; page: number | null; field: string | null; reason: string; start: number | null; end: number | null; kind: string | null; decision: string | null; resolved_now?: boolean; boxes?: Box[] };
type PageRow = { document_id: string; page: number; page_class: string; state: string; reasons: string[]; rescued_by: string | null };
type State = { matter_id: string; pages: PageRow[]; items: Item[]; export_allowed: Record<string, boolean> };
type PageData = { width_pt: number; height_pt: number; items: Item[] };

const DECISIONS = ["confirm_protect", "not_pii", "add_span", "add_region", "flag_handwriting", "accept_ocr"];
const NOT_PII = ["not_pii", "kept_public_body", "generic_term", "ocr_noise", "false_alarm"];

export default function Review() {
  const [matter, setMatter] = useState("matter_001");
  const [st, setSt] = useState<State | null>(null);
  const [msg, setMsg] = useState("");
  const [sel, setSel] = useState<{ doc: string; page: number } | null>(null);
  const [pageData, setPageData] = useState<PageData | null>(null);
  const [reviewer, setReviewer] = useState("");

  const load = useCallback(() => {
    api<State>(`/review/${matter}`).then(setSt).catch((e) => setMsg(errorText(e)));
  }, [matter]);
  useEffect(() => {
    load();
    setReviewer(getViewer());
  }, [load]);
  useEffect(() => {
    if (!sel) return;
    api<PageData>(`/review/${matter}/page/${sel.doc}/${sel.page}`).then(setPageData).catch((e) => setMsg(errorText(e)));
  }, [sel, matter, st]);

  async function decide(it: Item, decision: string, reason: string | null, payloadText: string) {
    try {
      const payload = decision === "add_span" || decision === "add_region" ? JSON.parse(payloadText || "{}") : undefined;
      await api(`/review/${matter}/decisions`, { method: "POST", json: { item_id: it.item_id, decision, reason, payload }, headers: { "X-Reviewer": reviewer } });
      setMsg(`recorded ${decision}`);
      load();
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  async function rerun() {
    try {
      await api(`/review/${matter}/rerun`, { method: "POST" });
      setMsg("re-run started: the pipeline applies the recorded decisions");
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  return (
    <div>
      <h2>Review queue</h2>
      <div className="toolbar">
        matter <input value={matter} onChange={(e) => setMatter(e.target.value)} size={12} />
        reviewer <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} size={8} />
        <button onClick={load}>Reload</button>
        <button onClick={rerun}>Re-run with decisions</button>
        <span className="muted">{msg}</span>
      </div>
      {st && (
        <>
          <p>
            Export allowed:{" "}
            {Object.entries(st.export_allowed).map(([d, ok]) => (
              <span key={d} className={`badge ${ok ? "verified" : "silver"}`}>
                {d}: {ok ? "yes" : "no"}
              </span>
            ))}
            <span className="muted"> No force option exists: only recorded decisions clear REVIEW and BLOCKED pages.</span>
          </p>
          <div className="workspace">
            <div>
              <table>
                <thead>
                  <tr><th>Doc</th><th className="num">Page</th><th>Class</th><th>State</th><th>Reasons</th><th>Rescued by</th></tr>
                </thead>
                <tbody>
                  {st.pages.filter((p) => p.state !== "PASS").map((p) => (
                    <tr key={`${p.document_id}-${p.page}`} onClick={() => setSel({ doc: p.document_id, page: p.page })} style={{ cursor: "pointer" }}>
                      <td>{p.document_id}</td>
                      <td className="num">{p.page}</td>
                      <td>{p.page_class}</td>
                      <td className={p.state === "BLOCKED" ? "err" : ""}>{p.state}</td>
                      <td>{p.reasons.join(", ")}</td>
                      <td>{p.rescued_by ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sel && pageData && (
                <div className="pageview" style={{ marginTop: 10 }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={`/api/documents/${sel.doc}/pages/${sel.page}/image?dpi=100`} alt="page" />
                  <svg viewBox={`0 0 ${pageData.width_pt} ${pageData.height_pt}`} preserveAspectRatio="none">
                    {pageData.items.flatMap((it) =>
                      (it.boxes ?? []).map((b, i) => (
                        <rect key={`${it.item_id}-${i}`} x={b.x0} y={b.y0} width={b.x1 - b.x0} height={b.y1 - b.y0} fill="rgba(179,38,30,0.15)" stroke="#b3261e" strokeWidth={0.8} />
                      )),
                    )}
                  </svg>
                </div>
              )}
            </div>
            <div className="panel">
              <h3>Items {sel ? `on ${sel.doc} p${sel.page}` : "(pick a page)"}</h3>
              {(pageData?.items ?? []).map((it) => (
                <ItemRow key={it.item_id} it={it} onDecide={decide} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function ItemRow({ it, onDecide }: { it: Item; onDecide: (it: Item, d: string, r: string | null, p: string) => void }) {
  const [d, setD] = useState("confirm_protect");
  const [r, setR] = useState("false_alarm");
  const [p, setP] = useState("");
  return (
    <div className="ent">
      <span className="badge">{it.reason}</span>
      {it.kind && <span className="badge">{it.kind}</span>}
      {it.decision && <span className="badge verified">{it.decision}</span>}
      <div className="row">
        <select value={d} onChange={(e) => setD(e.target.value)}>
          {DECISIONS.map((x) => <option key={x}>{x}</option>)}
        </select>
        {d === "not_pii" && (
          <select value={r} onChange={(e) => setR(e.target.value)}>
            {NOT_PII.map((x) => <option key={x}>{x}</option>)}
          </select>
        )}
        {(d === "add_span" || d === "add_region") && (
          <input placeholder="JSON payload: document_id, page, start, end, entity_type" value={p} onChange={(e) => setP(e.target.value)} size={28} />
        )}
        <button onClick={() => onDecide(it, d, d === "not_pii" ? r : null, p)}>Record</button>
      </div>
    </div>
  );
}
