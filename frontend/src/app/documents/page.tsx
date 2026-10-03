"use client";
import { useEffect, useState } from "react";
import { api, errorText, getViewer } from "@/lib/api";

type Doc = {
  document_id: string;
  split: string;
  page_count: number | null;
  page_classes: Record<string, number>;
  iaa: boolean;
  candidates_allowed: boolean;
  document_type: string | null;
};

export default function Documents() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [err, setErr] = useState("");
  const [viewer, setV] = useState("A1");
  useEffect(() => {
    setV(getViewer());
    api<Doc[]>("/documents").then(setDocs).catch((e) => setErr(errorText(e)));
  }, []);
  const target = viewer === "adjudicator" ? "adjudicated" : viewer;
  return (
    <div>
      <h2>Documents</h2>
      {err && <p className="err">{err}</p>}
      <table>
        <thead>
          <tr>
            <th>Document</th>
            <th>Split</th>
            <th>Type</th>
            <th className="num">Pages</th>
            <th>Page classes</th>
            <th>Mode for {viewer}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.document_id}>
              <td>{d.document_id}</td>
              <td>{d.split}</td>
              <td className="muted">{d.document_type}</td>
              <td className="num">{d.page_count}</td>
              <td>
                {Object.entries(d.page_classes).map(([k, v]) => (
                  <span className="badge" key={k}>
                    {k} {v}
                  </span>
                ))}
              </td>
              <td>{d.iaa ? "blind (IAA)" : d.candidates_allowed ? "candidates shown" : "blind (test split)"}</td>
              <td>
                <a href={`/annotate/${d.document_id}/1?as=${target}`}>annotate</a>
                {(viewer === "A1" || viewer === "adjudicator") && (
                  <>
                    {" · "}
                    <a href={`/classes/${d.document_id}`}>page classes</a>
                  </>
                )}
                {viewer === "A1" && (
                  <>
                    {" · "}
                    <a href={`/annotate/${d.document_id}/1?as=A1_retest`} title="test-retest: re-annotate 5 pages a week later, blind (§9.3)">retest</a>
                  </>
                )}
                {viewer === "adjudicator" && (
                  <>
                    {" · "}
                    <a href={`/adjudicate/${d.document_id}`}>adjudicate</a>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Blind rules: on the IAA documents A1 and A2 see neither the silver pass, candidates, nor each other&apos;s work. The test split never
        gets detector candidates. A1 verifies the silver pass on the other documents.
      </p>
    </div>
  );
}
