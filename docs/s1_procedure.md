# S1 silver-pass procedure (EX-001)

Tracked file, no PII. Its bytes, together with `docs/annotation_guidelines.md`, form the "prompt" whose SHA-256 is recorded in every silver file.

1. `redactor silver packets --doc <id>` writes, under `golden_dataset/annotations_raw/claude_silver/work/<id>/`, one line-numbered text file per page (the page's reference text source, lines as extracted) and, for OCR and hybrid pages, a view image at 120 dpi. Document-level fields go to `fields.txt`.
2. The annotator (Claude Code, one model for the whole pass) reads each page's text and, where present, its view image, and writes proposals to `work/<id>.proposals.json`. Each proposal names the page, the 1-based line where the mention starts, the exact surface text as it appears in the reference text (newlines included for multi-line mentions), the occurrence number on that line, the type, role, canonical key, attributes, flags, certainty and, when OCR misread the mention, the corrected `gold` text. Region-only items (signatures, photographs) give a box as page fractions.
3. `redactor silver build --doc <id>` converts proposals deterministically: it locates each surface in the reference text, derives regions from the word boxes (one per line, partial words apportioned by character), derives the action from the policy, fills identifier checksums, sorts entities by (page, y0, x0, type) and numbers them, attaches the silver block, validates, and writes `claude_silver/<id>.ann.json`. Failed builds are logged in `work/runs.jsonl` and re-run for that document only.
4. `redactor silver registry` builds the silver canonical registry from `work/registry.proposals.json`.
5. Nothing is printed except counts, IDs and issue codes. Silver files are frozen once written; a new run makes a new file.
