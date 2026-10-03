// Pure helpers for the annotation workspace (no React).
import type { AnnotationSet, Entity, Region } from "./api-types";

export type Word = {
  index: number;
  text: string;
  bbox: { x0: number; y0: number; x1: number; y1: number };
  conf: number | null;
  block: number;
  line: number;
  start: number;
  end: number;
};

export type PageWords = { text_source_id: string; width_pt: number; height_pt: number; text: string; words: Word[] };

export const TYPE_COLOURS: Record<string, string> = {
  PERSON: "#d62728",
  ORGANIZATION: "#1f77b4",
  ADDRESS: "#9467bd",
  LOCATION: "#ff7f0e",
  DATE: "#2ca02c",
  DATE_OF_BIRTH: "#8c564b",
  AGE: "#17becf",
  GENDER: "#bcbd22",
  SIGNATURE: "#000000",
  PHOTO: "#000000",
};

export const colourOf = (t: string) => TYPE_COLOURS[t] ?? "#7f7f7f";

/** One region per line of the selected words (union of their boxes). */
export function regionsFor(words: Word[]): Region[] {
  const byLine = new Map<number, Word[]>();
  for (const w of words) byLine.set(w.line, [...(byLine.get(w.line) ?? []), w]);
  return [...byLine.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, ws]) => ({
      x0: round2(Math.min(...ws.map((w) => w.bbox.x0))),
      y0: round2(Math.min(...ws.map((w) => w.bbox.y0))),
      x1: round2(Math.max(...ws.map((w) => w.bbox.x1))),
      y1: round2(Math.max(...ws.map((w) => w.bbox.y1))),
    }));
}

export const round2 = (x: number) => Math.round(x * 100) / 100;

/** Selected words are contiguous in text order (then they can carry a text anchor). */
export function contiguous(sel: Word[]): boolean {
  const idx = sel.map((w) => w.index).sort((a, b) => a - b);
  return idx.every((v, i) => i === 0 || v === idx[i - 1] + 1);
}

export function nextEntityId(ann: AnnotationSet): string {
  let n = 0;
  for (const e of ann.entities ?? []) {
    const m = /\.e(\d+)$/.exec(e.entity_id);
    if (m) n = Math.max(n, parseInt(m[1], 10));
  }
  return `${ann.document_id}.e${String(n + 1).padStart(4, "0")}`;
}

export function wordsInBox(words: Word[], b: { x0: number; y0: number; x1: number; y1: number }): Word[] {
  return words.filter((w) => {
    const cx = (w.bbox.x0 + w.bbox.x1) / 2;
    const cy = (w.bbox.y0 + w.bbox.y1) / 2;
    return cx >= b.x0 && cx <= b.x1 && cy >= b.y0 && cy <= b.y1;
  });
}

export function overlapsAnchor(e: Entity, start: number, end: number, source: string): boolean {
  const a = e.text_anchor;
  return !!a && a.text_source_id === source && a.start < end && start < a.end;
}

export const ROLE_TYPES = new Set(["PERSON", "ORGANIZATION"]);
