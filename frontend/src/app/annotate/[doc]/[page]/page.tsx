"use client";
// Annotation workspace (plan §11 A1): page image with word boxes; select words or draw a region; set
// type, role, canonical ID, attributes, flags, certainty, override and notes; save with validation and a
// revision check; candidates (dev, non-IAA only) and the silver pass (A1 verification) as starting points.
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AnnotationSet, CanonicalRegistry, Entity, Region, RegistryEntity } from "@/lib/api-types";
import { api, errorText, getViewer } from "@/lib/api";
import { colourOf, contiguous, nextEntityId, overlapsAnchor, regionsFor, round2, wordsInBox, type PageWords, type Word } from "@/lib/annotate";

type Taxonomy = {
  entity_types: string[];
  region_only: string[];
  person_roles: string[];
  org_roles: string[];
  date_roles: string[];
  location_granularity: string[];
  flags: string[];
  certainty: string[];
};
type DocMeta = { document_id: string; split: string; page_count: number; iaa: boolean; candidates_allowed: boolean; pages: { page: number; width_pt: number; height_pt: number; content_kind: string }[] };
type AnnResp = { annotation: AnnotationSet; stored_revision: number; status: { submitted?: boolean }; readonly: boolean };
type Span = { start: number; end: number; text: string; entity_type: string; page: number | null; bboxes: Region[]; rule_id: string | null };

const ANNOTATOR_FOR: Record<string, string> = { A1: "A1", A2: "A2", adjudicator: "adjudicated" };

export default function Workspace() {
  const params = useParams<{ doc: string; page: string }>();
  const doc = params.doc;
  const pageNo = parseInt(params.page, 10);
  const [viewer, setViewerState] = useState("A1");
  const [target, setTarget] = useState("A1");
  const [tax, setTax] = useState<Taxonomy | null>(null);
  const [meta, setMeta] = useState<DocMeta | null>(null);
  const [pw, setPw] = useState<PageWords | null>(null);
  const [ann, setAnn] = useState<AnnotationSet | null>(null);
  const [stored, setStored] = useState(0);
  const [readonly, setReadonly] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [registry, setRegistry] = useState<{ registry: CanonicalRegistry; sha: string } | null>(null);
  const [cands, setCands] = useState<Span[]>([]);
  const [hiddenCands, setHiddenCands] = useState<Set<string>>(new Set());
  const [sel, setSel] = useState<Word[]>([]);
  const [drawn, setDrawn] = useState<Region | null>(null);
  const [mode, setMode] = useState<"select" | "region">("select");
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [verifier, setVerifier] = useState("");
  const drag = useRef<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const load = useCallback(async () => {
    const v = getViewer();
    setViewerState(v);
    const as = new URLSearchParams(window.location.search).get("as") || ANNOTATOR_FOR[v];
    setTarget(as);
    try {
      const [t, m, w, a] = await Promise.all([
        api<Taxonomy>("/taxonomy"),
        api<DocMeta>(`/documents/${doc}`),
        api<PageWords>(`/documents/${doc}/pages/${pageNo}/words`),
        api<AnnResp>(`/annotations/${as}/${doc}`),
      ]);
      setTax(t);
      setMeta(m);
      setPw(w);
      setAnn(a.annotation);
      setStored(a.stored_revision);
      setReadonly(a.readonly);
      setDirty(false);
      try {
        const regAnn = as === "adjudicated" ? "adjudicated" : as;
        setRegistry(await api(`/registry/${regAnn}`));
      } catch {
        setRegistry(null);
      }
      if (m.candidates_allowed && v !== "A2" && as !== "claude_silver" && as !== "A1_retest") {   // retest is blind (§9.3)
        try {
          const c = await api<{ spans: Span[] }>(`/candidates/${doc}`);
          setCands(c.spans.filter((s) => s.page === pageNo));
        } catch {
          setCands([]);
        }
      } else setCands([]);
    } catch (e) {
      setMsg(errorText(e));
    }
  }, [doc, pageNo]);

  useEffect(() => {
    load();
  }, [load]);

  const pageEntities = useMemo(() => (ann?.entities ?? []).filter((e) => e.page === pageNo), [ann, pageNo]);
  const selectedEntity = pageEntities.find((e) => e.entity_id === selected) ?? null;
  const W = pw?.width_pt ?? 595;
  const H = pw?.height_pt ?? 842;

  function toPt(ev: React.MouseEvent): { x: number; y: number } {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ((ev.clientX - r.left) / r.width) * W, y: ((ev.clientY - r.top) / r.height) * H };
  }

  function onDown(ev: React.MouseEvent) {
    drag.current = toPt(ev);
  }
  function onUp(ev: React.MouseEvent) {
    if (!drag.current || !pw) return;
    const a = drag.current;
    const b = toPt(ev);
    drag.current = null;
    const box = { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) };
    const isClick = box.x1 - box.x0 < 2 && box.y1 - box.y0 < 2;
    if (mode === "region" && !isClick) {
      setDrawn({ x0: round2(box.x0), y0: round2(box.y0), x1: round2(box.x1), y1: round2(box.y1) });
      setSel([]);
      setSelected(null);
      return;
    }
    if (isClick) {
      const hit = pw.words.find((w) => b.x >= w.bbox.x0 && b.x <= w.bbox.x1 && b.y >= w.bbox.y0 && b.y <= w.bbox.y1);
      if (!hit) {
        setSel([]);
        return;
      }
      if (ev.shiftKey && sel.length) {
        const lo = Math.min(hit.index, ...sel.map((s) => s.index));
        const hi = Math.max(hit.index, ...sel.map((s) => s.index));
        setSel(pw.words.filter((w) => w.index >= lo && w.index <= hi));
      } else if (ev.ctrlKey || ev.metaKey) {
        setSel(sel.some((s) => s.index === hit.index) ? sel.filter((s) => s.index !== hit.index) : [...sel, hit]);
      } else setSel([hit]);
    } else setSel(wordsInBox(pw.words, box));
    setDrawn(null);
    setSelected(null);
  }

  function update(next: AnnotationSet) {
    setAnn(next);
    setDirty(true);
  }

  function addEntity(e: Entity) {
    if (!ann) return;
    update({ ...ann, entities: [...(ann.entities ?? []), e] });
    setSel([]);
    setDrawn(null);
    setSelected(e.entity_id);
  }

  function newFromSelection(type: string) {
    if (!ann || !pw) return;
    const words = [...sel].sort((a, b) => a.index - b.index);
    const regionOnly = tax?.region_only.includes(type);
    let e: Entity;
    const base = {
      entity_id: nextEntityId(ann),
      entity_type: type as Entity["entity_type"],
      page: pageNo,
      action: "REVIEW" as const,
      action_source: "policy" as const,
      certainty: "certain" as const,
      flags: [] as NonNullable<Entity["flags"]>,
      attributes: {},
      notes: "",
      provenance: { annotator: (target === "adjudicated" ? "adjudicated" : target) as "A1" | "A2" | "adjudicated", pass: 1, origin: "manual" as const },
    };
    if (drawn) {
      e = { ...base, regions: [drawn], text: regionOnly ? null : "", certainty: regionOnly ? "certain" : "probable" };
    } else if (words.length) {
      const text = pw.text.slice(words[0].start, words[words.length - 1].end);
      const anchored = contiguous(words) && !regionOnly;
      e = {
        ...base,
        regions: regionsFor(words),
        text: regionOnly ? null : text,
        text_anchor: anchored ? { text_source_id: pw.text_source_id, start: words[0].start, end: words[words.length - 1].end } : null,
        flags: words.length > 1 && new Set(words.map((w) => w.line)).size > 1 ? ["split_line"] : [],
      };
    } else return;
    addEntity(e);
  }

  function patch(id: string, upd: Partial<Entity>) {
    if (!ann) return;
    update({ ...ann, entities: (ann.entities ?? []).map((e) => (e.entity_id === id ? { ...e, ...upd } : e)) });
  }

  function remove(id: string) {
    if (!ann) return;
    update({ ...ann, entities: (ann.entities ?? []).filter((e) => e.entity_id !== id) });
    setSelected(null);
  }

  function acceptCandidate(c: Span) {
    if (!ann || !pw) return;
    if ((ann.entities ?? []).some((e) => e.page === pageNo && overlapsAnchor(e, c.start, c.end, pw.text_source_id))) {
      setMsg("overlaps an existing mention");
      return;
    }
    const words = pw.words.filter((w) => w.start < c.end && c.start < w.end);
    addEntity({
      entity_id: nextEntityId(ann),
      entity_type: c.entity_type as Entity["entity_type"],
      page: pageNo,
      text: c.text,
      regions: regionsFor(words),
      text_anchor: { text_source_id: pw.text_source_id, start: c.start, end: c.end },
      action: "REVIEW",
      action_source: "policy",
      certainty: "certain",
      flags: [],
      attributes: {},
      notes: "",
      provenance: { annotator: target as "A1", pass: 1, origin: "preannotation_accepted" },
    });
  }

  function verifySilver(e: Entity) {
    if (!verifier.trim()) {
      setMsg("enter your initials as verifier first");
      return;
    }
    patch(e.entity_id, {
      provenance: { ...e.provenance, adjudication: "verified_silver", verified_by: verifier.trim(), verified_date: new Date().toISOString().slice(0, 10) },
    });
  }

  function togglePageComplete() {
    if (!ann) return;
    const cov = ann.coverage ?? {};
    const pages = new Set(cov.pages_complete ?? []);
    if (pages.has(pageNo)) pages.delete(pageNo);
    else pages.add(pageNo);
    update({ ...ann, coverage: { ...cov, pages_complete: [...pages].sort((a, b) => a - b) } });
  }

  async function save() {
    if (!ann) return;
    try {
      const r = await api<{ revision: number; warnings: string[] }>(`/annotations/${target}/${doc}`, {
        method: "PUT",
        json: { base_revision: stored, annotation: ann },
      });
      setMsg(`saved revision ${r.revision}${r.warnings.length ? ` · warnings: ${r.warnings.join(", ")}` : ""}`);
      await load();
    } catch (e) {
      setMsg(`not saved — ${errorText(e)}`);
    }
  }

  async function submit() {
    if (dirty) {
      setMsg("save first");
      return;
    }
    if (!window.confirm("Submit this file? Submitted files are final.")) return;
    try {
      await api(`/annotations/${target}/${doc}/submit`, { method: "POST" });
      await load();
      setMsg("submitted");
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  async function startFromSilver() {
    try {
      await api(`/annotations/A1/${doc}/start-from-silver`, { method: "POST" });
      await load();
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  async function addRegistryEntity(type: "PERSON" | "ORGANIZATION", role: string, label: string, gender: string): Promise<string | null> {
    if (!registry) return null;
    const ents = registry.registry.entities ?? [];
    const prefix = type === "PERSON" ? "person" : "org";
    const n = ents.filter((x) => x.canonical_id.includes(`/${prefix}_`)).length + 1;
    const id = `${registry.registry.matter_id}/${prefix}_${String(n).padStart(3, "0")}`;
    const ne: RegistryEntity = { canonical_id: id, entity_type: type, role, label, gender: type === "PERSON" ? (gender as RegistryEntity["gender"]) : null };
    try {
      const regAnn = target === "adjudicated" ? "adjudicated" : target;
      await api(`/registry/${regAnn}`, { method: "PUT", json: { base_sha: registry.sha, registry: { ...registry.registry, entities: [...ents, ne] } } });
      setRegistry(await api(`/registry/${regAnn}`));
      return id;
    } catch (e) {
      setMsg(errorText(e));
      return null;
    }
  }

  if (!meta || !pw || !ann || !tax) return <p>{msg || "loading…"}</p>;
  const complete = (ann.coverage?.pages_complete ?? []).includes(pageNo);
  const silverStart = target === "A1" && stored === 0 && !meta.iaa;

  return (
    <div>
      <div className="toolbar">
        <strong>
          {doc} · page {pageNo}/{meta.page_count}
        </strong>
        <span className="badge">{meta.split}</span>
        {meta.iaa && <span className="badge">IAA · blind</span>}
        <span className="badge">file: {target}</span>
        <span className="badge">rev {stored}</span>
        {readonly && <span className="badge">read-only</span>}
        <a href={`/annotate/${doc}/${Math.max(1, pageNo - 1)}?as=${target}`}>◀ prev</a>
        <a href={`/annotate/${doc}/${Math.min(meta.page_count, pageNo + 1)}?as=${target}`}>next ▶</a>
        <label className="row">
          <input type="radio" checked={mode === "select"} onChange={() => setMode("select")} /> select words
        </label>
        <label className="row">
          <input type="radio" checked={mode === "region"} onChange={() => setMode("region")} /> draw region
        </label>
        <label className="row">
          <input type="checkbox" checked={complete} disabled={readonly} onChange={togglePageComplete} /> page complete
        </label>
        <button className="primary" disabled={readonly || !dirty} onClick={save}>
          Save
        </button>
        <button disabled={readonly || target === "adjudicated"} onClick={submit}>
          Submit
        </button>
        {silverStart && <button onClick={startFromSilver}>Start from silver pass</button>}
        <span className={msg.startsWith("not saved") ? "err" : "muted"}>{msg}</span>
      </div>
      <div className="workspace">
        <div className="pageview">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/documents/${doc}/pages/${pageNo}/image?dpi=100`} alt={`page ${pageNo}`} draggable={false} />
          <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseDown={onDown} onMouseUp={onUp}>
            {pw.words.map((w) => (
              <rect key={w.index} x={w.bbox.x0} y={w.bbox.y0} width={w.bbox.x1 - w.bbox.x0} height={w.bbox.y1 - w.bbox.y0}
                fill={sel.some((s) => s.index === w.index) ? "rgba(31,95,191,0.25)" : "none"} stroke="rgba(0,0,0,0.12)" strokeWidth={0.3} />
            ))}
            {cands.filter((c) => !hiddenCands.has(`${c.start}-${c.end}`)).flatMap((c) =>
              c.bboxes.map((b, i) => (
                <rect key={`c${c.start}-${i}`} x={b.x0} y={b.y0} width={b.x1 - b.x0} height={b.y1 - b.y0} fill="none"
                  stroke={colourOf(c.entity_type)} strokeDasharray="2 1.5" strokeWidth={0.8} />
              )),
            )}
            {pageEntities.flatMap((e) =>
              (e.regions ?? []).map((r, i) => (
                <rect key={`${e.entity_id}-${i}`} x={r.x0} y={r.y0} width={r.x1 - r.x0} height={r.y1 - r.y0}
                  fill={colourOf(e.entity_type)} fillOpacity={e.entity_id === selected ? 0.35 : 0.15}
                  stroke={colourOf(e.entity_type)} strokeWidth={e.entity_id === selected ? 1.4 : 0.7} />
              )),
            )}
            {drawn && <rect x={drawn.x0} y={drawn.y0} width={drawn.x1 - drawn.x0} height={drawn.y1 - drawn.y0} fill="rgba(0,0,0,0.08)" stroke="#000" strokeDasharray="3 2" strokeWidth={0.8} />}
          </svg>
        </div>
        <div className="panel">
          {!readonly && (sel.length > 0 || drawn) && (
            <>
              <h3>New mention ({drawn ? "region" : `${sel.length} words${contiguous(sel) ? "" : ", not contiguous: regions only"}`})</h3>
              <div className="row">
                {tax.entity_types.filter((t) => t !== "OTHER").map((t) => (
                  <button key={t} onClick={() => newFromSelection(t)} style={{ borderColor: colourOf(t) }}>
                    {t}
                  </button>
                ))}
              </div>
            </>
          )}
          {selectedEntity && (
            <EntityForm key={selectedEntity.entity_id} e={selectedEntity} tax={tax} registry={registry?.registry ?? null} readonly={readonly}
              onPatch={(u) => patch(selectedEntity.entity_id, u)} onRemove={() => remove(selectedEntity.entity_id)} onNewCanonical={addRegistryEntity} />
          )}
          <h3>Mentions on this page ({pageEntities.length})</h3>
          {target === "A1" && pageEntities.some((e) => e.provenance.origin === "silver") && (
            <div className="row">
              <span className="muted">Verifier initials</span>
              <input value={verifier} onChange={(ev) => setVerifier(ev.target.value)} size={6} />
            </div>
          )}
          {pageEntities.map((e) => (
            <div key={e.entity_id} className={`ent ${e.entity_id === selected ? "sel" : ""}`} onClick={() => setSelected(e.entity_id)}>
              <span className="badge" style={{ background: colourOf(e.entity_type), color: "#fff" }}>{e.entity_type}</span>
              {e.role && <span className="badge">{e.role}</span>}
              <span className="badge">{e.action}</span>
              {e.provenance.origin === "silver" && (
                <span className={`badge ${e.provenance.adjudication === "verified_silver" ? "verified" : "silver"}`}>
                  {e.provenance.adjudication === "verified_silver" ? `verified ${e.provenance.verified_by ?? ""}` : "silver"}
                </span>
              )}
              <div>{e.text ?? <em className="muted">(region only)</em>}</div>
              {!readonly && e.provenance.origin === "silver" && e.provenance.adjudication !== "verified_silver" && (
                <div className="row">
                  <button onClick={(ev) => { ev.stopPropagation(); verifySilver(e); }}>Accept</button>
                  <button onClick={(ev) => { ev.stopPropagation(); remove(e.entity_id); }}>Reject</button>
                </div>
              )}
            </div>
          ))}
          {cands.length > 0 && !readonly && (
            <>
              <h3>Candidates (baseline; dev only, never gold until accepted)</h3>
              {cands.filter((c) => !hiddenCands.has(`${c.start}-${c.end}`)).map((c) => (
                <div key={`${c.start}-${c.end}-${c.entity_type}`} className="ent">
                  <span className="badge">{c.entity_type}</span> {c.text}
                  <div className="row">
                    <button onClick={() => acceptCandidate(c)}>Accept</button>
                    <button onClick={() => setHiddenCands(new Set([...hiddenCands, `${c.start}-${c.end}`]))}>Reject</button>
                  </div>
                </div>
              ))}
            </>
          )}
          <p className="muted">
            Click a word, shift-click to extend, ctrl-click to add, or drag a box. Region mode draws a free region (signatures, photos,
            text the OCR missed). Actions come from the policy on save; use an override only with a reason. Working as {viewer}.
          </p>
        </div>
      </div>
    </div>
  );
}

function EntityForm(props: {
  e: Entity;
  tax: Taxonomy;
  registry: CanonicalRegistry | null;
  readonly: boolean;
  onPatch: (u: Partial<Entity>) => void;
  onRemove: () => void;
  onNewCanonical: (t: "PERSON" | "ORGANIZATION", role: string, label: string, gender: string) => Promise<string | null>;
}) {
  const { e, tax, registry, readonly, onPatch } = props;
  const attrs = (e.attributes ?? {}) as Record<string, unknown>;
  const setAttr = (k: string, v: unknown) => {
    const next = { ...attrs };
    if (v === "" || v === null || v === undefined) delete next[k];
    else next[k] = v;
    onPatch({ attributes: next });
  };
  const roles = e.entity_type === "PERSON" ? tax.person_roles : e.entity_type === "ORGANIZATION" ? tax.org_roles : [];
  const canon = (registry?.entities ?? []).filter((r) => r.entity_type === e.entity_type);
  const flags = new Set(e.flags ?? []);
  const surface = e.text ?? "";
  return (
    <div>
      <h3>
        {e.entity_id} · {e.entity_type}
      </h3>
      <fieldset disabled={readonly} style={{ border: "none", padding: 0 }}>
        {e.text !== null && e.text !== undefined && (
          <div className="row">
            <span className="muted">Text</span>
            <input value={surface} size={34} onChange={(ev) => {
              const t = ev.target.value;
              const f = new Set(e.flags ?? []);
              if (e.text_anchor) f.add("ocr_degraded");
              onPatch({ text: t, flags: [...f] as Entity["flags"] });
            }} />
          </div>
        )}
        {roles.length > 0 && (
          <div className="row">
            <span className="muted">Role</span>
            <select value={e.role ?? ""} onChange={(ev) => onPatch({ role: ev.target.value || null })}>
              <option value="">—</option>
              {roles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        {(e.entity_type === "PERSON" || e.entity_type === "ORGANIZATION" || e.entity_type === "DATE_OF_BIRTH") && (
          <div className="row">
            <span className="muted">Canonical</span>
            <select value={e.canonical_id ?? ""} onChange={async (ev) => {
              if (ev.target.value === "__new" && e.entity_type !== "DATE_OF_BIRTH") {
                const label = window.prompt("Label for the new canonical entity (local only)", surface) ?? "";
                const gender = e.entity_type === "PERSON" ? window.prompt("Gender: female, male, non_binary or unknown", "unknown") ?? "unknown" : "";
                const id = await props.onNewCanonical(e.entity_type as "PERSON" | "ORGANIZATION", e.role ?? "other", label, gender);
                if (id) onPatch({ canonical_id: id });
              } else onPatch({ canonical_id: ev.target.value || null });
            }}>
              <option value="">—</option>
              {(e.entity_type === "DATE_OF_BIRTH" ? (registry?.entities ?? []).filter((r) => r.entity_type === "PERSON") : canon).map((r) => (
                <option key={r.canonical_id} value={r.canonical_id}>{r.canonical_id.split("/")[1]} · {r.label}</option>
              ))}
              {e.entity_type !== "DATE_OF_BIRTH" && <option value="__new">+ new…</option>}
            </select>
          </div>
        )}
        {e.entity_type === "DATE" && (
          <div className="row">
            <span className="muted">date_role</span>
            <select value={(attrs.date_role as string) ?? ""} onChange={(ev) => setAttr("date_role", ev.target.value)}>
              <option value="">—</option>
              {tax.date_roles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        {e.entity_type === "LOCATION" && (
          <div className="row">
            <span className="muted">granularity</span>
            <select value={(attrs.granularity as string) ?? ""} onChange={(ev) => setAttr("granularity", ev.target.value)}>
              <option value="">—</option>
              {tax.location_granularity.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        {e.entity_type === "PHONE" && (
          <div className="row">
            <span className="muted">number_class</span>
            <select value={(attrs.number_class as string) ?? ""} onChange={(ev) => setAttr("number_class", ev.target.value)}>
              <option value="">—</option>
              {["mobile", "landline", "13", "1300", "1800", "other"].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        {e.entity_type === "PERSON" && (
          <div className="row">
            <span className="muted">name_form</span>
            <select value={(attrs.name_form as string) ?? ""} onChange={(ev) => setAttr("name_form", ev.target.value)}>
              <option value="">—</option>
              {["full", "given_only", "surname_only", "title_surname", "initial_surname", "surname_comma_given", "initials"].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        {e.entity_type === "AGE" && (
          <div className="row">
            <span className="muted">stated_age</span>
            <input size={4} value={(attrs.stated_age as number | undefined) ?? ""} onChange={(ev) => setAttr("stated_age", ev.target.value ? Number(ev.target.value) : "")} />
            <span className="muted">ref date</span>
            <input size={10} placeholder="YYYY-MM-DD" value={(attrs.age_ref_date as string) ?? ""} onChange={(ev) => setAttr("age_ref_date", ev.target.value)} />
          </div>
        )}
        {e.entity_type === "NATIONALITY" && (
          <div className="row">
            <span className="muted">form</span>
            <select value={(attrs.form as string) ?? ""} onChange={(ev) => setAttr("form", ev.target.value)}>
              <option value="">—</option>
              {["demonym", "citizenship", "country_of_origin"].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
        <div className="row">
          <span className="muted">Certainty</span>
          <select value={e.certainty ?? "certain"} onChange={(ev) => onPatch({ certainty: ev.target.value as Entity["certainty"] })}>
            {tax.certainty.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="row">
          {tax.flags.map((f) => (
            <label key={f} className="row">
              <input type="checkbox" checked={flags.has(f as never)} onChange={() => {
                const n = new Set(flags);
                if (n.has(f as never)) n.delete(f as never);
                else n.add(f as never);
                onPatch({ flags: [...n] as Entity["flags"] });
              }} />
              {f}
            </label>
          ))}
        </div>
        <div className="row">
          <span className="muted">Override</span>
          <select value={e.action_source === "override" ? e.action : ""} onChange={(ev) => {
            const v = ev.target.value;
            if (!v) onPatch({ action_source: "policy", override_reason: null });
            else onPatch({ action_source: "override", action: v as Entity["action"], override_reason: e.override_reason ?? "" });
          }}>
            <option value="">policy ({e.action})</option>
            {["KEEP", "SYNTHETIC", "REDACT", "REVIEW"].map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          {e.action_source === "override" && (
            <input placeholder="reason (required)" value={e.override_reason ?? ""} onChange={(ev) => onPatch({ override_reason: ev.target.value })} />
          )}
        </div>
        <div className="row">
          <span className="muted">Notes</span>
          <textarea rows={2} cols={34} value={e.notes ?? ""} onChange={(ev) => onPatch({ notes: ev.target.value })} />
        </div>
        <div className="row">
          <button onClick={props.onRemove}>Delete mention</button>
        </div>
      </fieldset>
    </div>
  );
}
