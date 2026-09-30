### 4.11 Production release gate (fail-closed)
**Purpose.** Nothing is exported unless every page has been checked. Anything uncertain goes to a person. Any residual the final leak scan can find blocks the export. The gate is also evaluated offline against the golden_dataset (§4.5, "Release gate").

**States and export rule**
- `PASS`: the page meets every criterion.
- `REVIEW`: a person must decide before export.
- `BLOCKED`: the final leak scan found a residual. Nothing from the document is exported until it is resolved.
- A document is exported only when every page is `PASS` (or `REVIEW` with a recorded decision) and the final leak scan is clean. There is **no force flag**. The only override is a recorded decision by an authorised reviewer.

**Triggers** (`config/release_gate.v0.1.yaml`). Thresholds are provisional: calibrated on silver labels of the dev split, re-calibrated on verified gold, and frozen before the test split is scored. Any numbers quoted in this plan are placeholders, not results.

| Trigger | Action |
|---|---|
| Page class `unknown` | REVIEW immediately. No retry. |
| Handwriting flag (the annotation flag in evaluation; a heuristic or a reviewer flag in production) | REVIEW immediately. No retry. |
| Low OCR confidence: page mean word confidence below `T_mean`, or the share of words below `T_word` above `T_frac` | Retry ladder, then REVIEW if the page is still low. |
| Two OCR engines disagree on the page (after the comparison) | REVIEW. |
| Detector disagreement (multi-detector experiments): same region with a different type, a different action (protect vs KEEP), or a boundary conflict beyond tolerance | The region is protected (fail-closed) and the page goes to REVIEW. |
| A detection from another OCR attempt that cannot be projected onto the chosen text | The region is boxed and the page goes to REVIEW. |
| Raster region on a text-layer page that contains text or cannot be classified as blank or logo | Hybrid OCR; if still unclassified, REVIEW. |
| `DOB_AGE_CONFLICT`, or a surrogate that collides with the source text (§4.9.5) | REVIEW. |

**OCR retry ladder** (a fixed order, bounded, and every attempt is recorded in the run manifest)
1. **Retry the same engine with different settings**, in a configured sequence: higher resolution, deskew, a different page-segmentation mode, contrast cleanup.
2. **Try a second OCR engine** (RapidOCR or docTR, both local) and compare the two results.
3. **If the page still disagrees or stays low-confidence, send it to REVIEW.**

Rules:
- **A retry may only improve a page's status, never let it through unchecked.** After every attempt the page runs the same gate checks again. Nothing is waived because a retry happened.
- **Handwriting and unknown page classes go straight to REVIEW** and skip the ladder.
- **Protection is the union over attempts.** Detections from every attempt and engine are projected onto the chosen text source (the bounding-box projection of §4.3). Anything that cannot be projected is boxed as a region and sent to REVIEW. A retry can add protection but never remove it.
- **The final leak scan runs after all retries**, and again on each export artifact.
- `second_engine` is `on_low_confidence` by default. A `strict` mode runs it on every OCR page for corroboration. Both modes are compared in the evaluation.

**Final leak scan** (on the pseudonymized text, the JSON and, for PDFs, the re-OCR of the burned pages)
- **Vault surfaces:** every real value and form stored for the matter (names and name components of 3+ characters, identifiers digits-only, addresses, phones, emails, DOB forms), normalized, with exact and fuzzy matching (edit distance ≤ 1 for strings of 6+ characters, plus common OCR confusions). REDACT-class values are matched by keyed hash.
- **Checksum-valid identifiers:** any digit sequence that validates as a TFN, ABN, ACN, Medicare number, IHI or provider number. Surrogates are invalid by design, so a valid hit is a real identifier.
- **Detector re-run** (the baseline always, other detectors when configured): any protect-type hit that is not a known surrogate or token. The allow-list comes from the vault.
- **A hit means BLOCKED**, with its location. Resolution needs a recorded decision: a fix and re-run, or a false alarm with a reason.
- **Limit, stated plainly.** The scan only knows what the vault, the validators and the detectors know. A name nobody detected is invisible to it. Recall is measured offline against the golden_dataset. The gate's job is to send uncertain pages to a person and to catch what can be known. A matter seed list would strengthen it (could_be_changed).

**Review queue**
- The reviewer sees the page image with boxes, the candidate spans and the reasons.
- Decisions: confirm protect, mark not-PII (with a reason), add a span or region, flag handwriting, or accept the OCR as-is (allowed only for confidence triggers).
- Every decision is stored (reviewer, time, reason) in an append-only audit log. Decisions are inputs to the pipeline, so the same decisions always give the same output hash.

### 4.12 Outputs and local re-identification

#### 4.12.1 Primary output: structured pseudonymized JSON
`redaction.pseudonymized_document` v0.1, written as canonical JSON:
```json
{
  "schema": "redaction.pseudonymized_document", "schema_version": "0.1.0",
  "document_id": "doc_003", "matter_id": "matter_001", "source_sha256": "349a3cba361d…",
  "pipeline": { "extraction": "…", "detectors": ["baseline@0.2.0"], "linker": "…", "policy": "default-au-pi@0.1.0",
                "replacement": "surrogate@0.1.0", "vault_epoch": 1, "config_hash": "…" },
  "pages": [{
    "page": 1, "content_kind": "born_digital", "text_source_id": "text_layer.pdfium@…",
    "text": "<pseudonymized page text>",
    "gate": { "status": "PASS", "reasons": [] },
    "spans": [{ "span_id": "p1.s0007", "start": 120, "end": 132, "entity_type": "PERSON",
                "canonical_id": "matter_001/person_001", "action": "SYNTHETIC",
                "bboxes": [{ "x0": 0, "y0": 0, "x1": 0, "y1": 0 }] }]
  }],
  "edit_log": [{ "edit_id": "e0007", "page": 1, "span_id": "p1.s0007", "new_start": 120, "new_end": 132,
                 "entity_type": "PERSON", "canonical_id": "matter_001/person_001", "action": "SYNTHETIC",
                 "strategy": "name_components", "rule_id": "names.propagated" }],
  "gate": { "status": "PASS", "leak_scan": "clean" },
  "output_sha256": "…"
}
```
- **No original text anywhere in the file.** Originals exist only in the vault. The exported edit log has no original offsets or lengths. A local sidecar (`exports/<matter>/private/edit_log.private.json`) holds them, with a keyed hash of each original, for audit and for the edit-completeness test.
- `canonical_id` values are opaque (`person_001`). They mean nothing without the vault.
- `output_sha256` is the hash of the canonical JSON without that field.

#### 4.12.2 Renders: text and Markdown
Pure, deterministic functions of the JSON (`redactor render --format text` or `--format md`). Text pages are separated by a fixed separator. Markdown adds a heading per page. Tokens such as `[NATIONALITY]` pass through. A render never adds information that is not in the JSON.

#### 4.12.3 PDF output: rasterize and burn in boxes
- **Rasterize every page** (born-digital pages too) with a pinned renderer and DPI, so no text layer, hidden text or vector text survives.
- **Burn opaque boxes into the pixels** over every span whose action is SYNTHETIC or REDACT, using the span's word boxes (padded, snapped to the line height). A span with no box falls back to its whole line (fail-closed). Regions that are annotated or drawn by a reviewer (signatures, photos, handwriting) are boxed the same way.
- **No synthetic text is drawn into the scan.** The surrogate text exists only in the JSON, text and Markdown outputs.
- **Clean container.** The output PDF is assembled from the page images by a deterministic writer: no timestamps, a fixed document ID, no original filename, and no Info or XMP metadata (only the `doc_id`).
- **Output verification.** Each burned page is re-OCRed locally and run through the final leak scan (§4.11). Any hit blocks the export.
- A per-page box manifest is kept locally for QA.

#### 4.12.4 Local re-identification through the vault
**Use case.** A downstream tool (for example an LLM) works on the pseudonymized text. Its answer contains fake names such as "John Smith". The vault maps each fake name back to the real one, so the real names can be restored in that answer. This happens only on the firm's machine. The vault, which holds the real data, never leaves it.

**Mechanism** (`redactor reidentify --matter M --in answer.txt --out restored.txt`, or `POST /reidentify`)
- The reverse index comes from the vault: every surrogate form that was issued, plus the component maps (surrogate surname → real surname, surrogate first name → real first name). Because the forward mapping is injective, the reverse mapping is unambiguous. A form that was never issued still resolves by components: "Dr Jones" becomes "Dr Smith" even if only "Jones" appeared in the export.
- Matching is deterministic: longest match first, on word boundaries, case-sensitive first and then case-insensitive with the casing restored. Possessives, titles, initials and "Surname, Given" order are handled.
- **Safe failure.** Anything that is not in the vault, or is ambiguous, is left unchanged and listed in the report. The tool never invents a real value.
- **One-way types are never restored.** REDACT tokens (`[TFN]`, `[NATIONALITY]`, `[DATE_OF_BIRTH]`) stay as they are, and the vault holds no plaintext for them.
- **Report.** Counts per type, plus unmatched and ambiguous surrogate-like tokens, with no PII in the report.

**Security**
- Localhost only, for an authorised role. Every call is written to the audit log (counts and hashes only).
- The restored text contains real PII. It is written only under `exports/<matter>/reidentified/` (git-ignored) and never passes through any external process.
- It needs the vault and its key. A lost key means the mapping is lost (§10 Q21).
- Whether the pseudonymized text may leave the machine for a downstream tool at all is the firm's decision (§10 Q20). The system never sends it anywhere itself.

**Limits.** An LLM may paraphrase or invent new forms (nicknames, "Mr S."). Those cannot be restored, so they are left as they are and reported. Partial restoration leaves surrogates in place, which is the safe direction.

---

## 5. Directory structure
```
redaction-tool-from-scratch/
├── .gitignore  .pre-commit-config.yaml  CLAUDE.md  README.md
├── .claude/settings.json            # assistant deny rules (§2.2)
├── plans/PLAN_REV2_parts/           # this plan (PLAN_V0.md is revision 1)
├── docs/annotation_guidelines.md    # tracked, no PII
├── docs/data_exceptions.md          # recorded data exceptions (EX-001), tracked, no PII
├── golden_dataset_docs/             # original client PDFs: immutable, git-ignored, never modified
├── golden_dataset/                  # CONFIDENTIAL derived ground truth: git-ignored, own local-only repo (§3.2)
├── schemas/                         # generated JSON Schemas (tracked)
├── config/                          # taxonomy, policy, baseline rules, forms/, extraction configs, release_gate, surrogate lists
├── experiments/                     # experiment YAMLs (tracked, no PII)
├── backend/                         # Python 3.12, uv, FastAPI
│   ├── pyproject.toml  uv.lock
│   ├── src/redactor/
│   │   ├── core/          # BBox, Word, PageExtraction, EntitySpan, canonical JSON, hashing, coord transforms
│   │   ├── dataset/       # pydantic golden models, validate/migrate, register, manifest, freeze/verify
│   │   ├── ingest/        # fingerprint, page classifier
│   │   ├── extraction/    # Extractor protocol, render, text_layer, ocr_tesseract, ocr_second_engine, hybrid, retry ladder, cache
│   │   ├── detectors/     # protocol, registry, combiner, baseline/ (incl. layout), adapters/{presidio,openredaction,philter}
│   │   ├── linking/  policy/
│   │   ├── replacement/   # names, dob, identifiers, vault
│   │   ├── gate/          # triggers, retry orchestration, final leak scan, review queue
│   │   ├── output/        # pseudonymized JSON, text/Markdown renders, PDF burn-in writer, output verification
│   │   ├── reidentify/    # reverse index, matcher, report
│   │   ├── evaluation/    # projection, matching, metrics/, attribution, determinism, iaa
│   │   ├── experiments/   # runner, run manifest, environment fingerprint
│   │   ├── reporting/     # public/private writers, report.md, leak test
│   │   ├── security/      # network guard, PII-safe logging
│   │   ├── api/           # thin FastAPI routers
│   │   └── cli.py
│   ├── tests/             # synthetic-only (assistant runs these)
│   └── tests_local/       # real-data checks (user runs; aggregate output)
├── fixtures/synthetic/              # generated PDFs + exact ground truth (tracked)
├── sidecars/openredaction/          # Node adapter, pinned lockfile
├── sidecars/philter/                # compose service + filter policies
├── frontend/                        # Next.js (App Router, TS), telemetry off, no external assets
├── docker/                          # backend + Tesseract image, compose with internal network
├── data/                            # CONFIDENTIAL derived data: renders, extraction cache, vaults (git-ignored)
├── exports/                         # CONFIDENTIAL: pseudonymized outputs and re-identified results (git-ignored)
└── runs/                            # experiment outputs: public/ + private/ (git-ignored)
```
The confidential folders can live outside the repo: set `REDACTOR_DATA_ROOT` (§2.2).

## 6. What can be reused
- **In the repo:** nothing; it contains only the PDFs.
- **From this planning session:** the structural inspection logic (image-coverage grid, text render-mode trace, vector-path counts, metadata-presence checks). It will be ported to pypdfium2 in `ingest/page_classifier.py`.
- **Libraries (use rather than write):**
  - pypdfium2 (render, text and char boxes; Apache-2.0/BSD).
  - pikepdf (PDF metadata and XMP, deterministic PDF assembly).
  - Tesseract 5 CLI (TSV words with boxes and confidences).
  - RapidOCR or docTR as the second OCR engine (both Apache-2.0).
  - Pillow and numpy for rasterizing, deskew, contrast cleanup and box burn-in; OpenCV-headless (Apache-2.0) only if they are not enough.
  - RapidFuzz (Levenshtein and alignment).
  - pydantic v2, PyYAML, FastAPI/uvicorn, pytest + hypothesis.
  - `cryptography` for vault encryption, plus the standard library's `hmac`, `hashlib` and `sqlite3`.
  - reportlab + fontTools for synthetic fixtures, including vector-outlined pages via `ReportLabPen`.
  - Cohen's κ and B³ are small pure-Python functions (about 60 lines), so no ML dependency is needed.
- **Candidate systems:**
  - presidio-analyzer + spaCy `en_core_web_lg`.
  - The `openredaction` npm package: MIT licensed, regex-first. It has an optional hosted "AI assist" feature, which must stay disabled.
  - Philter/Phileas (Java/Docker; confirm licence and distribution at C1).
- **Deliberately not reused:**
  - **PyMuPDF** in product code: it is AGPL-3.0 unless the firm buys a licence. It is fine for throwaway diagnostics.
  - **Faker** at runtime: its output changes across versions.
  - **The Anaconda environment**: it is unpinned and has mixed dependencies. The project gets its own uv-managed Python 3.12 environment.
- **Tooling already on the machine:** uv, Docker, Node/pnpm and Java 17.

---

## 7. Deterministic baseline_detector

### 7.1 Baseline v0 (`baseline@0.1.0`): text only, the reference point
It uses no LLMs, GLiNER, NER models, generative models or external APIs. The estimate is about 1.2k lines of code plus tests.

| File | Purpose | ≈ LOC |
|---|---|---|
| `detectors/base.py` | Detector protocol and registry | 60 |
| `baseline/validators.py` | Checksum validators: TFN (mod 11), ABN (mod 89), ACN (mod-10 complement), Medicare (1-3-7-9 weights, first digit 2–6), provider number (check character), IHI (Luhn, 800360 prefix). The surrogate generator reuses them to force invalid values (§4.9.3) | 150 |
| `baseline/patterns.py` | Regex catalogue with IDs, priorities and required-context flags. Covers: AU phones (mobile, landline, +61, 13/1300/1800); email; URL; AHPRA (3 letters + 10 digits); BSB/account; dates ("16/10/2025", "16.10.2025", "16 October 2025", YYYYMMDD with range check); AGE ("aged 52", "52-year-old", "52 years"); AU addresses (unit/number + street + street-type lexicon; PO Box and Locked Bag; `SUBURB STATE 4-digit postcode` lines) | 280 |
| `baseline/context.py` | Keyword windows for context-only types: DOB, claim/ref/file numbers, court file numbers, passport, driver licence, policy number, Centrelink CRN, MRN/URN, vehicle registration. Also the `date_role` of each DATE ("date of injury/accident", "examined on", "report dated", "claim lodged") | 130 |
| `baseline/names.py` | Finds PERSON seeds, then propagates them across the matter (details below the table) | 200 |
| `baseline/orgs.py` | ORGANIZATION by suffix (Pty Ltd, Limited, Lawyers, Solicitors, Hospital, Clinic, Medical Centre, Physiotherapy, Radiology, Insurance …), plus a KEEP gazetteer (WorkCover Queensland, Medicare, Services Australia, courts) | 80 |
| `baseline/lexicons.py` | Closed lists for GENDER and NATIONALITY. NATIONALITY matches demonyms and "born in / migrated from `<country>`" patterns. An **exclusion list** of proper nouns that contain a demonym or country ("Australian Taxation Office", "Commonwealth of Australia", "Australian Capital Territory") prevents false redactions. LOCATION covers only state names and abbreviations plus address components | 100 |
| `baseline/resolve.py` | Overlap resolution: checksum-validated beats context rule, which beats pattern, which beats lexicon/propagation. Ties go to the longest, then earliest span, then type name | 80 |
| `baseline/detector.py` | Assembles, sorts and emits `EntitySpan`s; config fingerprint | 80 |
| `config/baseline.v0.1.yaml` | Keyword lists, stop-lists, enabled rules | — |

**How `names.py` works**
- **Seeds come from:**
  - an honorific followed by 1–3 capitalised tokens (ALL-CAPS included);
  - form labels such as Name, Surname, Given names, Claimant, Plaintiff, Patient, Worker and Re:;
  - signature blocks.
- **Propagation:** each seeded name and its components are then matched as whole words across the whole matter, subject to two limits:
  - surnames must be at least 3 characters;
  - first names must be capitalised and not on a stop-list.

**Testing and limits**
- **Tests** use published test vectors and synthetic documents only.
- **Known limitations, by design:**
  - names that have no seed context;
  - organisations without a suffix;
  - bare suburb names;
  - form label/value pairs that OCR reading order has split (addressed by v1, below).

### 7.2 Baseline v1 (`baseline@0.2.0`): v0 plus layout-aware key–value rules
**Why.** doc_002 is a 30-page scanned form. On forms, OCR reading order separates a label from its value (the value can come several lines later, or after the next label), so text-only rules miss it. The layout rules match a label to its value by **position on the page**, using word boxes instead of reading order.

**Kept separate from v0.** v0 stays the simplest reference. v1 is its own experiment, so the gain from layout is measured, not assumed.

**How it works**
1. **Lines and cells.** Words are grouped into lines by vertical overlap, and into cells by horizontal gaps larger than a configured multiple of the median character width. Sorting uses rounded coordinates, so it is deterministic.
2. **Labels.** A label lexicon (`config/forms/*.yaml`) maps label text ("Surname", "Given names", "Date of birth", "Address", "Telephone", "Email", "Medicare no.", "Claim number", "Employer", "Occupation", with variants and common OCR errors) to an entity type and a value rule.
3. **Value rules.** `right` (same line, within a distance), `below` (next line within a distance and left-aligned within a tolerance) or `inside_box` (same ruled box, where box lines are detected). The value is the run of words up to the next label or cell boundary.
4. **Validation.** Typed values pass the type validator (phone, email, date, identifier checksum). Free-text values (PERSON, ADDRESS, ORGANIZATION) take the label's type and a lower confidence.
5. **Output.** An `EntitySpan` with `rule_id="kv:<form>:<label>"` and boxes from the value words. A value that is not contiguous in the OCR text becomes one span per contiguous run, all sharing a `group_id`.
6. **Determinism.** Fixed tolerances from the config, stable sorts, no randomness.

| File | Purpose | ≈ LOC |
|---|---|---|
| `baseline/layout.py` | Word boxes to lines and cells; nearest-neighbour lookups (right, below, inside box) | 120 |
| `baseline/kv_rules.py` | Label matching, value extraction, validation, span emission | 130 |
| `config/forms/*.yaml` | Generic AU label lexicon plus per-form overrides (starting with the doc_002 form type) | — |

**Evaluation caveat.** doc_002 is the only form and is in the dev split, so the layout rules are tuned and scored on the same form. Results are labelled "dev, not held-out". Synthetic forms test the mechanism. A fair held-out estimate needs more forms (could_be_changed).

---

## 8. Incorporating the five PDFs without modifying them
Nothing in this section runs until the plan is approved.

1. **Register (quick; the first real operation).** `redactor dataset register golden_dataset_docs/` gives each PDF a safe ID (doc_001 …) and a SHA-256 hash, so anyone can tell later whether a file has changed.
   - It hashes each file, copies it to `golden_dataset/documents/doc_00N.pdf` (opaque IDs, because the filenames contain PII), re-hashes the copy (the hash must match), sets the copy read-only and writes the hashes to the manifest.
   - Original filenames are kept only in the confidential metadata.
   - Its output is aggregate only: counts, IDs and hash prefixes.
2. **Stable IDs.** IDs are allocated append-only in the manifest. The five current files are doc_001 to doc_005 (sorted-filename order, matching §1.2). A file added later gets the next free ID (doc_006 …). Existing IDs never change.
3. **Read-only access.** All code opens PDFs read-only. Derived artifacts (renders, OCR output, caches) go to `data/`, keyed by SHA-256.
4. **Checksum guard.** Every experiment runs a pre-check, and `tests_local/` repeats it, that the hashes of `golden_dataset_docs/` and `golden_dataset/documents/` are unchanged. Any mismatch is a hard failure.
5. **Matter.** All five documents are assumed to belong to one matter, `matter_001`. This needs confirming (§10 Q15).
6. **Split.**
   - **dev:** doc_002, doc_003, doc_005 (57 pages).
   - **test:** doc_001, doc_004 (31 pages). The test split is never used for rule tuning. It gets the silver pass like the rest, and a person verifies **every page** of it before any test score is trusted (§9.4). It never receives detector candidates (§9.5).

## 9. Annotating the golden_dataset_docs

### 9.1 Who annotates, and where
- **Claude Code writes the first pass (exception EX-001, §2.2).** It annotates every document in `golden_dataset_docs/` during the build, not during planning, once F1–F3 exist and schema v0.1 and guidelines v0.1 are written (§11, milestone S1). Its output is labelled **silver**.
- **People verify and own the gold.** Annotators A1 and A2 and an adjudicator work in the local annotation UI (milestone A1) on the firm's machine. They verify and correct the silver pass, and A1 and A2 annotate the IAA documents independently from scratch (§9.3). No score is trusted until the verification rule in §9.4 is met.
- **Optional local help.** Deterministic detectors (and later a pinned, offline local model) can pre-annotate. Their output is a *candidate*, never gold (§9.5).
- **What "highest accuracy and determinism" means here.** No gold standard is perfect. The plan raises quality with independent double annotation on part of the set, a recall audit against every local detector, automated validators and adjudication. It then *measures* the remaining noise (IAA, test–retest) and reports it as the ceiling for every detector score. For determinism, every artifact is reproducible from the same inputs (§9.4), except silver files: a model's output cannot be guaranteed to repeat, so silver files are frozen once written.

### 9.2 Pipeline
| Step | What happens | Output |
|---|---|---|
| P0 Register | Safe IDs, hashes, read-only copies (§8) | manifest |
| P1 Classify | Automatic page classes, then a person verifies all 88 pages | human-verified page classes |
| P2 Extract | Pinned renderer and OCR, single-threaded; frozen, cached text sources | text sources with word boxes |
| P3 Candidates | Deterministic detectors propose spans. Only for dev documents outside the IAA set | `candidates.json`, never gold |
| P3b Silver pass | Claude Code annotates every page, plus the filename and PDF metadata fields, of every document (EX-001, milestone S1), in document then page order, using the annotation schema. The P6 validators run on these files before any person sees them | `annotations_raw/claude_silver/` |
| P4 Verify | A1 verifies and corrects the silver pass: every page of the test split (doc_001, doc_004) and a seeded random sample of the other pages (§9.4). On the IAA documents A1 instead annotates from scratch, blind to silver (§9.3). A1 also builds the registry and confirms roles, canonical IDs and `date_role` | `annotations_raw/A1/` |
| P5 Pass 2 | A2 annotates the IAA documents independently (§9.3) | `annotations_raw/A2/` |
| P6 Validate | Automated validators; zero errors to proceed (§9.4) | validation report |
| P7 Recall audit | Every span a local detector predicts but the gold lacks is accepted or rejected, with a reason (§9.4) | `audit/` |
| P8 Adjudicate | A1 and A2 disagreements are resolved by the adjudicator, with reasons | `annotations/` |
| P9 Freeze | Canonical serialization, deterministic IDs, manifest hashes, version tag | `golden_dataset@v1.0` |

### 9.3 Second annotator and inter-annotator agreement
- **Coverage.** A2 independently annotates **at least 2 documents**. Default: **doc_003** (born-digital, 23 pages) and **doc_004** (scanned, 22 pages), 45 of 88 pages. That covers a text-layer document and an OCR document, one from dev and one from test. Adding doc_002 (the hardest form to anchor) is recommended if the budget allows.
- **Independence.** A1 and A2 work blind: no candidates, no access to each other's work, the same guidelines version (v0.2, after the pilot). Both annotate from scratch and never see the silver pass, so their agreement stays independent of it.
- **Silver as a third rater.** After adjudication, `claude_silver` is scored against the adjudicated gold on the same metrics. This shows how far silver can be trusted on pages nobody has verified (§9.4). Silver is not counted as a human annotator in the agreement figures.
- **Agreement metrics** (computed with the matching code of §4.5, first with A1 as "gold" and A2 as "prediction", then the other way round):
  - span F1: strict, exact-boundary and overlap-any, per type group;
  - token-level Cohen's κ for protect vs keep, and for type on overlap-matched pairs;
  - canonical-grouping agreement (B³ F1), plus gender and role agreement;
  - action agreement after the policy is applied;
  - region IoU of matched mentions (how noisy the coordinates are);
  - page-class agreement;
  - **test–retest:** A1 re-annotates 5 pages a week later, and the agreement with their own first pass is reported.
- **Targets** (initial; confirmed after the pilot): strict span F1 of at least 0.90 and κ of at least 0.85 on the protect types. If agreement is lower, stop, revise the guidelines and re-annotate a fresh sample (for example doc_005 plus 5 pages of doc_002) before continuing.
- **Reporting.** `iaa.public.json` holds metrics only. `iaa.private.json` holds the disagreement list, categorised as boundary, type, missed, extra, role or canonical.
- **Use.** Guideline revisions, the ceiling for detector scores (a detector within IAA noise of the gold cannot be ranked against another), and the adjudication worklist.
- A2 must be someone other than A1. A second pass by A1 alone measures consistency (test–retest) but not agreement.
