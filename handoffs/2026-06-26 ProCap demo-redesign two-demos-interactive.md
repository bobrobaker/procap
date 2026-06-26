# Handoff — ProCap demo redesign: two distinct demos, interactive, PDF export

**Date:** 2026-06-26
**Branch/HEAD:** `master` @ `a38f49c` (clean, all committed). Demo served at
`http://127.0.0.1:8000` via `procap serve`. Tests 41/41, lint clean.
**Goal:** redesign the web demo per user feedback — from a single read-only inspector into
**two purpose-distinct, interactive demos** with categorization controls, blank segments,
and PDF export. This is a scope expansion (read-only → stateful editor) — read the Open
decisions section before building.

## Where things are now (grounding)
- `procap/webdemo.py` — the demo (stdlib `http.server`, `procap serve`). Currently
  **read-only**: renders run artifacts as HTML + a small progressive-enhancement JS block
  (scroll restore, `:target`/JS lightbox, persistent hero collapse). No write path.
- Two runs exist today (both regenerable; `runs/` is gitignored):
  - `labeled_demo` — 4 golden steps, has `.labels.json` → accuracy overlay. Built by
    `procap run corpus/synthetic/labeled_demo.mp4 --against corpus/synthetic/written_procedure.md`.
  - `audit_demo` — 3 filled-intent steps + lexical content audit. Built by
    `corpus/make_audit_demo.py` against `corpus/synthetic/written_procedure_reordered.md`.
- Synthetic clips come from `corpus/make_synthetic.py` (committed; `.mp4` is gitignored).
- Contracts: `procap/model.py` (`Keyframe`, `Segment`, `ProcedureStep`, `AuditReport`,
  `held_seconds`, `AuditReport.method`). Stages hand off via on-disk JSON (`procap/run.py`).

## The feedback to implement (next session)

### 1. Copy: objective, not self-referential  *(also codified in CLAUDE.md)*
Strip the meta-narrative from the demo. Remove lines like "don't take the pitch on faith —
here's a real run…", "Inspect the example, stage by stage", "judge it yourself". The
honesty/seam-exposure is the *principle behind* the build; it must stay hidden. Present the
data plainly. Sweep `webdemo.py` for first-person/defensive framing and neutralize it.

### 2. Naming: ProCap  *(codified in CLAUDE.md)*
User-facing prose/UI uses **ProCap** (capital P, C). Package/module/CLI stay lowercase
`procap`. Update the demo masthead, hero, FAQ, decision-doc prose where it's the product
name — NOT code identifiers.

### 3. Define golden/dross at the top of the demo
A short plain definition near the top (newcomer's first contact), per CLAUDE.md convention.

### 4. The two demos are DIFFERENT PRODUCTS — explain each and its purpose
This is the core of the redesign. Today both runs look like the same inspector; they must
read as two distinct workflows, each explained:

- **Demo A — "document an existing procedure → SOP" (note-taking mode).**
  You have a recording of a task you already do and want to write up. ProCap **guesses
  golden/dross**, and the GUI **helps you add notes for each part** and **easily retag** a
  segment if the guess is wrong. Output: a written SOP. (Maps to today's `labeled_demo`.)

- **Demo B — "qualify against a provided SOP" (conformance mode).**
  You are performing a *provided* SOP to qualify/validate it. ProCap **matches your
  recording against the SOP** and **reports what parts don't match up**; then you
  **confirm or deny** each flagged segment. (Maps to today's `audit_demo`.)

### 5. Both demos need ≥ 7 steps (currently 3–4)
- New synthetic content with **7 real steps** for each demo. Demo A: a richer
  `make_synthetic.py` clip (more distinct actions) + labels. Demo B: a **7-step filled
  procedure + a 7-step SOP doc with planted mismatches** (extend `make_audit_demo.py` and
  `written_procedure_reordered.md`).
- **Demo B may keep "doc steps matched" as a `%`** (user explicitly OK'd it now — at 7 steps
  it no longer reads as a shallow 2/3). Note: earlier feedback had us drop the % for a count;
  this REVERSES that for the matched metric. Demo A is about retag/notes, not a match %.

### 6. Interactive controls (the big shift: read-only → editor)
- **Categorization buttons** — retag a segment golden↔dross (Demo A); confirm/deny a
  match-flag (Demo B). Edits must **persist** (see decisions).
- **"Add blank segment" button** — insert a step with **no screen frame** (for off-screen
  work the recording didn't capture), notes-only.
- **Per-segment notes** editing (Demo A's "put notes for each part").
- **Download as PDF** — **screen frame + notes for each step**, NOT markdown. (PDF, see
  decisions.)

## Open decisions — resolve BEFORE building (this is no longer a static page)
1. **Persistence / write path.** Retags, notes, confirm/deny, blank segments need to be
   saved. Stdlib `http.server` *can* take POST → write back to the run's `segments.json` /
   `procedure.json`. Decide: write back to artifacts (keeps "artifacts are source of truth")
   vs. a separate edits file. Recommend: POST endpoints that patch the run JSON; keep the
   pipeline re-runnable (an edit shouldn't be clobbered by a re-run — maybe an `edits.json`
   overlay layered at render time).
2. **Client JS.** Interactivity (buttons, inline note editing, optimistic retag) needs real
   JS beyond progressive enhancement. The "minimal-JS" posture was a choice, not a hard
   rule; confirm it's fine to add a modest vanilla-JS layer (still no framework/deps).
3. **PDF export.** Stdlib can't make PDFs cleanly. Options: (a) **browser print-to-PDF** —
   a print-stylesheet (`@media print`) + a "Save as PDF" button calling `window.print()`;
   zero dependency, but goes through the browser's print dialog. (b) a Python PDF lib
   (`fpdf2`/`reportlab`/`weasyprint`) — breaks the minimal-deps ethos; would need explicit
   sign-off. **Recommend (a)** first (no dep, screens+notes lay out fine in print CSS).
4. **Two demos: routing.** Keep the run-selector model (two runs) but relabel/reframe as
   "Demo A / Demo B" with their distinct explanations and distinct control sets (Demo A =
   retag+notes; Demo B = confirm/deny match). Don't present them as the same inspector.

## Key decisions already made (don't relitigate)
- ProCap (prose) vs `procap` (code) split — codified in CLAUDE.md.
- Confidence is now a real per-segment value (`golden.py`, log-scaled, preserves the 0.8 VLM
  gate); audit carries `AuditReport.method` {count,lexical,vlm}; `held_seconds` accounting —
  all landed at `a38f49c`. The redesign builds on these, doesn't undo them.
- Minor known gap: `classify` uses its default `change_threshold` (0.0025), not the run's
  `meta.change_threshold`. Equal for the demo; thread `meta`'s value through if it matters.

## First moves for next session
1. Re-read this + `docs/decisions/2026-06-26-demo-value-prop-rebuild.md` and
   `…-demo-and-honesty-pass.md` for the why behind current copy.
2. Get the user to settle the 4 Open decisions (esp. PDF approach + persistence shape) —
   present options, don't assume. (`brain2` confer is available if you want an adversarial
   design pass, but the user may just decide.)
3. Then: 7-step content first (both demos), then the two-demo reframe + copy sweep + naming,
   then interactivity, then PDF. Keep tests green; `procap serve` to verify each slice.
