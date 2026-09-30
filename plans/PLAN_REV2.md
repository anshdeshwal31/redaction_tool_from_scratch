# PLAN_V1 — Local PII Pseudonymization & Evaluation Platform

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
| # | Decision | Lands in |
|---|---|---|
| D1 | DATE_OF_BIRTH gets an **age-preserving surrogate** (no longer KEEP). DATE, AGE and GENDER stay KEEP | §3.7, §4.9.2 |
| D2 | Identifiers get **checksum-invalid or reserved-range surrogates**; **TFN becomes a REDACT token** | §3.7, §4.9.3 |
| D3 | **NATIONALITY is REDACTed** (the brief allows it) | §3.4, §3.7, §4.9.4 |
| D4 | A **second annotator** covers at least 2 documents so inter-annotator agreement (IAA) is measured | §4.5, §9.3 |
| D5 | **Layout-aware key–value rules** in baseline v1: a form label is matched to its value by position | §7.2 |
| D6 | The five PDFs now live in **`golden_dataset_docs/`** (renamed from `baseline_docs/`, same files). **All** of them are annotated | §1.2, §8, §9 |
| D7 | **Register** (safe ID + hash) is the first real operation; it is quick | §8, §11 F2 |
| D8 | **Fail-closed release gate** with an OCR retry ladder and a REVIEW queue | §4.11 |
| D9 | **Primary output is structured pseudonymized JSON** (pages, spans, edit log), rendered to text or Markdown. **PDF output is rasterized with burned-in boxes** | §4.12 |
| D10 | **Local re-identification** of downstream results through the vault | §4.12.4 |
| D11 | Annotate every document with the **highest achievable accuracy and determinism** | §9 |

---

## 1. Findings

### 1.1 Repository and machine
- `redaction-tool-from-scratch/` holds only `golden_dataset_docs/` (the 5 client PDFs, renamed from `baseline_docs/`) and `plans/`. **No redaction, baseline or evaluation code exists yet**, so there is nothing in the repo to reuse.
- ⚠ **The folder sits inside a git repo rooted at `Desktop/`, and its `origin` is an unrelated GitHub project** (`link-validator-google-sheet-add-on-webUI`). `git status` already lists the folder as untracked, so one careless `git add . && git push` could publish the client PDFs. This is fixed first, in F0.
- **Installed:**
  - Python 3.13.2 (system; only PyPDF2, numpy, pillow and pydantic).
  - Anaconda Python 3.11 (PyMuPDF 1.28, pypdf 5.9, spaCy 3.8, FastAPI, RapidFuzz).
  - uv 0.10, Node 24 + pnpm 10, Java 17 (for Philter/Phileas) and Docker 29.
- **Missing:** Tesseract, poppler, Ghostscript and any local LLM runtime.

### 1.2 golden_dataset_docs — structural inspection only
I inspected the PDFs using only counts, geometry, text render modes, vector-path counts and checksums. **No page text, OCR output or page image entered the assistant's context** (see §2.2). The folder was called `baseline_docs/` at the first inspection; the hashes were re-checked after the rename, so the five files, their order and their IDs are unchanged.

The *filenames* contain practitioner names and a numeric identifier, so **filenames are treated as PII**.

| doc_id | Type (from filename) | Pages | Producer | Composition | Text layer | OCR |
|---|---|---|---|---|---|---|
| doc_001 | Degree of Permanent Impairment (DPI) report | 9 | Microsoft Print to PDF | **Vector-outlined glyphs**: 500–2,560 small filled paths/page, no fonts, no text. Raster regions on p1 (12 %) and p9 (1 %) at ~600 dpi | absent | yes (render → OCR) |
| doc_002 | Notice of Claim for Damages (form) | 30 | Konica Minolta bizhub C250i | Scan with **multi-layer (MRC-style) compression**: 3–72 image tiles/page at 150 + 300 dpi, full coverage | absent | yes |
| doc_003 | Psychiatrist medico-legal report | 23 | Microsoft Word 365 | Born-digital (Arial). Raster regions on p1 (19 %, hybrid candidate), p18 and p23 | usable (alnum ratio 0.94–0.98) | raster regions only |
| doc_004 | Independent medico-legal report | 22 | "Button Manager V2" scanner | 1 image/page at 200 dpi; auto-cropped page sizes (591–600 × 838–846 pt) | absent | yes |
| doc_005 | Plaintiff training transcript | 4 | wkhtmltopdf 0.12.6.1 | Born-digital; small logos on p1 | usable | no |

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
Document binaries, extracted text, OCR output, annotations, mappings, vaults and PII never leave the firm's controlled environment. That rules out cloud OCR, cloud LLMs, embeddings, vector DBs, analytics and telemetry. The system itself never sends anything out. Whether pseudonymized output may be given to an outside tool afterwards is the firm's decision (§10, Q20).

### 2.2 Assistant data boundary
Claude Code is itself a cloud API, so the same rule applies to the development assistant. It is enforced in five ways:

- **The assistant does not read, OCR or annotate the documents.** Annotation (§9) is done locally by people the firm authorises, with local tools. Any exception has to be explicit and recorded, because document content would then reach the model provider, which the brief forbids.
- **Deny rules.** `.claude/settings.json` blocks the file-reading tools on `golden_dataset_docs/**`, `golden_dataset/**`, `data/**`, `exports/**` and `runs/**/private/**`.
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
  - The assistant builds and tests only against synthetic fixtures.
  - Tests that touch real documents live in `tests_local/`, and the user runs them.
  - Debug loop: the user describes error *patterns* (not content), and the assistant reproduces them as synthetic fixtures.

### 2.3 Git hygiene
- The project gets its own repository: a nested `git init` with no remote until the firm chooses one.
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
- `annotator`: `A1 | A2 | adjudicated`. `mode`: `blind | candidates_shown | adjudicated`.
- `field` is used for document-level text, with `page: null`. Values: `filename | pdf.title | pdf.author | pdf.subject | pdf.keywords | xmp`.
- `flags`: `handwritten`, `ocr_degraded`, `split_line`, `split_page`, `partially_illegible`, `quasi_identifier`.
- `certainty`: `certain | probable | uncertain`. A mention that is uncertain, or whose action is REVIEW, is excluded from P/R and reported separately.
- `provenance.origin`: `manual | preannotation_accepted | preannotation_edited | audit_added`. This lets us measure pre-annotation bias.
- Adjudicated gold adds `provenance.adjudication`: `agreed | took_A1 | took_A2 | merged | added`.
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

| Group (report bucket) | Types | Default action (policy v0.1) | Baseline method |
|---|---|---|---|
| person | PERSON | SYNTHETIC | seeds + propagation (§7.1) |
| organization | ORGANIZATION | by role (§10) | suffix patterns + KEEP gazetteer |
| contact | ADDRESS, EMAIL, PHONE, URL† | SYNTHETIC (phones and emails use reserved ranges, §4.9.3) | regex + AU address grammar |
| location | LOCATION (`granularity` attribute) | country/state KEEP; finer levels per §10 | address-derived + state names |
| legal identifiers | CLAIM_NUMBER, COURT_FILE_NUMBER†, POLICY_NUMBER†, ABN, ACN, PASSPORT, DRIVER_LICENCE, VEHICLE_REGISTRATION†, CENTRELINK_CRN†, ACCOUNT_NUMBER (incl. BSB) | SYNTHETIC: checksum-invalid where a checksum exists, otherwise format-preserving (§4.9.3) | checksums (ABN, ACN) + context keywords |
| legal identifiers | **TFN** | **REDACT** → `[TFN]` (one-way) | checksum (mod 11) + context |
| medical / provider identifiers | MEDICARE, PROVIDER_NUMBER, AHPRA_REGISTRATION†, IHI†, MEDICAL_RECORD_NUMBER†, DVA_NUMBER† | SYNTHETIC: checksum-invalid where a checksum exists, otherwise format-preserving | checksums (Medicare, provider number, IHI) + context |
| temporal | DATE, AGE† | KEEP | date grammar, age patterns, `date_role` from context |
| temporal | **DATE_OF_BIRTH** | **SYNTHETIC, age-preserving** (§4.9.2); fallback REDACT | DOB from context (keyword window) |
| demographic | GENDER | KEEP | closed lexicon |
| demographic | **NATIONALITY** | **REDACT** → `[NATIONALITY]` (one-way) | closed lexicon + exclusions (§7.1) |
| visual (region-only) | SIGNATURE†, PHOTO† | REDACT (PDF output only) | not detected in v0; annotated only |

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

### 4.1 Stages and artifacts
```
PDF ─► S0 Ingest ──────► DocumentMetadata (fingerprint, per-page classes)
         ▼
       S1 Extraction ──► PageExtraction per (page, text source): words + boxes + text
         │                 sources: text_layer | ocr.<engine> | hybrid | gold_transcript (oracle)
         │                 cached by (pdf sha256, page, extractor fingerprint)
         ▼
       S2 Detection ───► EntitySpan[] per detector; Combiner for multi-detector experiments
         ▼
       S3 Linking ─────► canonical groups (rule-based; held constant across detector experiments)
         ▼                      S0–S3 run over every document of the matter first (§4.9.5)
       S4 Policy ──────► action per span: KEEP | SYNTHETIC | REDACT | REVIEW
         ▼
       S5 Replacement ─► pseudonymized text + edit log + per-matter vault
         ▼
       S6 Release gate ► PASS | REVIEW | BLOCKED (retry ladder, review queue, final leak scan)      §4.11
         ▼
       S7 Export ──────► structured JSON (primary) → text / Markdown; PDF (rasterized, boxes)     §4.12
         ▼
       S8 Re-identify ─► restore real values in downstream results, locally, via the vault        §4.12.4

Evaluators read each stage's frozen artifacts independently:
E0 page classes · E1 OCR · E2 detection · E3 linking · E4 actions · E5 privacy/preservation/consistency
E6 determinism · E7 runtime · E8 release gate · E9 outputs and re-identification · E10 annotation quality (IAA)
```
- **Detection runs on frozen extraction artifacts.** OCR cost is paid once, and OCR can be varied independently of detection.
- **Linker, policy and replacement are held constant when comparing detectors.** Any difference is then attributable to detection. Each of them can also be varied as an experiment of its own.
- **Matter-level two-pass processing.** Name propagation, canonical grouping and the DOB age window all need the whole matter, so S0–S3 run over every document of a matter before S4–S7 start.

### 4.2 Normalized interfaces (`backend/src/redactor/core`, `extraction`, `detectors`)
```python
class Extractor(Protocol):
    id: str                                    # "text_layer.pdfium", "ocr.tesseract", "ocr.rapidocr"
    def fingerprint(self) -> str: ...          # engine version + model sha256 + config hash
    def extract(self, doc: PdfRef, page: int, scope: OcrScope,
                settings: OcrSettings | None = None) -> PageExtraction: ...   # settings: dpi, psm, deskew, contrast (retry ladder, §4.11)

class Detector(Protocol):
    name: str; version: str
    def fingerprint(self) -> str: ...
    def detect(self, doc: DocumentText) -> list[EntitySpan]: ...   # pages + doc-level fields (filename, PDF metadata)

@dataclass(frozen=True, slots=True)
class EntitySpan:
    document_id: str; page: int | None; field: str | None
    text_source_id: str; start: int; end: int; text: str
    entity_type: str            # canonical taxonomy type
    native_type: str            # detector's own label, kept for audits
    confidence: float | None    # rounded to 4 dp
    detector: str               # "baseline@0.1.0"
    rule_id: str | None
    bboxes: tuple[BBox, ...]    # derived from the text source's word boxes
    attributes: tuple[tuple[str, str], ...]
    group_id: str | None        # links the pieces of one entity that is not contiguous in the text (layout rules, §7.2)
```
- **Label mapping.** Each adapter owns a table from its native labels to the taxonomy. Unmapped labels become `OTHER` and are reported.
- **Ordering.** Every list is sorted by `(document_id, page, field, start, end, entity_type, detector, rule_id)`.
- **Independence.** The evaluation code imports only these types, never a concrete detector.

### 4.3 Gold projection (what makes OCR swappable)
- **Projection.** For a text source X, each gold mention is projected onto the words of X whose boxes overlap the mention's regions. A word counts if at least 50 % of its area overlaps, and ties are broken deterministically. This yields a span in X's text.
- **Empty projection.** No overlapping words means the mention was **not recovered by extraction**.
- **Exact anchor.** When the mention's `text_anchor` is on X itself, its exact offsets are used instead.
- **Oracle source.** Pages with gold transcripts also get an **oracle** text source (`gold_transcript`). Running a detector on it measures detection with perfect OCR.
- **Reuse.** The same projection maps detections from one OCR attempt onto another (the release gate's union-of-attempts rule, §4.11).

### 4.4 Experiment definition (`experiments/*.yaml`, no PII)
```yaml
experiment_id: baseline_plus_presidio_ner
extraction: {text_layer: text_layer.pdfium, ocr: ocr.tesseract/best-eng-300dpi-psm3, hybrid: true}
detectors:
  - {name: baseline, config: config/baseline.v0.1.yaml}        # v1 (layout rules): config/baseline.v1.yaml
  - {name: presidio, config: {mode: ner_only, spacy_model: en_core_web_lg==3.8.0, score_threshold: 0.5}}
combiner: {strategy: union, overlap_resolution: [priority, longest, earliest, type_name]}
linker: rule_based@0.1.0
policy: config/policy.v0.1.yaml
replacement: surrogate@0.1.0
gate: config/release_gate.v0.1.yaml                            # optional: adds the gate metrics (§4.11)
output: {formats: [json, text, markdown, pdf_burned], pdf_dpi: 300}   # optional: adds the output metrics (§4.12)
dataset: {manifest: golden_dataset/manifest.json, split: [dev]}
determinism: {in_process_runs: 20, fresh_process_runs: 5}
```

### 4.5 Metrics
Every metric is computed per entity type, type group, page class and document, with both micro and macro averages. Raw counts are always shown next to rates.

| Family | Metrics |
|---|---|
| Page classification | Accuracy and confusion matrix against the human-verified page classes. |
| Detection | P / R / F1 with TP / FP / FN under four matching schemes: **strict** (bounds + type), **exact-boundary** (bounds only), **overlap-typed** (overlap + compatible type, e.g. DATE_OF_BIRTH ≈ DATE) and **overlap-any** (the privacy-relevant one). Also: entity-type accuracy on overlap-matched pairs, character-level coverage and a type-confusion matrix. Matching is one-to-one and greedy by overlap, with deterministic tie-breaks. REVIEW or uncertain gold mentions and ignore regions are excluded. |
| Protection (action-aware) | Let gold_protect be gold mentions whose action is SYNTHETIC or REDACT, and pred_protect be predictions whose resolved action is SYNTHETIC or REDACT. Reported: protection recall and precision at mention and character level, and **KEEP violations** (gold KEEP mentions overlapped by pred_protect). |
| Privacy (final output) | **Residual PII count and rate**: gold_protect mentions not fully replaced, plus **leak-scan** hits of normalized gold surfaces anywhere in the matter's output. The scan uses exact matching, digits-only matching for identifiers, fuzzy matching for OCR variants and surname tokens of 3+ characters. Also: **critical leakage** (critical types and roles) and a per-document leakage table. System REVIEW outputs are reported two ways: pessimistic (counted as leaked) and with-review. |
| Preservation | Preservation rate for the KEEP types (DATE, AGE, GENDER, KEEP-role organisations and locations). **Age preservation**: for every surrogate DOB, the age at each reference date equals the true age (must be 100 %; any miss is a defect). **DOB change**: the surrogate differs from the true DOB. **Non-PII character preservation**: unchanged characters outside gold_protect divided by all such characters. Over-redacted character count. |
| REDACT-class types | TFN and NATIONALITY: redaction recall, false redactions (for example "Australian Taxation Office" touched by the nationality rule) and token correctness. |
| Identifier safety | Share of surrogate identifiers that are checksum-invalid (must be 100 %). Phones inside the reserved ranges and emails on reserved domains (100 %). Surrogates that equal a real identifier or occur in the matter's source text (0). |
| Consistency | Canonical grouping pairwise P / R / F1 and B³ F1 over matched mentions. **Alias consistency**: the share of gold entities with 2+ mentions that map to exactly one synthetic identity. **Form consistency**, e.g. `title_surname` → title + synthetic surname. **DOB consistency**: one surrogate per person everywhere, re-rendered in each mention's own format. Also: collisions (distinct gold entities mapped to the same identity), cross-document consistency, and **gender preservation** (identity gender matches the registry; no "Mr" + female name). |
| Determinism | See §4.7. |
| OCR | CER / WER after NFKC and whitespace normalization, both case-sensitive and case-insensitive. **Order-agnostic bag-of-words F1**, which is robust to form reading order. **Entity-surface recovery** per type: exact, near (≤ 20 % CER), degraded or missing. Mean word confidence. Detection recall on OCR text vs oracle text. **Retry efficacy**: the share of low-confidence pages rescued by each rung of the retry ladder (§4.11). |
| Release gate | **Gate recall**: the share of pages with at least one missed gold_protect mention that the gate routes to REVIEW or BLOCKED (the most important one). **Unsafe-pass count**: pages marked PASS that still leak (target 0). **Review load**: the share of pages in REVIEW, by reason. Retry rescue rate. Precision of final-leak-scan hits. |
| Outputs | JSON schema validity and render determinism. **Edit-log completeness**: every changed character range is covered by exactly one edit (checked locally against the private originals). The exported JSON holds no original text (leak test). **PDF burn-in**: share of gold_protect region area covered by boxes; collateral area (boxed area outside any gold_protect region, as a share of all boxed area); residual PII found by re-OCR of the output pages; no text layer, metadata or original filename left. |
| Re-identification | **Round trip**: pseudonymize then re-identify restores every SYNTHETIC-class span exactly (REDACT tokens are excluded by design). **Simulated downstream answers** (templated texts using surrogate forms: possessives, initials, "Surname, Given", case changes): restoration rate, ambiguity rate and wrong-restoration rate (target 0). **One-way check**: REDACT tokens are never restored and TFN values are not stored in the vault. |
| Annotation quality (IAA) | See §9.3: span F1 between annotators, token-level Cohen's κ, type agreement, canonical-grouping agreement, action agreement, region IoU, page-class agreement and A1's test–retest agreement. IAA is the ceiling for what a detector metric can claim. |
| Runtime | Wall-clock per stage per page (p50 / p95), measured outside the determinism runs. |

### 4.6 Attributing failures to OCR vs detection
Each gold_protect mention gets one outcome per experiment:

| Outcome | Meaning |
|---|---|
| `OK` | Detected and protected. |
| `DETECTOR_MISS` | OCR recovered the surface exactly, but the detector did not find it. |
| `OCR_INDUCED_MISS` | OCR degraded the surface and the detector missed it; cross-checked against the oracle text where one exists. |
| `OCR_LOSS` | OCR did not recover the surface at all. |
| `POLICY_MISS` | Detected, but given the wrong action. |
| `REPLACEMENT_MISS` | Given a protect action, but the surface is still in the output. |

Each failed mention is also tagged `gate_flagged` (true or false), so the report shows which misses the release gate would have caught. The results are tabulated per detector and page class, so the report shows directly whether OCR or detection needs fixing.

### 4.7 Determinism harness
- **Stage isolation.** Separate tests for: (a) OCR on sample pages, including every rung of the retry ladder; (b) detection on frozen extraction; (c) linking and replacement on frozen detections; (d) gate decisions; (e) output rendering (JSON, text, Markdown, PDF); (f) re-identification; (g) end to end.
- **Run counts.** The default is 20 in-process runs plus 5 fresh-process runs, each with a different `PYTHONHASHSEED`; it can be raised to 100. Fresh processes catch hash-ordering bugs that are invisible inside a single process.
- **Compared:** span sets, emitted order, entity types, canonical IDs, replacements, gate decisions and final-output SHA-256 (JSON bytes, rendered text and, for PDFs, both the file bytes and a hash of the rasterized pages).
- **Recorded:** counts of identical and differing runs, plus a structured diff of the first divergence (added, removed or changed spans; order, type, ID and replacement changes).
- **Execution settings.** Runs are single-flight, with `OMP_THREAD_LIMIT=1` and ONNX runtime and torch threads set to 1 where relevant. Evaluation uses fixed test keys for the vault. Production matter keys are random, so replacement determinism is measured *given a key*.
- **Local LLMs (later).** Record and pin:
  - the GGUF SHA-256;
  - the runtime build;
  - the sampling parameters and seed;
  - JSON-schema-constrained output.

  Temperature 0 is **not** assumed to guarantee determinism; it is measured.

**Code rules**
- Explicit total ordering everywhere.
- No `hash()`, `uuid4`, clock or random values inside hashed outputs; IDs are derived with SHA-256.
- Canonical JSON (sorted keys, UTF-8, fixed float rounding) for every hash.
- Pinned lockfiles, model hashes and container digests.
- The PDF writer is deterministic: no timestamps, a fixed document ID, stripped Info and XMP, and a pinned rasterizer.

### 4.8 Reporting
- **Machine-readable:** `runs/<run_id>/public/results.json` (plus CSV).
- **Human-readable:** `report.md`, containing:
  - the comparison table: Experiment | Documents | PII P | PII R | F1 | Residual PII | Protected-info preservation | Entity consistency | Detection determinism | Replacement determinism | OCR | Runtime. "Protected-info preservation" covers the KEEP types (DATE, AGE, GENDER) and the age preservation of surrogate DOBs;
  - breakdowns for PERSON, ORGANIZATION, ADDRESS, EMAIL, PHONE, legal identifiers, medical/provider identifiers, page class (born_digital / scanned / vector_outlined / hybrid) and each document;
  - the attribution table, the determinism table, the **release-gate table** (gate recall, unsafe passes, review load by reason, retry rescue rate) and the **output table** (JSON, PDF burn-in, re-identification);
  - the **dataset-quality table** (IAA, §9.3), so detector scores are read against the annotation ceiling.

  There is **no single overall score**.
- **Small-sample honesty:** raw counts, document-level bootstrap intervals, and dev and test splits reported separately. Layout-rule results on doc_002 are labelled "dev, not held-out" (§7.2).
- **Private outputs:** `runs/<run_id>/private/` holds predictions with their text and a local HTML error browser.
- **Run manifest** records:
  - config hash, dataset manifest hash and git commit;
  - the environment fingerprint: Python version, lockfile hash, pdfium version, Tesseract version and traineddata SHA-256, second OCR engine and model hash, spaCy model, Node and Java versions, container digests and thread settings.

---

### 4.9 Replacement design (needed for the E5 metrics; built in R1)

#### 4.9.1 Person names
- **Names are replaced component by component, preserving gender.** Within each matter:
  - each surname maps to a synthetic surname;
  - each (first name, gender) pair maps to a synthetic first name;
  - initials go through a per-matter letter permutation, and the synthetic names are chosen to start with the permuted initial.

  As a result, "John Smith", "John", "Mr Smith", "SMITH, John" and "J. Smith" stay mutually consistent *even when linking is imperfect*, and a shared family surname stays shared. Titles and pronouns are never changed. Casing and "Surname, Given" ordering are preserved.
- **Gender is taken from these sources, in order:**
  1. the honorific;
  2. registry or linker evidence;
  3. a first-name lexicon;
  4. otherwise `unknown`, which gets a unisex surrogate.
- **Surrogate pools are mixed-origin.** They are not matched to the apparent origin of the original name, so the output does not bring back the nationality hints that NATIONALITY redaction removes.
- **Surrogates never occur in the matter's extracted text** (case-insensitive). That avoids collisions with real names the detector missed, and it keeps re-identification unambiguous (§4.12.4).
- **Keyed derivation.**
  - HMAC-SHA256(matter key, type ‖ normalized value) indexes into versioned, hashed surrogate lists.
  - Collisions are resolved with a counter over sorted inputs, so the mapping is injective within a matter.
  - Faker is not used at runtime.
  - Because the derivation is keyed, nobody can re-identify people by computing surrogates for guessed real names.

#### 4.9.2 DATE_OF_BIRTH: age-preserving surrogate
- **Goal.** The legal analysis needs the person's age, not their birthday. Every age the documents state or imply stays correct, while the real date of birth disappears.
- **Age.** `age(B, R)` is the number of completed years between birth date B and reference date R. A 29 February birthday counts as 1 March in non-leap years, and 29 February is never issued as a surrogate.
- **Reference dates (R).** Per person and matter: DATE mentions whose `date_role` is `date_of_injury`, `examination`, `report` or `claim`, plus every DATE in the same sentence as an AGE mention. The DOB itself is excluded. The baseline infers the roles from keywords such as "date of injury", "examined on" and "report dated".
- **Feasible set F.** All dates B′ in the **same birth year** with `age(B′, R) = age(B, R)` for every R, and B′ ≠ B. In practice this is a window of consecutive days around B, between the nearest reference-date anniversaries.
- **Issue.** `B′ = F[ HMAC(matter key, "DOB" ‖ canonical_id ‖ ISO(B)) mod |F| ]`, with F sorted ascending. It is issued once per person and frozen in the vault, so every mention in every document gets the same B′.
- **Re-rendering.** Each mention is rebuilt from B′ in its own format (dd/mm/yyyy, d MMMM yyyy, ordinal, two-digit year) and granularity. A month-and-year mention uses B′'s month. A year-only mention is untouched, because the year is kept.
- **Guard.** If `|F| < min_window_days` (default 14, to be tuned in the pilot), the fallback is the token `[DATE_OF_BIRTH]` plus a REVIEW note. A near-identical date is never issued.
- **Conflict.** A later document may add a reference date that breaks the frozen B′. The gate then raises `DOB_AGE_CONFLICT` (REVIEW). Re-issuing bumps the vault epoch (§4.9.5) and requires re-exporting the affected outputs.
- **What is preserved and what is lost.** Preserved: birth year and the age at every reference date. Replaced: day and month of birth (within F). The documents' kept DATEs and AGEs stay consistent with the surrogate.

#### 4.9.3 Identifiers: checksum-invalid or reserved-range
| Type | Strategy |
|---|---|
| TFN | **REDACT** → `[TFN]`. The vault keeps only a keyed hash (leak-scannable, never restorable). |
| MEDICARE, ABN, ACN, PROVIDER_NUMBER, IHI | Same length, grouping and leading-digit rules as a real number. Digits come from the HMAC, then the check digit is forced to be **invalid** using the same validator the baseline uses. A property test proves no surrogate ever validates. |
| PHONE | Numbers from the range ACMA reserves for fictional use (verify the current list before implementing), keeping the area code or mobile class and the spacing. 13/1300/1800 numbers follow the organisation's action. |
| EMAIL | Reserved domains (example.com, example.org, example.net; RFC 2606). The local part is derived from the synthetic name. |
| ADDRESS | Fictitious street, real suburb and postcode in the same state (ABS localities, CC BY 4.0). |
| CLAIM_NUMBER, COURT_FILE_NUMBER, POLICY_NUMBER, ACCOUNT_NUMBER (BSB), PASSPORT, DRIVER_LICENCE, VEHICLE_REGISTRATION, CENTRELINK_CRN, AHPRA, MRN, DVA | No public checksum or reserved range exists, so the surrogate is format-preserving: keyed digits or letters with separators, prefix class and length kept. It is collision-checked against the vault and against every identifier-like token in the matter's text. |

- Because the surrogates are invalid by construction, **a checksum-valid identifier in any output is an automatic leak alarm** (§4.11). The post-scan allow-lists the surrogates the vault knows about.
- Surrogates are also never equal to a real identifier of the matter.

#### 4.9.4 Other actions
- **NATIONALITY** becomes the constant token `[NATIONALITY]`. It is one-way.
- **KEEP types** (DATE, AGE, GENDER, KEEP-role organisations and locations) are untouched.
- **Organisations** are replaced by synthetic names that keep their kind (for example "… Pty Ltd", "… Medical Centre"), and the same canonical organisation always gets the same name.

#### 4.9.5 Vault, matter key and two-pass processing
- **Vault.** One SQLite database per matter in `data/vault/`. It holds the matter key, canonical key → real value and forms, every surrogate issued, the frozen DOB surrogates and an `epoch` counter. `SYNTHETIC` values are stored reversibly and `REDACT` values as keyed hashes only (`vault_mode`).
- **Encryption at rest.** The vault is encrypted, with a key from the OS credential store or a passphrase. The vault and its key are never in git and never in logs.
- **Matter key.** Random in production (CSPRNG), so surrogates cannot be recomputed from the code. Evaluation uses fixed test keys.
- **Two passes.** S0–S3 run for all documents of the matter and only then are replacements issued. This is required by name propagation, canonical grouping and the DOB window.
- **Epochs.** Mappings are frozen once issued. A conflict (a new reference date, or a surrogate that appears in a newly added document) raises a REVIEW item. Resolving it may re-issue a surrogate, which bumps the epoch and marks the affected exports stale.

#### 4.9.6 Edit log
Each replacement records (start, end, replacement, canonical_id, rule, strategy). This gives the offset map that the evaluation needs and the audit trail for the export (§4.12.1).

### 4.10 Service boundaries (Phase 9)
- **Domain packages** (dataset, extraction, detectors, evaluation, gate, output, reidentify) are plain Python used by both the CLI and the API. The API contains no business logic.
- **FastAPI routers:**
  - `/documents`: register or upload, metadata, page renders.
  - `/extractions`: words per page and text source.
  - `/annotations`: GET and PUT with schema validation and a revision check, in a per-annotator namespace (A1, A2, adjudicated).
  - `/iaa`: agreement metrics and the adjudication worklist.
  - `/experiments`: list and launch. Launched runs execute in a separate worker process.
  - `/runs`: status, public results, and private details for the local UI only.
  - `/review`: the REVIEW queue and reviewer decisions (§4.11).
  - `/export`: produces the JSON, text, Markdown and PDF outputs. It refuses (HTTP 409) while the gate is not clear.
  - `/reidentify`: local re-identification of downstream text (§4.12.4). Localhost only, role-gated and audit-logged.
  - `/health`.
- **Next.js frontend:**
  - It talks only to the local API (CSP `connect-src 'self'`).
  - Pages: `/documents`, `/annotate/[doc]/[page]`, `/adjudicate/[doc]`, `/runs`, `/runs/[id]`, `/runs/[id]/compare/[doc]/[page]`, `/review`.
  - TypeScript types are generated from the OpenAPI schema.

### 4.11 Production release gate (fail-closed)
**Purpose.** Nothing is exported unless every page has been checked. Anything uncertain goes to a person. Any residual the final leak scan can find blocks the export. The gate is also evaluated offline against the golden_dataset (§4.5, "Release gate").

**States and export rule**
- `PASS`: the page meets every criterion.
- `REVIEW`: a person must decide before export.
- `BLOCKED`: the final leak scan found a residual. Nothing from the document is exported until it is resolved.
- A document is exported only when every page is `PASS` (or `REVIEW` with a recorded decision) and the final leak scan is clean. There is **no force flag**. The only override is a recorded decision by an authorised reviewer.

**Triggers** (`config/release_gate.v0.1.yaml`). Thresholds are calibrated on the dev split and frozen before the test split is scored. Any numbers quoted in this plan are placeholders, not results.

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
├── plans/PLAN_V1.md                 # this plan (PLAN_V0.md is revision 1)
├── docs/annotation_guidelines.md    # tracked, no PII
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
   - **test:** doc_001, doc_004 (31 pages). The test split is never used for rule tuning and is annotated without pre-annotation.

## 9. Annotating the golden_dataset_docs

### 9.1 Who annotates, and where
- **Firm-authorised people, locally.** Annotators A1 and A2 and an adjudicator work in the local annotation UI (milestone A1), on the firm's machine, with local OCR and local tools only.
- **The assistant does not annotate** (§2.2). It builds the tooling and the synthetic tests. An exception has to be explicit, because document content would then reach the model provider.
- **Optional local help.** Deterministic detectors (and later a pinned, offline local model) can pre-annotate. Their output is a *candidate*, never gold (§9.5).
- **What "highest accuracy and determinism" means here.** No gold standard is perfect. The plan raises quality with independent double annotation on part of the set, a recall audit against every local detector, automated validators and adjudication. It then *measures* the remaining noise (IAA, test–retest) and reports it as the ceiling for every detector score. For determinism, every artifact is reproducible from the same inputs (§9.4).

### 9.2 Pipeline
| Step | What happens | Output |
|---|---|---|
| P0 Register | Safe IDs, hashes, read-only copies (§8) | manifest |
| P1 Classify | Automatic page classes, then a person verifies all 88 pages | human-verified page classes |
| P2 Extract | Pinned renderer and OCR, single-threaded; frozen, cached text sources | text sources with word boxes |
| P3 Candidates | Deterministic detectors propose spans. Only for dev documents outside the IAA set | `candidates.json`, never gold |
| P4 Pass 1 | A1 annotates every page plus the filename and PDF metadata fields: type, region, role, canonical ID, `date_role`, flags. A1 also builds the registry | `annotations_raw/A1/` |
| P5 Pass 2 | A2 annotates the IAA documents independently (§9.3) | `annotations_raw/A2/` |
| P6 Validate | Automated validators; zero errors to proceed (§9.4) | validation report |
| P7 Recall audit | Every span a local detector predicts but the gold lacks is accepted or rejected, with a reason (§9.4) | `audit/` |
| P8 Adjudicate | A1 and A2 disagreements are resolved by the adjudicator, with reasons | `annotations/` |
| P9 Freeze | Canonical serialization, deterministic IDs, manifest hashes, version tag | `golden_dataset@v1.0` |

### 9.3 Second annotator and inter-annotator agreement
- **Coverage.** A2 independently annotates **at least 2 documents**. Default: **doc_003** (born-digital, 23 pages) and **doc_004** (scanned, 22 pages), 45 of 88 pages. That covers a text-layer document and an OCR document, one from dev and one from test. Adding doc_002 (the hardest form to anchor) is recommended if the budget allows.
- **Independence.** A1 and A2 work blind: no candidates, no access to each other's work, the same guidelines version (v0.2, after the pilot). Both annotate from scratch.
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

### 9.4 Accuracy and determinism controls
**Validators (P6)** run on every annotation file. The dataset cannot be frozen with errors:
- schema and version; coordinates inside the page; regions non-empty;
- `text_anchor` text equals the projected text of its source; no overlapping gold spans;
- every PERSON has a `canonical_id`; every canonical entity has at least one mention; gender evidence is present;
- every DATE_OF_BIRTH belongs to a canonical person;
- **DOB against stated age:** the age computed from a DOB at its reference date must match the stated AGE. A mismatch flags a possible misread or typo;
- identifier checksums (a mismatch raises a verification flag, not an error);
- every `handwritten` or `partially_illegible` flag has a reviewer note.

**Recall audit (P7).** This is pooling, as in information retrieval.
- Every local detector (the baseline, Presidio, OpenRedaction, Philter, when available) runs over all text sources. So do a checksum scan, a NATIONALITY and GENDER lexicon scan and a capitalised-token scan.
- Each predicted span that does not overlap the gold is reviewed. It is either accepted (added to the gold with `origin=audit_added`) or rejected with a reason from a fixed list (`not_pii`, `kept_public_body`, `generic_term`, `ocr_noise`).
- Because it pools several detectors, it does not favour any single one.

**Determinism of the annotation artifacts**
- The same PDF bytes give the same render (pinned pdfium and DPI) and the same OCR (pinned Tesseract and traineddata hash, fixed settings, one thread).
- Gold files are canonical JSON with no timestamps. Entity IDs are renumbered at freeze time by a fixed sort (page, y0, x0, type), so the same annotations always yield the same bytes.
- Candidates come from pinned, deterministic detectors, so a re-run gives the same candidates.
- `dataset verify` revalidates every file, recomputes all hashes and compares them with the frozen manifest. Any later change is a new revision with a changelog.

**Definition of done for golden_dataset v1.0:** validators pass; the audit and adjudication are complete; IAA is reported and meets the targets (or the shortfall is documented); the dataset is frozen and tagged; `dataset verify` passes.

### 9.5 What must be manually annotated (by the firm, locally)
| Item | Scope | Rough effort |
|---|---|---|
| Page-class verification | Confirm or correct the classifier on all 88 pages | 0.5 h |
| Pass 1 (A1) | All PII mentions on 88 pages, plus filenames and PDF metadata: type, boundaries and regions, role, canonical_id, `date_role`, attributes, flags. Candidates help on dev pages outside the IAA set | 18–26 h |
| Pass 2 (A2) | doc_003 and doc_004, blind (45 pages) | 8–12 h |
| Canonical registry | Per matter: people (with gender, evidence and DOB), organisations and their roles | 1–2 h |
| Region-only items | Signatures, photos, handwriting | 1 h |
| Gold transcripts (OCR subset) | About 12 pages corrected from an OCR draft: doc_001 × 3 (vector), doc_002 × 5 (MRC form), doc_004 × 4 (scan) | 4–6 h |
| Recall audit | All documents | 3–5 h |
| Adjudication | A1 vs A2 on the IAA documents | 2–4 h |
| Second look by A1 | Non-IAA documents (43 pages), on a different day | 3–5 h |
| **Total** | Across two or three people | **≈ 40–60 h** |

These are planning estimates, to be re-measured in the pilot.

**Process**
1. Write the guidelines (v0.1).
2. Pilot on doc_005 (4 pages) and 3 pages of doc_002, annotated by both A1 and A2, recording the time per page. The pilot pages are not part of the IAA set.
3. Fix the schema and guidelines, then freeze them as v0.2.
4. Run steps P4 to P9 of §9.2.
5. Tag the dataset as v1.0 in the local repo.

**Pre-annotation rules**
- Candidates appear as dashed boxes and are stored separately from gold.
- They are allowed on the dev split only, and never on the IAA documents or the test split.
- They come from the union of the baseline and Presidio, to limit bias toward the baseline.
- `provenance.origin` records whether each gold mention started as a candidate.

---

## 10. Policy questions that affect annotation
Because annotations record facts and roles, most answers change `config/policy.*.yaml` rather than the annotations. Question numbers are stable: Q5, Q7, Q14 and Q16 of the first draft moved to 10.1.

### 10.1 Decided (revision 2)
| Question | Decision |
|---|---|
| DATE_OF_BIRTH | SYNTHETIC, age-preserving (§4.9.2). Fallback: `[DATE_OF_BIRTH]` |
| TFN | REDACT → `[TFN]`, one-way |
| NATIONALITY | REDACT → `[NATIONALITY]`, one-way. Covers demonyms, citizenship and country of origin, never proper nouns |
| Identifiers | Checksum-invalid surrogates where a checksum exists. Reserved ranges for phones and emails |
| DATE, AGE, GENDER | KEEP (surrogate names keep the gender) |
| Output target | Structured JSON first, rendered to text or Markdown. PDF output is rasterized with burned-in boxes |
| Downstream results | Real names are restored locally through the vault (SYNTHETIC types only) |
| Filenames and PDF metadata | In scope. Annotated as `field` entities |

### 10.2 Still open, with proposed defaults
| # | Question | Proposed default |
|---|---|---|
| 1 | Names of treating practitioners and examining experts | SYNTHETIC. The role is annotated, so this can flip to KEEP later |
| 2 | Lawyers, barristers, insurer staff, judges and registrars | SYNTHETIC for lawyers and staff; KEEP for judicial officers |
| 3 | Organisations by role | SYNTHETIC for employers, law firms, hospitals and practices. KEEP for statutory bodies, courts and government. **Insurers: open** |
| 4 | Locations | KEEP country and state. SYNTHETIC for suburb, city, street and incident site, using a same-state surrogate |
| 6 | Ages ("a 52-year-old") | KEEP |
| 8 | Organisational phones (13/1300/1800), organisational emails, company ABN/ACN | Follow the organisation's action |
| 9 | This matter's claim, court and policy numbers | SYNTHETIC (critical) |
| 10 | Provider and AHPRA numbers | Follow the practitioner-name decision |
| 11 | Signatures, handwriting, photos, logos | Annotate as regions. REDACT in PDF output. Not represented in text output |
| 12 | Quasi-identifiers: occupation, incident narrative, rare conditions, and also ethnicity, Indigenous status, religion and spoken language | Out of scope for v0; flag only |
| 13 | People whose gender is unknown | Use a unisex surrogate. Gold records gender only when the document gives evidence |
| 15 | Matter boundaries | All five documents are one matter (to confirm) |
| 17 | How system REVIEW outputs count in leakage | Primary: pessimistic (counted as leaked). Secondary: with human review |
| 18 | Who are A1, A2 and the adjudicator? | A2 must be a different person from A1 (§9.3) |
| 19 | DOB window | `min_window_days` of 14 and the reference-date rule of §4.9.2. Confirm in the pilot |
| 20 | May pseudonymized text go to an external tool (for example a cloud LLM) under the firm's policy and client terms? | The system never sends anything itself. The answer only decides how the re-identification feature is used |
| 21 | Vault custody: key storage (OS credential store or passphrase), backup and retention, who may call re-identification | OS credential store, backup by the firm, role-gated access |
| 22 | Assistant access to the PDFs | Stay out (default). Any exception is explicit and recorded |

---

## 11. Implementation sequence

### Evaluation foundation (starts right after approval)

**F0: Safety scaffold**
- Confirm `plans/PLAN_V1.md` as the plan of record.
- Set up the repo:
  - nested `git init` with no remote;
  - `.gitignore`;
  - the pre-commit guard;
  - `.claude/settings.json` deny rules (and `REDACTOR_DATA_ROOT` if the confidential folders move outside the repo);
  - `CLAUDE.md` containing the data-boundary and determinism rules.
- *Ask first* before adding an exclude entry to the Desktop-level repo.

**F1: Core and schemas**
- A uv project on Python 3.12.
- Core types, coordinate transforms, canonical JSON and hashing.
- Taxonomy and policy YAML (with the revision-2 actions and strategies).
- Pydantic golden models (including AGE, `date_role`, NATIONALITY `form` and per-annotator files), JSON Schema export and `dataset validate`.

**F2: Register and ingest**
- `dataset register golden_dataset_docs/`: safe IDs, hashes, read-only copies, manifest. It is quick, and it is the first command run on the real files.
- The pypdfium2 page classifier and the metadata writer.
- An aggregate-only summary.

**F3: Extraction**
- Renderer, text-layer extractor, Tesseract TSV extractor, hybrid merge, cache and fingerprints.
- Tesseract 5 is required. The recommended reference environment is a pinned Docker image; a native install also works for development. I will **confirm with you before installing anything**.

**F4: Detection**
- Detector protocol, registry and combiner.
- baseline v0 (§7.1). Baseline v1 (§7.2) follows as a separate experiment.

**F5: Evaluation core**
- Projection, matching, detection and protection metrics.
- OCR metrics and failure attribution.
- The IAA module (it shares the matching code).
- The determinism harness (in-process and fresh-process runs).
- The run manifest and public/private reports, including the leak test.
- A synthetic fixture generator with exact ground truth, covering born-digital pages, rasterized scans with noise and skew, vector-outlined pages, hybrid pages and synthetic forms.

### Next milestones

**A1: Minimal annotation UI (Next.js + FastAPI)**
- Shows the page image with word boxes.
- Lets the annotator select words or draw a region, then set type, role, canonical ID (pick an existing one or create one), override, flags, `date_role` and notes.
- Per-annotator files, a blind mode, candidates shown for accept or reject (dev only), and later an adjudication view and an audit-review view (simple tables first).
- Saves with validation and a revision check. It is not overbuilt before the schema is stable.

**A2: Annotation campaign** (human work; it overlaps the build milestones below)
- Pilot, schema v0.2, pass 1, pass 2 (IAA), validators, recall audit, adjudication and freeze v1.0 (§9).

**R1: Linking, policy, replacement and vault**
- Rule-based linker and policy engine.
- Replacement: names, the age-preserving DOB, identifier surrogates, the TFN and NATIONALITY tokens.
- The encrypted vault with epochs, and the E5 metrics.

**G1: Release gate**
- The second OCR engine (RapidOCR or docTR) with an OCR comparison between the two engines, the retry ladder, the final leak scan and the REVIEW queue (API and UI).
- An extractor for scanner-produced OCR layers, once such documents appear.
- The gate metrics (E8).

**X1: Outputs and re-identification**
- Structured JSON, text and Markdown renders, and the PDF rasterize-and-burn-in writer with output verification.
- The local re-identification service and its metrics (E9).

**C1: Candidate adapters.** Each is labelled "as shipped" unless noted otherwise:
- **Presidio:** the default configuration with the AU recognizers explicitly enabled and checked at startup, plus an NER-only variant.
- **OpenRedaction:** a Node sidecar with AI assist disabled and no network access.
- **Philter/Phileas:** Docker on the internal network. The default policy and an "AU-extended" policy are run as separate experiments.

**C2: Combinations.** Only where single-system results show complementary recall:
- baseline + Presidio NER comes first, because names, organisations and locations are the baseline's weakest types by design;
- then baseline + OpenRedaction and baseline + Philter;
- then baseline + additional local NER;
- then baseline + a local LLM fallback applied to REVIEW spans only.

**U1: Results UI**
- Experiment table and breakdowns.
- Gold vs prediction overlays side by side, and determinism diffs.
- Document upload, the redaction preview and the review-queue screens.

## 12. Verification

**Synthetic suite** (`uv run pytest`; the assistant can run it):
- Validator test vectors (TFN, ABN, ACN, Medicare, IHI, provider number).
- Property tests for offset maps and coordinate round trips.
- The page classifier on one synthetic PDF per class.
- Metric tests with hand-computed expectations, using perfect, empty, off-by-one and type-swapped dummy detectors.
- The determinism harness catching a deliberately nondeterministic dummy detector.
- The network guard blocking a non-loopback connection.
- Schema round-trip and JSON Schema validation.
- The public-report leak test.
- **DOB surrogate:** property tests that the age at every reference date is preserved, the birth year is kept, the real DOB never appears, leap-day cases work, a tight window falls back to the token, and a later reference date raises `DOB_AGE_CONFLICT`.
- **Identifier surrogates:** no Medicare, ABN, ACN, provider or IHI surrogate ever validates; TFN is always `[TFN]`; phones fall in the reserved range; surrogates never equal a real identifier or occur in the source text.
- **NATIONALITY:** the lexicon redacts demonyms, citizenship and country-of-origin phrases, and leaves "Australian Taxation Office" alone.
- **IAA:** synthetic annotator pairs with known differences reproduce the expected F1, κ and B³ values.
- **Layout rules (v1):** synthetic forms with shuffled reading order recover the label–value pairs that v0 misses.
- **Gate:** low-confidence, `unknown` and handwriting pages route as specified; a retry never passes a page that fails the same checks; the union-of-attempts rule never drops a protection; a planted residual blocks the export; there is no force path.
- **Outputs:** the exported JSON contains no original text; the edit log is complete; text and Markdown renders are byte-identical across runs; a burned PDF has no text layer or metadata, and re-OCR of it finds no planted PII; the PDF writer is byte-deterministic.
- **Re-identification:** a round trip restores every SYNTHETIC span; templated "downstream answers" (possessives, initials, case changes) restore correctly; unknown or ambiguous tokens are left alone; REDACT tokens are never restored.

**Real-data smoke run** (local, aggregate output only):
- `dataset register` finds 5 documents and 88 pages, and the SHA-256 prefixes match §1.2. The page classes match §1.2: 9 vector_outlined, 52 scanned and 27 text-layer, with doc_003 p1 flagged as a hybrid candidate.
- `extract` fills the OCR cache for all 61 OCR pages. OCR determinism: 5 pages × 20 runs, all identical.
- `run experiments/baseline.yaml` produces 25 of 25 identical runs, and the public report has zero leak-test hits.
- The `golden_dataset_docs/` hashes are unchanged at the end.
- After the annotation campaign: `dataset verify` passes and the IAA report meets the targets.

---

## could_be_changed
Optional recommendations. Revision 2 adopted the earlier items on DOB, identifier surrogates, the second annotator, layout-aware rules, the fail-closed gate and the structured output (§0.2). What is left:

1. **Grow the golden_dataset before picking a winner.** This matters more now. The set is 5 documents from one matter, with a single hybrid candidate, no scanner OCR layers and **one form**, which sits in the dev split. So baseline v1's layout rules cannot be scored on held-out data, and the gate thresholds are calibrated on very few pages. Aim for about 25–30 documents across types: GP and hospital records, payslips, ATO and Centrelink letters, police and motor-vehicle-accident reports, CTP and WorkCover forms (at least 3 more forms), and handwritten forms. Keep a held-out split.
2. **Add a matter intake sheet.** A short list, entered by the firm, of the known parties for a matter: client name and aliases, date of birth, address, employer, claim and policy numbers. It would seed the baseline's name propagation, pre-fill the vault so the leak scan knows the key people from the start, and support the audit. It is the cheapest large gain in both recall and leak-scan strength, at the cost of one manual step per matter.
3. **State plainly that pseudonymization is not anonymization.** An incident narrative combined with dates, employer and occupation can re-identify someone (for example through news reports), so the output is still personal information under the Privacy Act 1988. Put this in the README and the guidelines, and add optional quasi-identifier flags for risk reporting.
4. **Consider more sensitive attributes than nationality.** Ethnic origin, Indigenous status, religion and language spoken are not in the v0 taxonomy (they are only flagged). Once the pilot shows how often they occur, the same closed-lexicon approach as NATIONALITY could redact them, or send them to REVIEW by default.
5. **Run a human re-identification test before production use.** Someone who has not seen the originals tries to identify the client from a sample of exported outputs. It tests the combined risk of names, dates, employers and narrative, which no automatic metric covers.
6. **Considered and not recommended: shifting dates per matter.** Limitation periods, statutory deadlines and ages all depend on the true dates. The age-preserving DOB surrogate already covers the one date that needs protecting.
