# redaction-tool-from-scratch

Local, deterministic PII pseudonymization and evaluation platform for confidential Australian medico-legal PDFs.

## Plan of record
- The plan is `plans/PLAN_REV2_parts/part_01.md` to `part_04.md`, read in order. `plans/PLAN_REV2.md` is stale and `plans/PLAN_V0.md` is revision 1; do not build from them.
- Build all of §11 in this order: F1, F2, F3, S1, F4, F5, A1, R1, G1, X1, C1, U1 (C2: combiner options and experiment configs only). A2 (human annotation) is not the assistant's: build its tooling, then stop. F0 is done.
- Keep `docs/build_progress.md` (tracked, no PII): after each milestone record what was built, test results, deviations and open items. At the start of a session or after context compaction, read it first and continue from the next unfinished item.
- Stop and ask only if the plan is unclear or contradicts itself, a permission check blocks an action (report it, never work around it), or S1 validation cannot be met.

## Data boundary (plan §2): hard rules
- Claude Code is a cloud API. Outside S1, never read, print, OCR or annotate document content in `golden_dataset_docs/`, `golden_dataset/`, `data/`, `exports/` or `runs/**/private/`. `.claude/settings.json` denies the file-reading tools there; never work around it with shell commands.
- EX-001 (silver annotation, milestone S1) is CLOSED (2026-10-01, model `claude-opus-5-5`; `docs/data_exceptions.md`). The deny rules are restored. Any further reading of document content needs a new, recorded exception.
- File names in `golden_dataset_docs/` contain PII. Never list or print them in logs or summaries; refer to documents by doc_id (doc_001 …). S1 may read them only to annotate the `filename` field.
- Every CLI and test prints aggregates only: counts, rates, hashes and IDs. Text output needs `--show-text`, which the assistant never passes.
- Unit tests use synthetic fixtures. Checks that touch real documents go in `backend/tests_local/`; by the owner's standing authorisation (2026-10-01) the assistant may run them and the real-file commands (register, extraction, OCR, smoke run), with aggregate-only output.
- No network calls with document data, no telemetry, no hosted OCR, LLM or embedding services. Installs are pre-approved only for pinned packages and images listed in plan §6 and the build prompt, recorded in lockfiles and `docs/build_progress.md`; ask first for anything else.
- Gate thresholds: set provisional values from silver labels and label them provisional.

## Git
- The repo has a GitHub remote. Never change it. Never commit or push unless the owner asks.
- `.gitignore` keeps the confidential folders and PDFs out. The hooks in `.githooks/` refuse confidential paths, PDFs and images; never bypass them with `--no-verify`.
- After a fresh clone, activate the hooks: `git config core.hooksPath .githooks`. When committing the hook files, keep them executable: `git add --chmod=+x .githooks/pre-commit .githooks/pre-push`.
- Check at any time: `sh .githooks/check-tracked.sh`.

## Determinism (plan §4.7)
- Explicit total ordering for every emitted list; never depend on set or dict iteration order.
- No `hash()`, `uuid4`, wall-clock or random values in hashed outputs; derive IDs with SHA-256.
- Canonical JSON (sorted keys, UTF-8, fixed float rounding) for everything that is hashed or compared.
- Pin versions: lockfiles, model hashes, container digests. Determinism runs are single-threaded (`OMP_THREAD_LIMIT=1`).
- Determinism tests run both in-process and in fresh processes with different `PYTHONHASHSEED` values.
