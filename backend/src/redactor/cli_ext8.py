"""CLI for outputs (plan §4.12): `render`, `export`, `reidentify`. Aggregate output only: counts,
statuses and hashes. Rendered and restored texts go to files under exports/ (git-ignored), never to stdout."""

from __future__ import annotations

import json
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_render(args) -> int:
    from .core.canonical import read_json, sha256_hex
    from .output.export import verify_sha
    from .output.render import render_markdown, render_text
    src = Path(args.json)
    doc = read_json(src)
    if not verify_sha(doc):
        _print({"ok": False, "error": "output_sha256 does not match the document"})
        return 2
    text = render_text(doc) if args.format == "text" else render_markdown(doc)
    out = src.with_suffix(".txt" if args.format == "text" else ".md")
    out.write_text(text, encoding="utf-8", newline="\n")
    _print({"ok": True, "document_id": doc["document_id"], "format": args.format, "output": out.name, "sha256": sha256_hex(text)})
    return 0


def cmd_export(args) -> int:
    from .output.exporter import ExportRefused, export_matter
    from .security.access import allowed
    if not allowed("export", args.actor):
        _print({"ok": False, "error": "actor is not authorised for export (config/access.v0.1.yaml)"})
        return 3
    try:
        rep = export_matter(args.matter, formats=args.formats.split(","), dpi=args.dpi)
    except ExportRefused as exc:
        _print({"ok": False, "error": str(exc)})
        return 4
    _print(rep)
    return 0 if rep["exported"] and not rep["refused"] else 4


def cmd_reidentify(args) -> int:
    from .reidentify.service import ReidentifyError, restore_file
    from .security.access import allowed
    if not allowed("reidentify", args.actor):
        _print({"ok": False, "error": "actor is not authorised for re-identification (config/access.v0.1.yaml)"})
        return 3
    try:
        _print(restore_file(args.matter, Path(args.inp), args.out, actor=args.actor))
    except ReidentifyError as exc:
        _print({"ok": False, "error": str(exc)})
        return 2
    return 0


def cmd_intake_add(args) -> int:
    from .dataset.intake import IntakeError, add_pdf, process
    try:
        rec = add_pdf(Path(args.pdf).read_bytes(), args.matter)
    except IntakeError as exc:
        _print({"ok": False, "error": str(exc)})
        return 2
    if not args.no_process:
        rec["processed"] = process(rec["document_id"])
    _print(rec)
    return 0


def cmd_intake_process(args) -> int:
    from .dataset.intake import process
    _print(process(args.doc))
    return 0


def cmd_determinism_stages(args) -> int:
    from .evaluation.stage_determinism import run
    out = run(in_process=args.in_process, seeds=tuple(int(x) for x in args.seeds.split(",")))
    _print(out)
    return 0 if out["all_identical"] else 5


def cmd_vault_set_passphrase(args) -> int:
    """Interactive: the passphrase is typed twice (never echoed, never an argument) and stored in the OS
    credential store for this matter."""
    import getpass
    from .security import credstore
    a = getpass.getpass("vault passphrase: ")
    if len(a) < 12 or a != getpass.getpass("again: "):
        _print({"ok": False, "error": "passphrases differ or shorter than 12 characters"})
        return 2
    credstore.store(credstore.target(args.matter), a)
    _print({"ok": True, "matter_id": args.matter, "stored_in": "OS credential store", "target": credstore.target(args.matter)})
    return 0


def cmd_page_class(args) -> int:
    from .dataset import page_classes as pc
    if args.kind is None:
        st = pc.status(args.doc)
        _print({k: v for k, v in st.items() if k != "pages"} | {"unverified_pages": [p["page"] for p in st["pages"] if not p["verified"]]})
        return 0
    try:
        _print(pc.record(args.doc, args.page, args.kind, verified_by=args.by))
    except pc.PageClassError as exc:
        _print({"ok": False, "error": str(exc)})
        return 2
    return 0


def register(sub) -> None:
    pcl = sub.add_parser("page-class", help="human page-class verification (plan §9.2 P1); without --kind: status")
    pcl.add_argument("--doc", required=True)
    pcl.add_argument("--page", type=int)
    pcl.add_argument("--kind", choices=["born_digital", "scanned", "vector_outlined", "hybrid", "blank", "unknown"])
    pcl.add_argument("--by", help="verifier initials")
    pcl.set_defaults(func=cmd_page_class)
    vk = sub.add_parser("vault", help="vault key custody (plan §10 Q21)").add_subparsers(dest="command", required=True)
    sp = vk.add_parser("set-passphrase", help="store a matter's vault passphrase in the OS credential store (interactive)")
    sp.add_argument("--matter", required=True)
    sp.set_defaults(func=cmd_vault_set_passphrase)
    dt = sub.add_parser("determinism", help="stage-isolated determinism checks (plan §4.7)").add_subparsers(dest="command", required=True)
    st = dt.add_parser("stages", help="replacement, gate, JSON/text/Markdown, PDF bytes and raster, re-identification "
                                      "on synthetic data, in-process and in fresh processes")
    st.add_argument("--in-process", type=int, default=3)
    st.add_argument("--seeds", default="11,23,37")
    st.set_defaults(func=cmd_determinism_stages)
    ik = sub.add_parser("intake", help="register an uploaded PDF for processing (split `intake`, never scored)").add_subparsers(
        dest="command", required=True)
    a = ik.add_parser("add", help="register a PDF (its filename is not stored), then classify and extract it")
    a.add_argument("--pdf", required=True)
    a.add_argument("--matter", required=True)
    a.add_argument("--no-process", action="store_true")
    a.set_defaults(func=cmd_intake_add)
    pr = ik.add_parser("process", help="classify and extract one intake document")
    pr.add_argument("--doc", required=True)
    pr.set_defaults(func=cmd_intake_process)
    r = sub.add_parser("render", help="render an exported pseudonymised JSON to text or Markdown (next to it)")
    r.add_argument("json")
    r.add_argument("--format", choices=["text", "md"], required=True)
    r.set_defaults(func=cmd_render)
    e = sub.add_parser("export", help="export a matter's outputs; refused while the gate is not clear (no force option)")
    e.add_argument("--matter", required=True)
    e.add_argument("--formats", default="json,text,md,pdf")
    e.add_argument("--dpi", type=int, default=300)
    e.add_argument("--actor", required=True)
    e.set_defaults(func=cmd_export)
    x = sub.add_parser("reidentify", help="restore real values in downstream text through the matter vault (local only)")
    x.add_argument("--matter", required=True)
    x.add_argument("--in", dest="inp", required=True)
    x.add_argument("--out", required=True, help="file name; written under exports/<matter>/reidentified/")
    x.add_argument("--actor", required=True)
    x.set_defaults(func=cmd_reidentify)
