"use client";
import { useEffect, useState } from "react";
import { getViewer, setViewer, VIEWERS, type Viewer } from "@/lib/api";

export function ViewerSwitch() {
  const [v, setV] = useState<Viewer>("A1");
  useEffect(() => setV(getViewer()), []);
  return (
    <label className="row">
      <span className="muted">Working as</span>
      <select
        value={v}
        onChange={(e) => {
          const nv = e.target.value as Viewer;
          setViewer(nv);
          setV(nv);
          window.location.reload();
        }}
      >
        {VIEWERS.map((x) => (
          <option key={x} value={x}>
            {x}
          </option>
        ))}
      </select>
    </label>
  );
}
