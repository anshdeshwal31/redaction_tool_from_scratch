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
| Annotation quality (IAA) | See §9.3: span F1 between annotators, token-level Cohen's κ, type agreement, canonical-grouping agreement, action agreement, region IoU, page-class agreement and A1's test–retest agreement, plus **silver vs human** (how often Claude's first pass agrees with the verified gold). IAA is the ceiling for what a detector metric can claim. |
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
- **Silver label.** Every score computed against gold that has not been fully human-verified is labelled **silver** in the tables and in `results.json` (`gold_status`: `verified`, `partly_verified` or `silver`). Silver scores are never shown as trusted results (§9.4).
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
  - `/annotations`: GET and PUT with schema validation and a revision check, in a per-annotator namespace (A1, A2, claude_silver (read-only), adjudicated).
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
