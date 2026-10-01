# Annotation guidelines — v0.1.0

Tracked file, no PII. All examples are invented. Source: plan §3.4 (field vocabularies, span conventions), §3.5 (registry), §3.7 (taxonomy, roles).
These guidelines apply to every annotator: A1, A2, the adjudicator and the silver pass by Claude Code (EX-001). Version 0.2 is frozen after the pilot (§9.5).

## 1. What to annotate
Annotate **facts**, not decisions. Record what a span is (type, role, attributes). The action (KEEP / SYNTHETIC / REDACT / REVIEW) is derived from the policy file; set an `override` only with a written reason.

Every page is annotated in full for **all types below**, including the KEEP types (DATE, AGE, GENDER). A page is marked complete only when every mention on it is annotated.

| Type | Annotate | Do not annotate |
|---|---|---|
| PERSON | Every name of a real person: plaintiff, family, witnesses, co-workers, doctors, experts, lawyers, insurer and employer staff, court officers. Given names alone, surnames alone, initials with surnames | Honorifics (Mr, Mrs, Ms, Dr, Prof) and post-nominals (MBBS, FRACS) — keep them outside the span. Pronouns. Eponyms in medical terms ("Colles fracture", "Glasgow Coma Scale") |
| ORGANIZATION | Employers, insurers, statutory bodies (WorkCover Queensland, Medicare, Services Australia), courts, law firms, hospitals, practices, schools, other companies | Generic words ("the hospital", "the insurer") |
| ADDRESS | Street addresses, PO Boxes, Locked Bags, including suburb, state and postcode when written together | A suburb or city alone (that is a LOCATION) |
| LOCATION | Countries, states, cities, suburbs, streets without numbers, named facilities, incident sites. Set `granularity` | Locations inside an ORGANIZATION name |
| EMAIL, PHONE, URL | Every email address, phone or fax number, web address. Set `number_class` for phones (mobile, landline, 13, 1300, 1800, other) | |
| Identifiers | CLAIM_NUMBER (claim, reference and file numbers of this matter), COURT_FILE_NUMBER, POLICY_NUMBER, TFN, ABN, ACN, PASSPORT, DRIVER_LICENCE, VEHICLE_REGISTRATION, CENTRELINK_CRN, ACCOUNT_NUMBER (incl. BSB), MEDICARE, PROVIDER_NUMBER, AHPRA_REGISTRATION, IHI, MEDICAL_RECORD_NUMBER (MRN/URN), DVA_NUMBER. Annotate the number only, not its label | Page numbers, form codes, section numbers, legislation numbers, clinical scores |
| DATE | Every calendar date (full, month-year, year when it refers to an event). Set `date_role`: date_of_injury, examination, report, claim, treatment, other | Durations ("three weeks"), ages |
| DATE_OF_BIRTH | Every form of a person's date of birth, including "born in 1970" (`granularity=year`). Needs the person's canonical ID | |
| AGE | A stated age ("52-year-old", "aged 52", "52 years of age"). Set `stated_age`; when the age is tied to a date, set `age_ref_date` (ISO) | |
| GENDER | Explicit gender or sex words describing a person: male, female, man, woman, gentleman, lady, boy, girl, "Sex: M" | Pronouns, honorifics |
| NATIONALITY | A demonym, citizenship or country of origin **describing a person** ("an Indian national", "born in Vietnam", "migrated from Fiji"). Set `form` (demonym, citizenship, country_of_origin) | A country or demonym inside a proper noun ("Australian Taxation Office", "Commonwealth of Australia"): that belongs to the ORGANIZATION or LOCATION. Ethnicity, Indigenous status, religion, language: flag the containing mention `quasi_identifier` only |
| SIGNATURE, PHOTO | Handwritten signatures, photographs of people — as regions only (no text) | Printed names under a signature (those are PERSON) |

### 1.1 Clarifications (v0.1.0)
- **AGE span:** the whole phrase for "52-year-old" and "52 years of age"; only the number for "aged 52" and "Age: 52". Link the person with `canonical_id` when clear.
- **DATE roles:** imaging, investigation and other doctors' letter dates are `other` unless the text says report/examination/injury/claim/treatment. A date without a year ("in February") is not annotated.
- **Repeated headers and footers** (letterhead, practice address, running name header) are annotated on every page where they appear.
- **Logos** that spell an organisation name are ORGANIZATION mentions; post-nominal credentials ("MBBS FRACGP") are not.
- **Organisation addresses, phones and emails** are annotated like any other; the policy decides what to replace.

## 2. Span conventions
- Annotate the **maximal unit**; gold spans never overlap. An email containing a name is one EMAIL with `attributes.contains_person`; "Smith Lawyers" is one ORGANIZATION with `contains_person`.
- Exclude honorifics, post-nominals and a trailing possessive "'s".
- A multi-line entity gets one region per line. An entity that crosses a page break is split into two mentions sharing a `canonical_id`, both flagged `split_page`.
- Document-level fields (`filename`, `pdf.title`, `pdf.author`, `pdf.subject`, `pdf.keywords`, `xmp`) are annotated like page text, with `page: null` and `field` set.

## 3. OCR text
Anchor every mention to the **reference text source** of its page (the OCR or text-layer text). If OCR misread the mention, anchor the OCR surface and put the correct reading in `text`, with the flag `ocr_degraded`. If OCR missed the mention entirely, annotate a region with the correct `text` and certainty `probable`.

## 4. Canonical entities and roles
- Every PERSON and every DATE_OF_BIRTH has a `canonical_id` (`matter_001/person_NNN`). All forms of one person ("Jane Example", "Jane", "Ms Example", "EXAMPLE, Jane", "J. Example") share it. Organisations get `matter_001/org_NNN`, other entities optional IDs.
- The registry records each canonical person's role, gender (female, male, non_binary, unknown) with evidence (honorific, pronoun, stated), and date of birth when the documents state it.
- PERSON roles: plaintiff, plaintiff_family, witness, co_worker, treating_practitioner, examining_expert, legal_representative, insurer_representative, employer_representative, court_officer, other.
- ORGANIZATION roles: employer, insurer, statutory_body, court_tribunal, government, law_firm, hospital, medical_practice, education, other.
- PERSON `name_form`: full, given_only, surname_only, title_surname, initial_surname, surname_comma_given, initials. `casing`: title, upper, lower, mixed.

## 5. Flags and certainty
- Flags: `handwritten`, `ocr_degraded`, `split_line`, `split_page`, `partially_illegible`, `quasi_identifier`. `handwritten` and `partially_illegible` need a note.
- Certainty: `certain`, `probable`, `uncertain`. Uncertain mentions are excluded from precision and recall and reported separately.

## 6. Silver pass (EX-001) specifics
- Output only under `golden_dataset/annotations_raw/claude_silver/`, `annotator: claude_silver`, `mode: silver`, provenance origin `silver`, and the `silver` block (model ID, run date, guidelines version, prompt hash, input hashes).
- Silver is never gold. It becomes gold only page by page, after a person verifies it (plan §9.4).
