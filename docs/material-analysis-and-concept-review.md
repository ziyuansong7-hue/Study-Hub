# Material analysis and concept review

This iteration separates raw source storage from the user-controlled course concept map.

## Flow

```text
upload
  → type detection (user may override)
  → quick or structured parsing
  → hidden, paginated source chunks
  → grounded concept candidates
  → optional independent provider verification
  → human confirm / merge / ignore
  → formal course concepts
  → Focus or multi-concept Practice session
```

`Material Analysis Summary` describes only the uploaded material. It never claims that a
textbook, slide deck, assignment, or exam covers an entire course. Structure checking is
optional and only meaningful for materials with a chapter, unit, or syllabus structure.

## Human-in-the-loop rules

- Candidates can recommend `create`, `merge`, `rename`, `ignore`, or `review`.
- Each recommendation stores importance, confidence, chapter/unit, prerequisites, reason,
  and source chunk ids.
- Evidence is collapsed by default. Raw chunks are never rendered on the Materials page.
- Confirmation is idempotent. Repeating a request does not create a second course concept.
- A configured external provider is used only when the course explicitly allows external AI.
  Otherwise verification is labelled `local_heuristic`.
- Focus Mode accepts exactly one concept. Practice Mode accepts zero or more concepts; an
  empty selection means the full course.
- Practice scopes can be saved by name, reloaded into the session builder, updated by using
  the same name, and deleted.

## Current boundary

The structured analyzer is deliberately lightweight. It detects chapter/unit structure and
samples long documents, but it does not run OCR, semantic clustering, or claim whole-course
coverage. Every AI recommendation remains editable and requires a user decision.
