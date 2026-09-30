### 9.4 Accuracy and determinism controls

**Trust rule for silver (EX-001)**
- **Test split:** a person verifies every page of doc_001 and doc_004 before any test score is trusted.
- **Other documents:** a person verifies a seeded random sample of the remaining pages: 20 % of each document's pages, at least 5 pages (or all pages if the document has fewer), stratified by page class. The seed is recorded in the manifest and the sample is drawn before verification starts. doc_003 is annotated by A1 and A2 from scratch on the IAA path, so it counts as verified.
- **Acceptance:** on the verified pages, silver must reach a strict span F1 of at least 0.90 on the protect types, with no missed critical-type mention. If a document's sample falls short, the whole document is verified. The thresholds are initial and are confirmed after the pilot.
- **Labels:** pages are `silver` (unverified) or `verified`, and scores carry a `gold_status` (§4.8). A silver page is copied into `annotations/` only with `verified_by` and a date.
- **Not reproducible:** a model's output cannot be guaranteed to repeat (temperature 0 does not guarantee it). Silver files are therefore frozen when written, with the model ID, run date, guidelines version, prompt hash and input hashes. They are never regenerated to "check" them; a new run makes a new file.

**Validators (P6)** run on every annotation file, silver files included. The dataset cannot be frozen with errors:

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

**Definition of done for golden_dataset v1.0:** validators pass; the audit and adjudication are complete; IAA is reported and meets the targets (or the shortfall is documented); the silver trust rule is met (whole test split verified, sample verified and accepted); the dataset is frozen and tagged; `dataset verify` passes.

### 9.5 What must be manually annotated (by the firm, locally)


| Item                          | Scope                                                                                                                                                                                             | Rough effort  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| Page-class verification       | Confirm or correct the classifier on all 88 pages                                                                                                                                                 | 0.5 h         |
| Silver pass (Claude Code) | All 88 pages plus the filename and PDF metadata fields of all five documents, in one run, then fixes for any file that fails validation | Model time, plus 1–2 h of person time to review the validation results |
| Pass 1 (A1), from scratch | doc_003 and doc_004 (45 pages), blind to silver | 9–13 h |
| Verification of silver (A1) | doc_001 in full (9 pages), plus the seeded sample of doc_002 and doc_005 (about 10 pages), with corrections | 3–5 h |
| Pass 2 (A2)                   | doc_003 and doc_004, blind (45 pages)                                                                                                                                                             | 8–12 h        |
| Canonical registry            | Per matter: people (with gender, evidence and DOB), organisations and their roles                                                                                                                 | 1–2 h         |
| Region-only items             | Signatures, photos, handwriting                                                                                                                                                                   | 1 h           |
| Gold transcripts (OCR subset) | About 12 pages corrected from an OCR draft: doc_001 × 3 (vector), doc_002 × 5 (MRC form), doc_004 × 4 (scan)                                                                                      | 4–6 h         |
| Recall audit                  | All documents                                                                                                                                                                                     | 3–5 h         |
| Adjudication                  | A1 vs A2 on the IAA documents                                                                                                                                                                     | 2–4 h         |
| **Total** | Across two or three people | **≈ 33–50 h** |


These are planning estimates, to be re-measured in the pilot. The silver pass replaces most from-scratch annotation, so person-hours fall; the verification rule (§9.4) is what keeps the gold trustworthy.

**Process**

1. Write the guidelines (v0.1).
2. Run the silver pass (milestone S1) on all five documents, with schema v0.1 and guidelines v0.1.
3. Pilot on doc_005 (4 pages) and 3 pages of doc_002, annotated by both A1 and A2, recording the time per page. The pilot pages are not part of the IAA set.
4. Fix the schema and guidelines, freeze them as v0.2, and migrate the silver files with an explicit migration function (the v0.1 originals are kept).
5. Run steps P4 to P9 of §9.2.
6. Tag the dataset as v1.0 in the local repo.

**Pre-annotation rules**

- Candidates appear as dashed boxes and are stored separately from gold.
- They are allowed on the dev split only, and never on the IAA documents or the test split.
- They come from the union of the baseline and Presidio, to limit bias toward the baseline.
- Silver is not a candidate. It is a full annotation with its own trust rule (§9.4). Verifiers see it as their starting point, except on the IAA documents, where A1 and A2 never see it (§9.3).
- `provenance.origin` records whether each gold mention started as a candidate.

---



## 10. Policy questions that affect annotation

Because annotations record facts and roles, most answers change `config/policy.*.yaml` rather than the annotations. Question numbers are stable: Q5, Q7, Q14 and Q16 of the first draft moved to 10.1.

### 10.1 Decided (revision 2)


| Question                   | Decision                                                                                                  |
| -------------------------- | --------------------------------------------------------------------------------------------------------- |
| DATE_OF_BIRTH              | SYNTHETIC, age-preserving (§4.9.2). Fallback: `[DATE_OF_BIRTH]`                                           |
| TFN                        | REDACT → `[TFN]`, one-way                                                                                 |
| NATIONALITY                | REDACT → `[NATIONALITY]`, one-way. Covers demonyms, citizenship and country of origin, never proper nouns |
| Identifiers                | Checksum-invalid surrogates where a checksum exists. Reserved ranges for phones and emails                |
| DATE, AGE, GENDER          | KEEP (surrogate names keep the gender)                                                                    |
| Output target              | Structured JSON first, rendered to text or Markdown. PDF output is rasterized with burned-in boxes        |
| Downstream results         | Real names are restored locally through the vault (SYNTHETIC types only)                                  |
| Filenames and PDF metadata | In scope. Annotated as `field` entities                                                                   |
| Claude Code silver annotation | Decided: exception EX-001 (§2.2). Labelled silver until verified (§9.4) |




### 10.2 Still open, with proposed defaults


| #   | Question                                                                                                                                | Proposed default                                                                                                                  |
| --- | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Names of treating practitioners and examining experts                                                                                   | SYNTHETIC. The role is annotated, so this can flip to KEEP later                                                                  |
| 2   | Lawyers, barristers, insurer staff, judges and registrars                                                                               | SYNTHETIC for lawyers and staff; KEEP for judicial officers                                                                       |
| 3   | Organisations by role                                                                                                                   | SYNTHETIC for employers, law firms, hospitals and practices. KEEP for statutory bodies, courts and government. **Insurers: open** |
| 4   | Locations                                                                                                                               | KEEP country and state. SYNTHETIC for suburb, city, street and incident site, using a same-state surrogate                        |
| 6   | Ages ("a 52-year-old")                                                                                                                  | KEEP                                                                                                                              |
| 8   | Organisational phones (13/1300/1800), organisational emails, company ABN/ACN                                                            | Follow the organisation's action                                                                                                  |
| 9   | This matter's claim, court and policy numbers                                                                                           | SYNTHETIC (critical)                                                                                                              |
| 10  | Provider and AHPRA numbers                                                                                                              | Follow the practitioner-name decision                                                                                             |
| 11  | Signatures, handwriting, photos, logos                                                                                                  | Annotate as regions. REDACT in PDF output. Not represented in text output                                                         |
| 12  | Quasi-identifiers: occupation, incident narrative, rare conditions, and also ethnicity, Indigenous status, religion and spoken language | Out of scope for v0; flag only                                                                                                    |
| 13  | People whose gender is unknown                                                                                                          | Use a unisex surrogate. Gold records gender only when the document gives evidence                                                 |
| 15  | Matter boundaries                                                                                                                       | All five documents are one matter (to confirm)                                                                                    |
| 17  | How system REVIEW outputs count in leakage                                                                                              | Primary: pessimistic (counted as leaked). Secondary: with human review                                                            |
| 18  | Who are A1, A2 and the adjudicator?                                                                                                     | A2 must be a different person from A1 (§9.3)                                                                                      |
| 19  | DOB window                                                                                                                              | `min_window_days` of 14 and the reference-date rule of §4.9.2. Confirm in the pilot                                               |
| 20  | May pseudonymized text go to an external tool (for example a cloud LLM) under the firm's policy and client terms?                       | The system never sends anything itself. The answer only decides how the re-identification feature is used                         |
| 21  | Vault custody: key storage (OS credential store or passphrase), backup and retention, who may call re-identification                    | OS credential store, backup by the firm, role-gated access                                                                        |
| 22  | Authorised by the project owner on 2026-10-01 (recorded in `docs/data_exceptions.md`). Does the firm need another signatory? | If so, add the name and date there before the silver pass |


---



## 11. Implementation sequence



### Evaluation foundation (starts right after approval)

**F0: Safety scaffold**

- Treat the plan in `plans/PLAN_REV2_parts/` as the plan of record.
- Set up the repo. Keep the existing repo and its GitHub remote, and do not change the remote:
  - `.gitignore` first, before any `git add`;
  - the pre-commit hook and a pre-push hook that reject confidential paths and PDFs;
  - a check that no PDF is tracked;
  - `.claude/settings.json` deny rules (the rules on `golden_dataset_docs/**` and `data/**` stay in force until S1; add `REDACTOR_DATA_ROOT` if the confidential folders move outside the repo);
  - `CLAUDE.md` containing the data-boundary and determinism rules.
- No commit or push unless the owner asks.

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
- Tesseract 5 is required. The recommended reference environment is a pinned Docker image; a native install also works for development. Pinned installs listed in §6 are pre-approved by the owner (2026-10-01) and recorded in the lockfiles and the build log; anything else is confirmed with the owner first.

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

- Pilot and schema v0.2 (after S1; the silver files are migrated to v0.2). Then: verification of silver, the independent A1 and A2 passes on the IAA documents, validators, recall audit, adjudication and freeze v1.0 (§9).

**S1: Silver pass (Claude Code, exception EX-001)** (build-time work; runs after F3 and before F4)
- **Preconditions:** the schema models exist at v0.1 (F1) and the guidelines v0.1 are written; page renders and OCR text exist (F2–F3); `docs/data_exceptions.md` records the authorisation; one model is chosen and stays fixed for the whole pass; and the deny rules on `golden_dataset_docs/**` and `data/**` are lifted for this task.
- **Run:** Claude Code annotates every page, plus the filename and PDF metadata fields, of every document, in document then page order. It writes `golden_dataset/annotations_raw/claude_silver/` in the annotation schema, with the model ID, run date, guidelines version, prompt hash and input hashes recorded in each file.
- **Checks:** the validators (§9.4) run on each file before any person sees it. A file that fails is re-run for that document only, and the failed run is kept in the log.
- **After:** the deny rules on `golden_dataset_docs/**` and `data/**` are restored and the exception record is closed in `docs/data_exceptions.md`. The silver files are migrated to v0.2 after the pilot, and verification (§9.4) follows.

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
- **Silver files:** they validate against the schema; none sits in `annotations/`; every silver file carries its model ID, run date, guidelines version, prompt hash and input hashes; promotion to `annotations/` fails without `verified_by`; scores on pages that are not verified carry the silver label.

**Real-data smoke run** (local, aggregate output only; run by the assistant under the owner's standing authorisation of 2026-10-01):

- `dataset register` finds 5 documents and 88 pages, and the SHA-256 prefixes match §1.2. The page classes match §1.2: 9 vector_outlined, 52 scanned and 27 text-layer, with doc_003 p1 flagged as a hybrid candidate.
- `extract` fills the OCR cache for all 61 OCR pages. OCR determinism: 5 pages × 20 runs, all identical.
- `run experiments/baseline.yaml` produces 25 of 25 identical runs, and the public report has zero leak-test hits.
- The `golden_dataset_docs/` hashes are unchanged at the end.
- After the silver pass (S1): `dataset validate` passes for every silver file, and the verification sample is drawn from the seed recorded in the manifest.
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

