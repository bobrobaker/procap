---
project: procap
goal: ProCap web demo — two-demo redesign + GUI restructure, verified and ready to commit
created: 2026-06-26
status: open
---

# Handoff — ProCap demo: two-demo redesign + GUI restructure (pre-commit)

**Branch/HEAD:** `master` @ `46afffe`. **7 files modified, UNCOMMITTED.** Demo served at
`http://127.0.0.1:8000` (http, not https). The immediate next step is `/eos` (commit).

## Goal
Land the ProCap web-demo redesign: two purpose-distinct interactive demos + a hierarchical
GUI. Work is complete and verified; it just needs committing.

## State — DONE **and verified** (tests 41/41, lint clean, both demos serve 200)
- **Two demos**, routed structurally by `RunView.mode` (conformance if a content audit exists,
  else note-taking):
  - **Demo A = conformance** (`runs/audit_demo`): qualify a recording against a provided SOP →
    confirm/deny each flagged divergence. 7 filled steps vs a 7-step SOP with planted
    out_of_order / missing_step / extra_in_doc. Default landing tab.
  - **Demo B = note-taking** (`runs/labeled_demo`): document a recording → editable draft SOP,
    retag golden↔dross, per-step notes, add off-screen steps. 7 golden steps + 3 dross.
- **Interactivity** — vanilla JS, **in-memory only** (no server write path): retag/notes/
  add-remove off-screen step (B); confirm/deny (A); **PDF via `@media print` + `window.print()`**
  over the live edited DOM (retagged-dross steps drop from the exported SOP; B's reviewed
  verdicts print, buttons don't).
- **GUI restructure** — 3 nested regions: **About** (what-it-does + inline golden/dross gloss +
  nested How-it-works/FAQ) → **demo tabs** → **one demo card** (header chips → interactive
  output LEADS → "How ProCap produced this" evidence collapsible holding keyframes/segments/
  accuracy). Golden/dross shrank to a one-line gloss; Overview block deleted (numbers → header
  chips); A/B relabeled so cards read A→B; stage-number badges dropped.
- **Confer fixes** (brain2 adversarial-PM, resolved): honesty/F1 proof promoted onto Demo B's
  intro first-screenful (it was absent from the default conformance view); stale FAQ "panel
  above" pointer corrected; About `why` trimmed to one sentence.

## State — NOT done
- **Not committed.** All 7 files are uncommitted on `master`. `/eos` does the commit.

## Next actions
1. **`/eos`** — mine-session → wrap-session → commit (user is doing this next).
2. **Tear down the idle brain2 PM agent:** `agent-spawn kill spawn-procap-gui-pm` (removes the
   tmux tile + worktree). Its job is done; the resolved confer thread is the provenance.
3. Optionally stop the demo server (`pkill -f "procap.cli serve"`).

## Key context
- **Files touched:** `procap/webdemo.py` (major restructure + interactivity + print CSS);
  `corpus/make_synthetic.py` (7-step Demo A clip: added heater/temp/logging);
  `corpus/make_audit_demo.py` + `corpus/synthetic/written_procedure_reordered.md` (7-step SOP
  with planted mismatches; flow intent avoids "setpoint" so the omission surfaces);
  `corpus/synthetic/labeled_demo.labels.json` (regenerated); `tests/test_extract.py` +
  `tests/test_procedure.py` (updated to the new 10-scene/9-keyframe content).
- **Decisions + why:**
  - **Vanilla JS, not a framework** — the interactive surface is small; keeps the demo's
    zero-dep/offline value prop intact. (User was open to deps; I chose vanilla and flagged the
    reasoning. Swap is contained if ever wanted.)
  - **In-memory + browser print-to-PDF** → no POST/write path at all (user's choice). Edits
    vanish on reload; the PDF is the durable output.
  - **A=conformance, B=note-taking, cards read A→B, default lands on A** (user: "swap the A/B
    words, keep the existing order"). The cards sort conformance-first, so swapping letters
    makes them read A→B without moving anything.
  - **Confer resolution:** A's honesty surface is coverage% + the method line (no F1 — it has no
    keep/drop accuracy); the F1 proof is a B-only concept and was promoted there.
- **Gotchas:**
  - `runs/` is gitignored — regenerate with `procap run corpus/synthetic/labeled_demo.mp4
    --against corpus/synthetic/written_procedure.md` then `python corpus/make_audit_demo.py`.
  - Keyframe lightbox overlays render at **page level** (not inside the evidence `<details>`),
    else a collapsed section hides the fixed-position overlay.
  - Spawning the brain2 architect: hit the MCP-trust gate (dismiss = option **3**); confer
    identity pinned to `brain2` in the brief (worktree basename `procap-gui-pm` would otherwise
    mismatch `between:`); long `--prompt` → use a short file-pointer prompt.

## Open decision
None blocking. Minor: tear down the spawned brain2 agent (recommend yes — job complete).

## Pointers
- `procap/webdemo.py` — the demo (RunView.mode, _render_about / _render_demo_block / _render_*).
- Confer provenance (resolved): `~/projects/brain2/handoffs/2026-06-26-confer-procap-gui-restructure.md`
  (+ `...-architect-brief.md`, residue — deletable).
- `docs/road.md` (phase roadmap), `docs/decisions/` (design calls + why).
