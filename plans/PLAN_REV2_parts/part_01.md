# PLAN_REV2 — Local PII Pseudonymization & Evaluation Platform

> Australian personal-injury medico-legal documents · plan **revision 2** · 2026-09-30 · supersedes `plans/PLAN_V0.md` (revision 1)
> Revision 2 adds the owner's decisions listed in §0.2. **Nothing has been built yet**: no code, no registration run, no git changes.

## 0. Context

The firm needs to pseudonymize confidential, mostly scanned Australian medico-legal PDFs entirely on its own infrastructure. Accuracy and determinism are the top priorities. No detector (Presidio, Philter/Phileas, OpenRedaction or our own) can be chosen until four things exist:

1. A manually verified **golden_dataset**.
2. An **OCR stage evaluated separately** from PII detection.
3. A simple deterministic **baseline_detector** to serve as the reference point.
4. A **detector-agnostic harness** that measures detection, privacy, preservation, consistency, determinism and OCR effects separately.

Production additionally needs a **fail-closed release gate** (§4.11) and a **structured output with local re-identification** (§4.12). The plan covers the whole system (FastAPI backend and Next.js frontend) but deliberately builds the **evaluation foundation first** (§11, F0–F5).

**Terminology (fixed):** golden_dataset · baseline_detector · candidate_detector · experiment · ground_truth · prediction. OpenRedaction, Presidio and Philter/Phileas are candidate_detectors; none of them is "the baseline".

### 0.1 Where each phase of the brief is covered

- Phase 1 (golden dataset) → §3, §8, §9
- Phase 2 (OCR evaluation) → §4.3, §4.6
- Phase 3 (deterministic baseline) → §7
- Phase 4 (normalized interface) → §4.2
- Phase 5 (candidate experiments) → §11 (C1–C2)
- Phase 6 (metrics) → §4.5
- Phase 7 (determinism) → §4.7
- Phase 8 (reporting) → §4.8
- Phase 9 (FastAPI + Next.js) → §4.10, §11 (A1, U1)
- Security / data sovereignty → §2
- Added in revision 2: release gate → §4.11; outputs and re-identification → §4.12



### 0.2 Decisions recorded in revision 2


| #   | Decision                                                                                                                                                      | Lands in           |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| D1  | DATE_OF_BIRTH gets an **age-preserving surrogate** (no longer KEEP). DATE, AGE and GENDER stay KEEP                                                           | §3.7, §4.9.2       |
| D2  | Identifiers get **checksum-invalid or reserved-range surrogates**; **TFN becomes a REDACT token**                                                             | §3.7, §4.9.3       |
| D3  | **NATIONALITY is REDACTed** (the brief allows it)                                                                                                             | §3.4, §3.7, §4.9.4 |
| D4  | A **second annotator** covers at least 2 documents so inter-annotator agreement (IAA) is measured                                                             | §4.5, §9.3         |
| D5  | **Layout-aware key–value rules** in baseline v1: a form label is matched to its value by position                                                             | §7.2               |
| D6  | The five PDFs now live in `golden_dataset_docs/` (renamed from `baseline_docs/`, same files). **All** of them are annotated                                   | §1.2, §8, §9       |
| D7  | **Register** (safe ID + hash) is the first real operation; it is quick                                                                                        | §8, §11 F2         |
| D8  | **Fail-closed release gate** with an OCR retry ladder and a REVIEW queue                                                                                      | §4.11              |
| D9  | **Primary output is structured pseudonymized JSON** (pages, spans, edit log), rendered to text or Markdown. **PDF output is rasterized with burned-in boxes** | §4.12              |
| D10 | **Local re-identification** of downstream results through the vault                                                                                           | §4.12.4            |
| D11 | Annotate every document with the **highest achievable accuracy and determinism**                                                                              | §9                 |
| D12 | **Claude Code does the first-pass annotation** at build time, under the recorded exception EX-001. Its output is labelled **silver** and is not trusted until a person has verified it | §2.2, §9 |


---



## 1. Findings



### 1.1 Repository and machine

- `redaction-tool-from-scratch/` holds only `golden_dataset_docs/` (the 5 client PDFs, renamed from `baseline_docs/`) and `plans/`. **No redaction, baseline or evaluation code exists yet**, so there is nothing in the repo to reuse.
- the project has its own repo with remote `origin` on GitHub. There is no `.gitignore` yet, so the PDFs could be pushed.
- **Installed:**
  - Python 3.13.2 (system; only PyPDF2, numpy, pillow and pydantic).
  - Anaconda Python 3.11 (PyMuPDF 1.28, pypdf 5.9, spaCy 3.8, FastAPI, RapidFuzz).
  - uv 0.10, Node 24 + pnpm 10, Java 17 (for Philter/Phileas) and Docker 29.
- **Missing:** Tesseract, poppler, Ghostscript and any local LLM runtime.



### 1.2 golden_dataset_docs — structural inspection only

I inspected the PDFs using only counts, geometry, text render modes, vector-path counts and checksums. **No page text, OCR output or page image entered the assistant's context** (see §2.2). The folder was called `baseline_docs/` at the first inspection; the hashes were re-checked after the rename, so the five files, their order and their IDs are unchanged.

The *filenames* contain practitioner names and a numeric identifier, so **filenames are treated as PII**.


| doc_id  | Type (from filename)                        | Pages | Producer                    | Composition                                                                                                                            | Text layer                     | OCR                 |
| ------- | ------------------------------------------- | ----- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------- |
| doc_001 | Degree of Permanent Impairment (DPI) report | 9     | Microsoft Print to PDF      | **Vector-outlined glyphs**: 500–2,560 small filled paths/page, no fonts, no text. Raster regions on p1 (12 %) and p9 (1 %) at ~600 dpi | absent                         | yes (render → OCR)  |
| doc_002 | Notice of Claim for Damages (form)          | 30    | Konica Minolta bizhub C250i | Scan with **multi-layer (MRC-style) compression**: 3–72 image tiles/page at 150 + 300 dpi, full coverage                               | absent                         | yes                 |
| doc_003 | Psychiatrist medico-legal report            | 23    | Microsoft Word 365          | Born-digital (Arial). Raster regions on p1 (19 %, hybrid candidate), p18 and p23                                                       | usable (alnum ratio 0.94–0.98) | raster regions only |
| doc_004 | Independent medico-legal report             | 22    | "Button Manager V2" scanner | 1 image/page at 200 dpi; auto-cropped page sizes (591–600 × 838–846 pt)                                                                | absent                         | yes                 |
| doc_005 | Plaintiff training transcript               | 4     | wkhtmltopdf 0.12.6.1        | Born-digital; small logos on p1                                                                                                        | usable                         | no                  |


SHA-256 prefixes (used by the register check in §8): doc_001 `054a4f09729a` · doc_002 `065305416f6f` · doc_003 `349a3cba361d` · doc_004 `c35b94897308` · doc_005 `321d47ed9379`.

**Totals:** 88 pages. **61 need OCR (69 %: 52 scanned + 9 vector-outlined); 27 have a usable text layer.**

- No document has an existing invisible OCR layer, form fields, PDF annotations, embedded files or bookmarks.
- PDF metadata fields that may hold PII (Author, Title, Keywords, XMP) are present in doc_001, doc_002, doc_003 and doc_005.

**Design consequences**

1. The page classifier needs a `vector_outlined` class. A page with no text and no images is not necessarily blank.
2. OCR always runs on the **rendered page**, never on extracted images, because MRC tiles are only fragments of the page.
3. Born-digital pages can carry PII inside raster regions (letterhead, signature). A `hybrid` route OCRs those regions.
4. Filenames and PDF metadata are part of the leakage surface.
5. I could not check for handwriting or signatures, by design. The annotation pass must flag them.
6. The corpus has only one hybrid candidate, no scanner OCR layers and **one form** (doc_002). Coverage of those classes is thin (see could_be_changed).

---



## 2. Hard constraints



### 2.1 Data sovereignty

Document binaries, extracted text, OCR output, annotations, mappings, vaults and PII never leave the firm's controlled environment. That rules out cloud OCR, cloud LLMs, embeddings, vector DBs, analytics and telemetry. The system itself never sends anything out.

There is one recorded exception, for building the dataset only: the silver first-pass annotation by Claude Code (EX-001, §2.2). Whether pseudonymized output may be given to an outside tool afterwards is the firm's decision (§10, Q20).

### 2.2 Assistant data boundary and the silver-annotation exception

Claude Code is itself a cloud API: whatever it reads or writes is processed by the model provider. By default the development assistant therefore never sees document content. The owner has decided on one scoped, recorded exception (EX-001, below). Otherwise the boundary is enforced in five ways:

- **Default: the assistant does not read, OCR or annotate the documents**, during the build or at any other time, except under EX-001 (S1) and the owner-authorised real-file runs below. Any other exception has to be explicit and recorded, because document content would then reach the model provider, which the brief forbids.
- **Deny rules.** `.claude/settings.json` blocks the file-reading tools on `golden_dataset_docs/**`, `golden_dataset/**`, `data/**`, `exports/**` and `runs/**/private/**`.
  - The rules on `golden_dataset_docs/**` and on the renders and OCR text in `data/**` stay in force until S1 starts. They are lifted only for the silver pass (EX-001), which may write only under `golden_dataset/annotations_raw/claude_silver/`, and are then restored.
  - These are guard-rails, not a sandbox: a shell command can still read a file.
  - The stronger option is to keep the confidential folders **outside the assistant's workspace** (for example `D:\confidential\…`, referenced through `REDACTOR_DATA_ROOT`) and mount them only into the processing containers.
- **Aggregate-only output.**
  - Every CLI prints only counts, rates, hashes and IDs. Printing text needs an explicit `--show-text` flag, which the assistant never passes.
  - A PII-safe log formatter keeps entity text and exception payloads out of logs.
- **Split outputs.**
  - `runs/<id>/public/` contains metrics only and is safe to share with the assistant.
  - `runs/<id>/private/` contains spans with their text and stays local.
  - An automated **leak test** scans every public artifact for gold surface strings and fails the run on any hit.
- **Synthetic-only development.**
  - The assistant builds and unit-tests against synthetic fixtures. The real-document work is the silver pass (EX-001) and the owner-authorised real-file runs (register, extraction, OCR, the smoke run), which print aggregates only.
  - Tests that touch real documents live in `tests_local/`. By the owner's standing authorisation of 2026-10-01 the assistant may run them and the real-file commands, with aggregate-only output.
  - Debug loop: the user describes error *patterns* (not content), and the assistant reproduces them as synthetic fixtures.



**EX-001: silver first-pass annotation by Claude Code** (build-time work; nothing is run while planning)
| Field | Value |
|---|---|
| Decision | Claude Code annotates every document in `golden_dataset_docs/` as a first pass (§9). The owner decided this; it is recorded in the plan on 2026-10-01 |
| When | During the build, in milestone S1 (§11), after F1–F3 exist and Claude Code has written schema v0.1 and guidelines v0.1. Not during F0–F3 |
| Sign-off | Authorised by the project owner on 2026-10-01 and recorded in `docs/data_exceptions.md`. If the firm requires another signatory, the name and date are added there before the pass starts |
| Data sent to the model provider | Page images and extracted text (words with boxes) of the documents, their filenames and PDF metadata values, and the annotations Claude writes |
| Model | One model for the whole pass, chosen by the owner before it starts. Its ID is recorded in every silver file |
| Output | `golden_dataset/annotations_raw/claude_silver/`, labelled **silver** (`annotator: claude_silver`) |
| Trust rule | No score is trusted until a person has verified the whole test split (doc_001 and doc_004) and a seeded random sample of the other pages (§9.4). Silver is never promoted to gold without that verification |
| Access rule | The deny rules on `golden_dataset_docs/**` and on the page renders and OCR text in `data/**` are lifted for this task only. Claude Code may write only under `golden_dataset/annotations_raw/claude_silver/`. All rules are restored when the silver pass ends |
| Scope | These five documents, annotation only. New documents need their own recorded exception |

The record lives in `docs/data_exceptions.md` (tracked, no PII) and is referenced from the dataset manifest and from every silver file.

### 2.3 Git hygiene
- The project keeps its existing repository and its GitHub remote. The `.gitignore`, a pre-commit hook and a **pre-push hook** that reject confidential paths and PDFs are the protection.
- `.gitignore` covers every confidential path (`golden_dataset_docs/`, `golden_dataset/`, `data/`, `runs/`, `exports/`).
- A pre-commit hook rejects `*.pdf`, image files and confidential paths. Synthetic fixtures are whitelisted.
- `golden_dataset/` is versioned in a **separate local-only repo** whose pre-push hook always fails.

### 2.4 Network isolation

- **Services:**
  - Everything binds to `127.0.0.1`.
  - Docker Compose uses an `internal: true` network for the processing sidecars (Philter, OpenRedaction, the second OCR engine).
  - Batch runs use `network_mode: none`.
- **In-process guard:** a network guard blocks non-loopback `socket.connect`, and a test proves it.
- **Offline flags:** `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `NEXT_TELEMETRY_DISABLED=1`, `DO_NOT_TRACK=1`.
- **No CDN assets:** FastAPI's Swagger UI loads from jsDelivr by default, so it is disabled or self-hosted. Fonts are self-hosted.
- **Pinned downloads:** models, tessdata and npm/pip packages are fetched only at build time, pinned by SHA-256 or lockfile.

---



## 3. Golden dataset schema (v0.1 draft, frozen as v0.2 after the pilot)



### 3.1 Principles

1. **Facts, not policy.** Annotations record facts: type, role, attributes, text and location. The `action` is *derived* from a versioned policy (§3.7). A manual `override` is allowed but needs a reason. Changing the policy never forces re-annotation.
2. **Dual anchoring.**
  - `regions` (page coordinates) are the primary anchor and survive changes of OCR engine.
  - `text_anchor` offsets are exact but tied to one named text source.
3. **Coordinates.**
  - Everything uses displayed page space (after `/Rotate`), in PDF points, with the origin at top-left.
  - A single transform module converts pdfium user space and OCR pixels into this space. Round-trip tests cover it.
4. **Versioning.**
  - Every file carries `schema` and `schema_version` (semver).
  - The pydantic models are the source of truth, and JSON Schemas are generated from them into `schemas/`.
  - Migrations are explicit functions.
5. **Explicit coverage.** Only pages and fields marked complete are evaluated, so a partial annotation never inflates false positives.
6. **Deterministic files.** Gold files are canonical JSON (sorted keys, UTF-8, fixed float rounding) with no wall-clock times or machine-specific values. Entity IDs are assigned at freeze time by a fixed sort, so the same annotations always produce the same bytes (§9.4).
7. **Independent passes are kept.** Each annotator's submitted pass is stored unedited under `annotations_raw/`. The evaluation reads only the adjudicated `annotations/`.



### 3.2 Files

```
golden_dataset/
  manifest.json                          dataset version, doc ids, sha256, matter_id, split, hash of every file below
  documents/doc_001.pdf …                byte-identical, read-only copies (safe IDs, no PII in names)
  metadata/doc_001.meta.json             document + page metadata (incl. original filename)
  annotations_raw/A1/doc_003.ann.json    one file per annotator pass; never edited after submission
  annotations_raw/A2/doc_003.ann.json    (A2 exists only for the IAA documents, §9.3)
  annotations_raw/claude_silver/doc_001.ann.json   silver first pass by Claude Code (EX-001); never gold
  annotations/doc_001.ann.json           adjudicated gold: what the evaluation reads
  annotations/doc_001.candidates.json    optional pre-annotation (never gold)
  audit/doc_001.audit.json               recall-audit and adjudication decisions, with reasons (§9.4)
  iaa/iaa.public.json, iaa.private.json  agreement metrics (public) and the disagreement list with text (private)
  registry/matter_001.entities.json      canonical entities (person_001 …)
  transcripts/doc_002/p0005.json         gold page transcripts (OCR-evaluation subset)
```

`golden_dataset_docs/` holds the untouched client originals. `golden_dataset/` is the derived, versioned ground truth built from them.

### 3.3 Document metadata — `golden.document_metadata`

```json
{
  "schema": "golden.document_metadata", "schema_version": "0.1.0",
  "document_id": "doc_002", "matter_id": "matter_001", "split": "dev",
  "original_filename": "<confidential>", "source_path": "golden_dataset_docs/<confidential>",
  "sha256": "065305416f6f…", "size_bytes": 3408844,
  "document_type": "claim_form/notice_of_claim_for_damages",
  "pdf": { "version": "1.4", "producer": "KONICA MINOLTA bizhub C250i", "encrypted": false,
           "metadata_fields_present": ["title", "xmp"], "annotations": 0, "form_fields": 0, "embedded_files": 0 },
  "page_count": 30,
  "pages": [{
    "page": 1, "width_pt": 595.0, "height_pt": 842.0, "rotation": 0,
    "signals": { "visible_text_chars": 0, "invisible_text_chars": 0, "image_count": 36,
                 "image_coverage": 1.0, "image_dpi": [150, 300], "vector_paths": 0, "alnum_ratio": null },
    "classification": { "content_kind": "scanned", "text_layer": "absent", "ocr_required": true,
                        "ocr_scope": "full_page", "classifier": "page_classifier@0.1.0", "human_verified": false },
    "extraction": { "method": "ocr", "text_source_id": "ocr.tesseract@5.5.0+best-eng#c1a2b",
                    "engine": "tesseract", "engine_version": "5.5.0", "model_sha256": "…",
                    "render": { "engine": "pdfium", "version": "…", "dpi": 300, "mode": "gray" } }
  }]
}
```

**Classification vocabulary**

- `content_kind`: `born_digital | scanned | vector_outlined | hybrid | blank | unknown`.
- `text_layer`: `usable | present_unusable | absent`, plus `invisible_ocr_layer: bool`.
- `ocr_scope`: `none | raster_regions | full_page`.

**Classification rules**

- Thresholds live in `config/extraction/page_classifier.yaml` and are recorded in every run.
- `unknown` always goes to human review (and, in production, straight to the release gate's REVIEW queue, §4.11).
- "Usable" means all of the following:
  - at least 50 visible characters;
  - an alphanumeric ratio of at least 0.6;
  - at most 1 % replacement or private-use characters;
  - optionally, a spot-check OCR of a crop agrees with the text layer.



### 3.4 Annotation set — `golden.annotation_set`

```json
{
  "schema": "golden.annotation_set", "schema_version": "0.1.0",
  "document_id": "doc_002", "document_sha256": "065305416f6f…",
  "annotator": "A1", "pass": 1, "mode": "blind",
  "revision": 7, "guidelines_version": "0.1.0", "policy_ref": "default-au-pi@0.1.0",
  "coverage": { "pages_complete": [1, 2, 3], "fields_complete": ["filename", "pdf.title"],
                "types_complete": ["DATE", "DATE_OF_BIRTH", "AGE", "GENDER", "NATIONALITY"] },
  "entities": [{
    "entity_id": "doc_002.e0042",
    "canonical_id": "matter_001/person_001",
    "entity_type": "PERSON",
    "text": "<gold surface text>",
    "page": 3, "field": null,
    "regions": [{ "x0": 212.4, "y0": 181.0, "x1": 301.7, "y1": 193.5 }],
    "text_anchor": { "text_source_id": "ocr.tesseract@5.5.0+best-eng#c1a2b", "start": 518, "end": 529 },
    "role": "plaintiff",
    "attributes": { "name_form": "title_surname", "casing": "title" },
    "action": "SYNTHETIC", "action_source": "policy", "override_reason": null,
    "flags": ["split_line"],
    "certainty": "certain",
    "notes": "",
    "provenance": { "annotator": "A1", "pass": 1, "origin": "manual" }
  }],
  "ignore_regions": [{ "page": 12, "regions": [{ "x0": 40, "y0": 700, "x1": 200, "y1": 760 }], "reason": "illegible stamp" }]
}
```

**Field vocabularies**

- `annotator`: `A1 | A2 | claude_silver | adjudicated`. `mode`: `blind | candidates_shown | silver | adjudicated`. Silver files also carry `silver: { model_id, run_date, guidelines_version, prompt_sha256, input_sha256s }` (EX-001).
- `field` is used for document-level text, with `page: null`. Values: `filename | pdf.title | pdf.author | pdf.subject | pdf.keywords | xmp`.
- `flags`: `handwritten`, `ocr_degraded`, `split_line`, `split_page`, `partially_illegible`, `quasi_identifier`.
- `certainty`: `certain | probable | uncertain`. A mention that is uncertain, or whose action is REVIEW, is excluded from P/R and reported separately.
- `provenance.origin`: `manual | preannotation_accepted | preannotation_edited | audit_added | silver`. This lets us measure pre-annotation bias.
- Adjudicated gold adds `provenance.adjudication`: `agreed | took_A1 | took_A2 | verified_silver | merged | added`. `verified_silver` needs `verified_by` and a date (§9.4).
- Region-only types (SIGNATURE, PHOTO) have `text: null` and no `text_anchor`.

**Type-specific attributes** (`attributes`)

- PERSON: `name_form` (full, given_only, surname_only, title_surname, initial_surname, surname_comma_given, …) and `casing`.
- DATE: `date_role` ∈ {date_of_injury, examination, report, claim, treatment, other}. It feeds the age window of DOB surrogates (§4.9.2).
- DATE_OF_BIRTH: `granularity` ∈ {full, month_year, year}. A canonical person is required.
- AGE: `stated_age` (integer), with an optional link to the DATE it refers to.
- NATIONALITY: `form` ∈ {demonym, citizenship, country_of_origin}.
- LOCATION: `granularity` (see §3.7).
- PHONE: `number_class` ∈ {mobile, landline, 13, 1300, 1800, other}.
- Identifier types: `checksum` ∈ {valid, invalid, not_applicable}. The validator fills it, not the annotator. `invalid` raises a verification flag (an OCR misread, or a number that really is invalid).

**Span conventions** (these go in `docs/annotation_guidelines.md`)

- PERSON spans exclude honorifics and post-nominals, because those carry gender and are kept.
- Spans exclude a trailing possessive "'s".
- Annotate the maximal unit, and never overlap gold spans:
  - An email containing a name is an EMAIL with `attributes.contains_person`.
  - "Smith Lawyers" is an ORGANIZATION with `contains_person`.
- A multi-line entity gets one region per line.
- An entity that crosses a page break is split into two mentions. Both share the same `canonical_id` and carry the `split_page` flag.
- **NATIONALITY** is annotated only when it describes a person: a demonym, a citizenship, "born in X", "migrated from X".
  - A country or demonym inside a proper noun ("Australian Taxation Office", "Commonwealth of Australia") belongs to that ORGANIZATION or LOCATION. It is never a NATIONALITY.
  - A bare country name that states a person's origin is a NATIONALITY (`form=country_of_origin`), not a LOCATION.
  - Ethnicity, Indigenous status, religion and language are not NATIONALITY. They get the `quasi_identifier` flag (out of scope for v0, §10 Q12).
- **DATE_OF_BIRTH** is annotated for every form of the date of birth ("16/10/1970", "born 16 October 1970", "born in October 1970", "D.O.B."). A year alone ("born in 1970") is annotated with `granularity=year`; it is kept because the surrogate keeps the year.



### 3.5 Canonical registry — `golden.canonical_registry` (one per matter)

```json
{
  "schema": "golden.canonical_registry", "schema_version": "0.1.0", "matter_id": "matter_001",
  "entities": [
    { "canonical_id": "matter_001/person_001", "entity_type": "PERSON", "role": "plaintiff",
      "gender": "female", "gender_evidence": "honorific", "date_of_birth": "<ISO date, confidential>", "label": "<local display name>" },
    { "canonical_id": "matter_001/org_003", "entity_type": "ORGANIZATION", "role": "employer", "label": "…" }
  ]
}
```

- Aliases such as "John Smith", "John", "Mr Smith", "Mr. Smith", "SMITH, John" and "J. Smith" are simply mentions that point to the same `canonical_id`. The alias set is derived, not stored.
- `gender` takes one of: `female | male | non_binary | unknown`. It is the ground truth for the gender-preservation metrics.
- `date_of_birth` (persons only, when known) is the ground truth for the age-preservation metric (§4.5).



### 3.6 Page transcript — `golden.page_transcript` (OCR subset only)

```json
{ "schema": "golden.page_transcript", "schema_version": "0.1.0", "document_id": "doc_002", "page": 5,
  "lines": [{ "text": "…", "bbox": { "x0": 0, "y0": 0, "x1": 0, "y1": 0 } }],
  "reading_order": "top_down_left_right", "seeded_from": "ocr.tesseract@5.5.0+best-eng#c1a2b", "verified_by": "A1" }
```



### 3.7 Taxonomy and policy (configuration, not code)

`config/taxonomy.v0.1.yaml` maps each type to a group and records `is_pii`, `critical` and its `validator`. `config/policy.v0.1.yaml` maps `(type, role, attributes)` to an action and a replacement strategy; the first matching rule wins.

In the table, types marked † are proposed additions to the list in the brief.


| Group (report bucket)          | Types                                                                                                                                                    | Default action (policy v0.1)                                                              | Baseline method                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| person                         | PERSON                                                                                                                                                   | SYNTHETIC                                                                                 | seeds + propagation (§7.1)                           |
| organization                   | ORGANIZATION                                                                                                                                             | by role (§10)                                                                             | suffix patterns + KEEP gazetteer                     |
| contact                        | ADDRESS, EMAIL, PHONE, URL†                                                                                                                              | SYNTHETIC (phones and emails use reserved ranges, §4.9.3)                                 | regex + AU address grammar                           |
| location                       | LOCATION (`granularity` attribute)                                                                                                                       | country/state KEEP; finer levels per §10                                                  | address-derived + state names                        |
| legal identifiers              | CLAIM_NUMBER, COURT_FILE_NUMBER†, POLICY_NUMBER†, ABN, ACN, PASSPORT, DRIVER_LICENCE, VEHICLE_REGISTRATION†, CENTRELINK_CRN†, ACCOUNT_NUMBER (incl. BSB) | SYNTHETIC: checksum-invalid where a checksum exists, otherwise format-preserving (§4.9.3) | checksums (ABN, ACN) + context keywords              |
| legal identifiers              | **TFN**                                                                                                                                                  | **REDACT** → `[TFN]` (one-way)                                                            | checksum (mod 11) + context                          |
| medical / provider identifiers | MEDICARE, PROVIDER_NUMBER, AHPRA_REGISTRATION†, IHI†, MEDICAL_RECORD_NUMBER†, DVA_NUMBER†                                                                | SYNTHETIC: checksum-invalid where a checksum exists, otherwise format-preserving          | checksums (Medicare, provider number, IHI) + context |
| temporal                       | DATE, AGE†                                                                                                                                               | KEEP                                                                                      | date grammar, age patterns, `date_role` from context |
| temporal                       | **DATE_OF_BIRTH**                                                                                                                                        | **SYNTHETIC, age-preserving** (§4.9.2); fallback REDACT                                   | DOB from context (keyword window)                    |
| demographic                    | GENDER                                                                                                                                                   | KEEP                                                                                      | closed lexicon                                       |
| demographic                    | **NATIONALITY**                                                                                                                                          | **REDACT** → `[NATIONALITY]` (one-way)                                                    | closed lexicon + exclusions (§7.1)                   |
| visual (region-only)           | SIGNATURE†, PHOTO†                                                                                                                                       | REDACT (PDF output only)                                                                  | not detected in v0; annotated only                   |


**Action semantics**

- `KEEP`: the text is unchanged.
- `SYNTHETIC`: the text is replaced by a surrogate. The mapping lives in the matter's vault, so the replacement is deterministic and can be reversed locally (§4.12.4).
- `REDACT`: the text is replaced by a constant typed token (`[TFN]`, `[NATIONALITY]`, `[DATE_OF_BIRTH]`, `[SIGNATURE]`). It is **one-way**: the vault keeps only a keyed hash, so the value can be leak-scanned but never restored.
- `REVIEW`: the system cannot decide, so the span goes to the human queue (§4.11).

**Role vocabularies**

- PERSON roles: `plaintiff, plaintiff_family, witness, co_worker, treating_practitioner, examining_expert, legal_representative, insurer_representative, employer_representative, court_officer, other`.
- ORGANIZATION roles: `employer, insurer, statutory_body, court_tribunal, government, law_firm, hospital, medical_practice, education, other`.
- LOCATION `granularity`: `country, state, city, suburb, street, facility, incident_site`.

```yaml
policy_id: default-au-pi
version: 0.1.0
defaults:
  PERSON:        {action: SYNTHETIC, strategy: name_components}
  ADDRESS:       {action: SYNTHETIC, strategy: address_same_state}
  EMAIL:         {action: SYNTHETIC, strategy: reserved_domain}
  PHONE:         {action: SYNTHETIC, strategy: reserved_range}
  DATE_OF_BIRTH: {action: SYNTHETIC, strategy: dob_age_preserving, min_window_days: 14, fallback: REDACT}
  TFN:           {action: REDACT, token: "[TFN]"}
  NATIONALITY:   {action: REDACT, token: "[NATIONALITY]"}
  SIGNATURE:     {action: REDACT, token: "[SIGNATURE]"}
  DATE:          {action: KEEP}
  AGE:           {action: KEEP}
  GENDER:        {action: KEEP}
  ORGANIZATION:  {action: REVIEW}
  LOCATION:      {action: REVIEW}
identifier_strategy:                   # every other identifier type
  checksummed: checksum_invalid        # MEDICARE, ABN, ACN, PROVIDER_NUMBER, IHI
  other: format_preserving
vault_mode: {SYNTHETIC: reversible, REDACT: hash_only}
rules:
  - {when: {entity_type: ORGANIZATION, role: [statutory_body, court_tribunal, government]}, action: KEEP}
  - {when: {entity_type: ORGANIZATION, role: [employer, medical_practice, hospital, law_firm]}, action: SYNTHETIC, strategy: org_name}
  - {when: {entity_type: LOCATION, attributes: {granularity: [country, state]}}, action: KEEP}
  - {when: {entity_type: PHONE, attributes: {number_class: ["13", "1300", "1800"]}}, action: KEEP}   # proposed default, §10 Q8
critical:
  - {entity_type: PERSON, role: [plaintiff, plaintiff_family]}
  - {entity_type: [DATE_OF_BIRTH, ADDRESS, PHONE, EMAIL, MEDICARE, TFN, PASSPORT, DRIVER_LICENCE, ACCOUNT_NUMBER, CLAIM_NUMBER]}
```

---



## 4. Evaluation architecture

