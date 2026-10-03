"use client";
// Redaction preview (plan §11 U1, §4.12): per page, the image as the PDF export would burn it next to the
// pseudonymised text of the staged JSON, with the page's gate status. Export is only possible once the
// gate is clear; the button calls /export, which answers 409 otherwise (there is no force option).
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorText } from "@/lib/api";

type PageRow = { page: number; content_kind: string; gate: { status: string; reasons: string[] }; text: string; spans: number };
type Preview = { document_id: string; gate: { status: string; leak_scan: string }; output_sha256: string; pages: PageRow[] };

export default function PreviewPage() {
  const { matter, doc } = useParams<{ matter: string; doc: string }>();
  const [pv, setPv] = useState<Preview | null>(null);
  const [p, setP] = useState(1);
  const [msg, setMsg] = useState("");
  const [actor, setActor] = useState("owner");
  useEffect(() => {
    api<Preview>(`/preview/${matter}/${doc}`).then(setPv).catch((e) => setMsg(errorText(e)));
  }, [matter, doc]);
  const page = pv?.pages.find((x) => x.page === p);

  async function doExport() {
    try {
      const r = await api<{ exported: number; refused: number }>(`/export/${matter}`, { method: "POST", json: { formats: ["json", "text", "md", "pdf"] }, headers: { "X-Actor": actor } });
      setMsg(`exported ${r.exported}, refused ${r.refused} (files under exports/${matter}/)`);
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  return (
    <div>
      <h2>Preview · {matter} · {doc}</h2>
      <div className="toolbar">
        <button disabled={p <= 1} onClick={() => setP(p - 1)}>◀ page</button>
        <span>page {p} / {pv?.pages.length ?? "?"}</span>
        <button disabled={!pv || p >= pv.pages.length} onClick={() => setP(p + 1)}>page ▶</button>
        {pv && (
          <span className={pv.gate.status === "PASS" ? "okc" : "err"}>
            document gate {pv.gate.status} · leak scan {pv.gate.leak_scan}
          </span>
        )}
        actor <input value={actor} onChange={(e) => setActor(e.target.value)} size={8} />
        <button className="primary" onClick={doExport}>Export matter</button>
        <span className="muted">{msg}</span>
      </div>
      {page && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <h3>Burned page (as exported to PDF)</h3>
            <div className="pageview">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/api/preview/${matter}/${doc}/${p}/image?dpi=100`} alt="burned page" />
            </div>
          </div>
          <div>
            <h3>Pseudonymised text · {page.content_kind} · gate {page.gate.status} {page.gate.reasons.join(", ")} · {page.spans} spans</h3>
            <pre className="report">{page.text}</pre>
          </div>
        </div>
      )}
      <p className="muted">Output hash {pv?.output_sha256.slice(0, 16)}. Pseudonymisation is not anonymisation: the narrative can still identify the client.</p>
    </div>
  );
}
