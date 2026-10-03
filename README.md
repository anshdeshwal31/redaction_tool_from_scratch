# redaction-tool-from-scratch

Local, deterministic PII pseudonymisation and evaluation platform for confidential Australian medico-legal
PDFs. Everything runs on this machine: no cloud OCR, LLM or embedding service, no telemetry.

The plan of record is `plans/PLAN_REV2_parts/part_01.md` to `part_04.md`. Build progress, measured results
and open items are in `docs/build_progress.md`.

## Pseudonymisation is not anonymisation

The outputs replace names, contact details, identifiers and dates of birth with consistent surrogates, but
they are **still personal information** under the Privacy Act 1988. An incident narrative combined with
dates, an employer and an occupation can identify the client (for example through news reports).
Pseudonymised outputs need the same care as the originals. Run a human re-identification test on a sample of
exports before any production use.

## Data rules

- Original PDFs live in `golden_dataset_docs/`, derived ground truth in `golden_dataset/`, caches, vaults
  and staged outputs in `data/`, exports in `exports/`, private run detail in `runs/*/private/`. All of these
  are git-ignored and can live outside the repo (`REDACTOR_DATA_ROOT`).
- Command-line tools and tests print counts, rates, hashes and IDs only. File names of the originals are
  never printed; documents are referred to by `doc_001` … .
- Git hooks in `.githooks/` refuse confidential paths, PDFs and images. After a clone:
  `git config core.hooksPath .githooks`. Check at any time: `sh .githooks/check-tracked.sh`.

## Setup

- Backend: Python 3.12 managed by uv. `cd backend && uv sync`.
- OCR: the pinned Tesseract image `redactor-tesseract:0.1.0` (`docker/tesseract/Dockerfile`).
- Candidates (optional): Presidio with spaCy `en_core_web_lg` (in `uv.lock`), OpenRedaction
  (`cd sidecars/openredaction && pnpm install`), Philter (`docker pull philterd/philter:3.4.1`).
- Frontend: `cd frontend && pnpm install && pnpm run build`.
- Vault passphrase per matter: `uv run redactor vault set-passphrase --matter matter_001` (stored in the OS
  credential store), or the `REDACTOR_VAULT_PASSPHRASE` environment variable.

## Main commands (run from `backend/`)

| Task | Command |
|---|---|
| Register and classify documents | `uv run redactor dataset register`, `uv run redactor ingest classify` |
| Extract text and OCR | `uv run redactor extract` |
| Run an experiment | `uv run redactor run ../experiments/baseline.yaml` |
| Production pipeline with the release gate | `uv run redactor pipeline run --matter matter_001` |
| Export (refused while the gate is not clear) | `uv run redactor export --matter matter_001 --actor owner` |
| Restore real names in downstream text | `uv run redactor reidentify --matter matter_001 --in answer.txt --out restored.txt --actor owner` |
| Stage determinism check | `uv run redactor determinism stages` |
| Local API | `uv run redactor api serve` (127.0.0.1:8765) |
| UI | `cd frontend && pnpm run start` (127.0.0.1:3000) |
| Tests | `uv run pytest` (synthetic only); real-data checks in `tests_local/` |

Roles for export and re-identification are set in `config/access.v0.1.yaml`.
