# Recorded data exceptions

Tracked file, no PII. The default rule (plan §2.2) is that the development assistant, Claude Code, never reads, OCRs or annotates the confidential documents. Each exception below is explicit, scoped and recorded here.

## EX-001: silver first-pass annotation by Claude Code

| Field | Value |
|---|---|
| Status | **CLOSED** on 2026-10-01 (active 2026-10-01 to 2026-10-01). Model for the whole pass: `claude-opus-5-5`. Deny rules restored in `.claude/settings.json` the same day |
| Authorised by | The project owner (Ansh Deshwal, per the session's git identity), by instruction in the Claude Code session of 2026-10-01 |
| Firm sign-off | The owner acts for the firm. If the firm requires another signatory, add the name and date here before the silver pass (S1) starts: ____________ |
| Scope | The five documents in `golden_dataset_docs/` (doc_001 to doc_005). Annotation only: entity annotations and draft page transcripts. New documents need their own exception |
| Data sent to the model provider | Page images and extracted text (words with boxes) of the documents, their filenames and PDF metadata values, and the annotations Claude writes |
| When | Milestone S1, after F1–F3 exist and schema v0.1 and guidelines v0.1 are written (plan §11) |
| Model | One model for the whole pass. Its ID, the run date, the guidelines version, the prompt hash and the input hashes are recorded in every silver file |
| Output | `golden_dataset/annotations_raw/claude_silver/`, labelled **silver** (`annotator: claude_silver`). Never written to `annotations/` |
| Trust rule | No score is trusted until a person has verified the whole test split (doc_001, doc_004) and a seeded random sample of the other pages (plan §9.4). Silver is never promoted to gold without that verification |
| Access rule | The deny rules on `golden_dataset_docs/**`, `golden_dataset/**` and `data/**` (Read) are lifted for S1 only. The Edit deny on `golden_dataset_docs/**` stays. All rules are restored when S1 ends |

## Standing authorisations given by the owner on 2026-10-01

| Authorisation | Conditions |
|---|---|
| Real-file runs by the assistant: `dataset register`, extraction and OCR of real pages, the real-data smoke run, `tests_local/` | Output is aggregate-only: counts, rates, hashes and IDs. No document text and no file names are printed or logged |
| Installs | Pinned packages and images listed in plan §6 and named in the build prompt. Each is recorded in a lockfile and in `docs/build_progress.md`. Anything else is asked first |
| Gate thresholds | The assistant sets provisional values from silver labels and labels them provisional. They are re-calibrated on verified gold |

## Closing the exception
When S1 ends: restore the deny rules, record the date and the model ID here, and set the status to CLOSED.
