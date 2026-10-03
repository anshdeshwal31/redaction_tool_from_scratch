"use client";
// Adjudication view (plan §11 A1: simple tables first): IAA metrics, the disagreement worklist with links
// to the pages, and promotion of a submitted, verified annotator file into the gold annotations.
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorText, getViewer } from "@/lib/api";

type Prf = { tp: number; fp: number; fn: number; precision: number | null; recall: number | null; f1: number | null };
type IaaPublic = {
  pages_compared: number;
  mentions_a: number;
  mentions_b: number;
  span: Record<string, { a_as_gold: Prf; b_as_gold: Prf; protect_types: Prf }>;
  kappa: { protect_keep_none: number | null; protect_binary: number | null; type_on_matched: number | null; tokens: number };
  canonical_b3: { f1: number | null };
  role_agreement: number | null;
  action_agreement: number | null;
  region_iou_mean: number | null;
  disagreements: Record<string, number>;
  targets: Record<string, number>;
};
type Work = { category: string; a: string | null; b: string | null; page: number | null };

export default function Adjudicate() {
  const { doc } = useParams<{ doc: string }>();
  const [data, setData] = useState<{ public: IaaPublic; worklist: Work[] } | null>(null);
  const [msg, setMsg] = useState("");
  const [a, setA] = useState("A1");
  const [b, setB] = useState("A2");
  const viewer = typeof window === "undefined" ? "" : getViewer();

  useEffect(() => {
    api<{ public: IaaPublic; worklist: Work[] }>(`/iaa/${doc}?a=${a}&b=${b}`).then(setData).catch((e) => setMsg(errorText(e)));
  }, [doc, a, b]);

  async function promote(source: string) {
    const by = window.prompt(`Verified by (name or initials) — promotes the submitted ${source} file of ${doc} into gold`);
    if (!by) return;
    try {
      const r = await api<{ revision: number }>(`/promote/${doc}`, { method: "POST", json: { source, verified_by: by } });
      setMsg(`promoted: gold revision ${r.revision}`);
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  if (viewer && viewer !== "adjudicator") return <p className="err">Switch to the adjudicator role to adjudicate.</p>;
  const p = data?.public;
  const f = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : x.toFixed(4));
  return (
    <div>
      <h2>Adjudicate {doc}</h2>
      <div className="row">
        compare
        <select value={a} onChange={(e) => setA(e.target.value)}>{["A1", "A2", "claude_silver", "adjudicated"].map((x) => <option key={x}>{x}</option>)}</select>
        with
        <select value={b} onChange={(e) => setB(e.target.value)}>{["A2", "A1", "claude_silver", "adjudicated"].map((x) => <option key={x}>{x}</option>)}</select>
        <button onClick={() => promote("A1")}>Promote A1 → gold</button>
        <button onClick={() => promote("A2")}>Promote A2 → gold</button>
        <span className="muted">{msg}</span>
      </div>
      {p && (
        <>
          <h3>Agreement ({p.pages_compared} pages; {p.mentions_a} vs {p.mentions_b} mentions)</h3>
          <table>
            <thead><tr><th>Scheme</th><th className="num">F1 ({a} as gold)</th><th className="num">F1 protect types</th><th className="num">TP</th><th className="num">FP</th><th className="num">FN</th></tr></thead>
            <tbody>
              {Object.entries(p.span).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{f(v.a_as_gold.f1)}</td><td className="num">{f(v.protect_types.f1)}</td><td className="num">{v.a_as_gold.tp}</td><td className="num">{v.a_as_gold.fp}</td><td className="num">{v.a_as_gold.fn}</td></tr>
              ))}
            </tbody>
          </table>
          <p>
            κ protect/keep/none {f(p.kappa.protect_keep_none)} · κ protect {f(p.kappa.protect_binary)} (target {p.targets.kappa_protect}) · κ type {f(p.kappa.type_on_matched)} ·
            B³ {f(p.canonical_b3.f1)} · role {f(p.role_agreement)} · action {f(p.action_agreement)} · region IoU {f(p.region_iou_mean)}
          </p>
          <h3>Worklist ({data!.worklist.length})</h3>
          <table>
            <thead><tr><th>Category</th><th>Page</th><th>{a}</th><th>{b}</th><th></th></tr></thead>
            <tbody>
              {data!.worklist.map((w, i) => (
                <tr key={i}>
                  <td>{w.category}</td><td>{w.page}</td><td>{w.a ?? "—"}</td><td>{w.b ?? "—"}</td>
                  <td>{w.page && <a href={`/annotate/${doc}/${w.page}?as=adjudicated`}>open</a>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
