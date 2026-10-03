// Number formatting for metric tables: a rate with trailing zeros trimmed, or a percentage; "–" when undefined.
export const f = (v: number | null | undefined, pct = false): string =>
  v === null || v === undefined ? "–" : pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
