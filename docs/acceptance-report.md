# Acceptance report

Date: 2026-09-27

## Delivered in this iteration

- Multi-course isolation with course language/terminology settings and per-course external-AI consent.
- Material categories/year, raw-file retention, repeated-header/footer cleanup, reprocessing/deletion, and explicit scanned-PDF failure.
- AI concept candidates with user confirmation before practice.
- Moderated Question Bank: pending/approved/rejected/disabled, editing, rewriting, and harder/easier variants.
- Explainable 0–100 difficulty score, Level 1–5, six dimensions, estimated time, independent review, and optional uploaded-past-exam calibration.
- Focus/Practice/Review sessions that use approved questions only, adaptive levels, recent-question penalty, skip support, and prerequisite child-session return flow.
- Deterministic grading for objective questions; transparent tiered grading for short answers and
  derivations. Clear rubric matches are graded locally, ambiguous wording can be arbitrated by an
  authorized external semantic model, and unavailable or inconclusive grading is marked for review
  without reducing mastery.
- Equivalent induction terminology (for example, `inductive case`) receives credit, partial answers
  receive partial credit, and semantically correct non-keyword wording can be routed to DeepSeek.
- Post-submission answer explanation, concise evidence, structured grading breakdown, mastery/state updates, and idempotent attempts.
- Persisted study-task timer and automatic minute-target completion.
- Personal Task Center with inbox, overdue, today, future, completed, edit, priority, category, postpone, reopen, and delete.
- SQLite backup/validated restore with safety copy; archived-course deletion; historical concept-state backfill.
- Alembic baseline plus repeatable backward-compatible local initialization.
- Debug-reloader scheduler guard, JSON logs, bilingual UI additions, documentation, and expanded tests.
- Configurable 128 MiB uploads and tolerant page-by-page PDF parsing for recoverable damaged cross-references and embedded CFF fonts.
- Type-aware material analysis (`textbook`, syllabus, slides, tutorial, assignment, past exam, supplementary reading) with editable automatic classification and Quick/Structured modes.
- Raw chunks removed from the Materials page; the dedicated source view is collapsed, searchable, and paginated.
- Independent Concept Review workspace with source-grounded action/reason/confidence, provider labels, filters, AJAX single decisions, multi-select verification/confirmation/ignore/classification/merge, and idempotent writes.
- Optional, upload-scoped Material Analysis Summary; it never claims whole-course coverage.
- Multi-concept Practice scopes with weak/due/chapter shortcuts while Focus Mode enforces exactly one concept.
- A separate Practice page keeps session setup and answering distinct; the selected multi-concept
  scope is preserved when the session starts.
- Named Practice scope presets (for example, `Midterm 1`) that can be loaded, updated idempotently, or deleted.
- Global Today/Planner information architecture plus distinct per-course workspaces; switching courses now changes materials, readiness, questions, tasks, and observed progress instead of only changing a label.
- Content-quality classification for source chunks; table-of-contents/layout/noise fragments remain inspectable but are excluded from concepts, retrieval, citations, and question generation.
- Textbook Chapter boundaries rebuilt from body pages rather than printed table-of-contents entries.
- Question provenance policies (`reference_guided`, `material_strict`, `exam_style`), persisted generation provider, learning objective, solution steps, quality status, and quarantine flags.
- On-demand session question generation and a bounded SQLite-first retriever that avoids loading a whole textbook into Python memory.

## Verification performed

The final automated verification commands and observed outcomes:

```text
python -m compileall ...                              passed
ruff check .                                          passed
python scripts/check_i18n.py                          341 static keys; no missing English/Chinese keys
pytest -q                                             54 passed
pytest -m "not e2e" --cov --cov-report=term-missing  52 passed; 73.17% coverage
pytest -m e2e                                        2 passed with Chromium
alembic upgrade head (fresh SQLite database)          revision 0005_content_quality (head)
python -m pip_audit -r requirements.txt               no known vulnerabilities found
```

Manual browser UAT also verified the user-visible critical path:

- Practice Mode accepts multiple selected concepts and opens a dedicated answering page.
- `base case and inductive case` is accepted locally with a score of 100%.
- A semantically correct answer that deliberately avoids the configured keywords is routed to
  DeepSeek when the course has external-AI consent; the page visibly reports DeepSeek as the actual
  grader and awards semantic credit.
- UAT-only sessions, attempts, mastery records, and agent logs were removed after verification, and
  the affected concept state was recomputed.

The automated suite uses isolated temporary SQLite databases. The content repair was also run against the user's backed-up local database to rebuild the imported textbook structure and quarantine legacy broken questions.

Automated coverage includes mastery increase, low-confidence/guessing limits, prerequisite action, missing-evidence disclosure, duplicate attempt IDs, duplicate review prevention, FakeProvider REST flow, MCP structured errors, all Web pages, material upload idempotency, provider retry/fallback, future/overdue/inbox to-dos, timer persistence, objective grading without an LLM, approved-only sessions, difficulty scoring/adaptation, evidence precision, Windows-safe SQLite backup, hidden/paginated source chunks, repeatable material metadata refresh, idempotent bulk concept confirmation, multi-concept Practice scoping, table-of-contents rejection, extraction-fragment quarantine, bounded large-corpus retrieval, Chinese lexical retrieval, and on-demand question generation for an empty course.

## Acceptance caveat

“University exam difficulty” is implemented as an explicit heuristic plus optional calibration from user-uploaded past exams. It is intentionally not presented as a University of Waterloo certification or a guarantee that generated questions match a specific instructor's exam. Question Bank batches require human approval. Session-time generation is allowed only after the deterministic quality gate and is marked with an auto-approval audit note.

## Remaining limitations

One local student, synchronous question generation, no OCR, lexical rather than semantic retrieval,
limited symbolic-math equivalence checking, no authentication, and no production WSGI deployment.
Automated browser smoke tests and manual UAT cover the primary workflow, but a dedicated assistive-
technology accessibility audit has not yet been performed. The next highest-value engineering step is
background question generation with visible progress and cancellation.
