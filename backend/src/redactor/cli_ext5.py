"""CLI for A1 onwards: local API, type generation, promotion and IAA. Aggregate output only."""

from __future__ import annotations

import datetime as _dt
import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_api_serve(args) -> int:
    from .api.app import serve
    serve(args.port)
    return 0


def cmd_api_types(args) -> int:
    from .api.app import create_app
    from .api.typegen import write
    from .paths import REPO_ROOT, schemas_dir
    openapi = create_app().openapi()
    (REPO_ROOT / "frontend" / "src" / "lib").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "frontend" / "openapi.json").write_text(json.dumps(openapi, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    n = write(REPO_ROOT / "frontend" / "src" / "lib" / "api-types.ts", openapi, schemas_dir())
    _print({"types": n, "paths": len(openapi.get("paths", {}))})
    return 0


def cmd_promote(args) -> int:
    from .dataset.store import StoreError, promote
    try:
        out = promote(args.doc, source=args.source, verified_by=args.verified_by, verified_date=args.date or _dt.date.today().isoformat())
    except StoreError as exc:
        _print({"ok": False, "error": exc.code, "detail": exc.detail if isinstance(exc.detail, (list, int, str)) else None})
        return 1
    _print({"ok": True, **out})
    return 0


def iaa_stem(a: str, b: str) -> str:
    """A1 vs A2 keeps the historical name; other pairs (silver vs gold, A1 test-retest) get their own files."""
    return "iaa" if (a, b) == ("A1", "A2") else f"iaa.{a}__{b}"


def cmd_iaa(args) -> int:
    """Writes golden_dataset/iaa/iaa.public.json (metrics) and iaa.private.json (worklist); prints metrics only."""
    from .core.canonical import write_canonical
    from .dataset import store
    from .evaluation.gold import load_gold
    from .evaluation.iaa import compare
    from .experiments.runner import load_document_text, page_classes_for
    from .paths import golden_dir
    from .taxonomy import load_policy, load_taxonomy
    pub_all, priv_all = {}, {}
    for doc in args.doc:
        pa, pb = store.annotation_path(args.a, doc), store.annotation_path(args.b, doc)
        if not (pa.exists() and pb.exists()):
            pub_all[doc] = {"error": "missing annotation file"}
            continue
        pub, priv = compare(load_document_text(doc), load_gold(doc, path=pa), load_gold(doc, path=pb), policy=load_policy(),
                            taxonomy=load_taxonomy(), page_classes=page_classes_for(doc))
        pub_all[doc], priv_all[doc] = pub, priv
    out = golden_dir() / "iaa"
    stem = iaa_stem(args.a, args.b)
    kind = {"A1_retest": "test_retest", "claude_silver": "silver_vs_human"}.get(args.b if args.a == "A1" else args.a, "inter_annotator")
    write_canonical(out / f"{stem}.public.json", {"a": args.a, "b": args.b, "kind": kind, "documents": pub_all})
    write_canonical(out / f"{stem}.private.json", {"a": args.a, "b": args.b, "kind": kind, "documents": priv_all})
    _print({d: {"strict_f1": v.get("span", {}).get("strict", {}).get("a_as_gold", {}).get("f1"),
                "kappa_protect": v.get("kappa", {}).get("protect_binary")} if "span" in v else v for d, v in pub_all.items()})
    return 0


def cmd_campaign(args) -> int:
    from .dataset import campaign as c
    try:
        if args.command == "audit":
            out = [c.audit_pool(d) for d in args.doc]
        elif args.command == "transcript":
            out = c.seed_transcript(args.doc, args.page)
        elif args.command == "migrate":
            from .paths import golden_dir
            files = sorted((golden_dir() / "annotations_raw").rglob("*.ann.json")) + sorted((golden_dir() / "annotations").glob("*.ann.json"))
            out = [c.migrate_file(f, args.to) for f in files]
        elif args.command == "freeze":
            out = c.freeze(args.version, commit=args.commit)
        else:
            out = c.verify()
    except c.CampaignError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print(out)
    return 0 if (not isinstance(out, dict) or out.get("ok", True)) else 1


def register(sub) -> None:
    api = sub.add_parser("api").add_subparsers(dest="command", required=True)
    s = api.add_parser("serve", help="local API on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_api_serve)
    api.add_parser("types", help="write frontend/openapi.json and frontend/src/lib/api-types.ts").set_defaults(func=cmd_api_types)
    dataset = next(a for a in sub.choices["dataset"]._actions if a.dest == "command")
    pr = dataset.add_parser("promote", help="copy a submitted, verified annotator file into annotations/ (gold)")
    pr.add_argument("--doc", required=True)
    pr.add_argument("--source", default="A1", choices=("A1", "A2"))
    pr.add_argument("--verified-by", required=True)
    pr.add_argument("--date", default=None)
    pr.set_defaults(func=cmd_promote)
    au = dataset.add_parser("audit", help="recall audit: pool local detectors, write pending audit items (P7)")
    au.add_argument("--doc", action="append", required=True)
    au.set_defaults(func=cmd_campaign)
    tr = dataset.add_parser("transcript", help="seed a gold page transcript from the reference OCR (person verifies)")
    tr.add_argument("--doc", required=True)
    tr.add_argument("--page", type=int, required=True)
    tr.set_defaults(func=cmd_campaign)
    mg = dataset.add_parser("migrate", help="explicit schema migration of every annotation file (originals kept)")
    mg.add_argument("--to", required=True)
    mg.set_defaults(func=cmd_campaign)
    fr = dataset.add_parser("freeze", help="validate, renumber gold IDs, record file hashes in the manifest")
    fr.add_argument("--version", required=True)
    fr.add_argument("--commit", action="store_true", help="owner only: commit and tag in the local golden repo")
    fr.set_defaults(func=cmd_campaign)
    dataset.add_parser("verify", help="revalidate every file and compare hashes with the frozen manifest").set_defaults(func=cmd_campaign)
    ia = sub.add_parser("iaa", help="inter-annotator agreement (public metrics + private worklist)")
    ia.add_argument("--doc", action="append", required=True)
    ia.add_argument("--a", default="A1")
    ia.add_argument("--b", default="A2")
    ia.set_defaults(func=cmd_iaa)
    try:
        from . import cli_ext6
        cli_ext6.register(sub)
    except ImportError:
        pass
