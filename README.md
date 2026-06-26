# procap

Turn a **screenshare of a technical GUI** (operating a machine, a lab instrument, a control
panel) into a **time-estimated written procedure** — and audit an existing written procedure
against what the video actually shows.

## Pipeline

```
video ──extract──> keyframes ──golden──> golden/dross segments ──procedure──> procedure.md
                                                              └──audit──> audit.md (vs a written doc)
```

1. **extract** (`procap/extract.py`) — sample the video and emit a keyframe at every moment the
   UI *durably changes* (changed-pixel fraction past a threshold, with sub-dwell transients and
   mouse-wander filtered out). A screen recording doesn't encode clicks, so "moment of change"
   is the click proxy.
2. **golden** (`procap/golden.py`) — split the keyframe timeline into **golden** (consequential
   actions) vs **dross** (mis-clicks reverted, mouse wander, dead time). The baseline detects a
   *revert* — a later keyframe returning to an earlier state means the excursion between was
   abandoned. A vision LLM re-judges ambiguous segments when available.
3. **procedure** (`procap/procedure.py`) — one step per golden action, with real durations from
   the timestamps and `[fill in]` slots inviting you to annotate intent. The VLM writes step
   titles/descriptions when keyed.
4. **audit** (`procap/audit.py`) — compare the generated procedure to an existing written doc;
   flag missing / out-of-order / under-documented steps.

**Heuristics are the always-on baseline; the vision LLM only enriches** (set `ANTHROPIC_API_KEY`).
Everything runs and is tested with no key. See `docs/decisions/2026-06-26-pipeline-and-contracts.md`.

## Usage

```bash
.venv/bin/python -m procap.cli run path/to/screencast.mp4 --against written_procedure.md
# or stage by stage:
.venv/bin/python -m procap.cli extract screencast.mp4      # -> runs/screencast/keyframes.json
.venv/bin/python -m procap.cli golden  runs/screencast     # -> segments.json
.venv/bin/python -m procap.cli procedure runs/screencast   # -> procedure.md
.venv/bin/python -m procap.cli audit   runs/screencast --against doc.md
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]      # needs ffmpeg + ffprobe on PATH
.venv/bin/python corpus/make_synthetic.py   # generate the labeled test clip
.venv/bin/python -m pytest -q
```

## Testing strategy

A **synthetic, ground-truth-labeled** screencast (`corpus/make_synthetic.py`) scripts the noise —
a mouse-wander stretch and a wrong-tab-then-revert excursion — so tests can assert the classifier
recovers exactly the golden actions (`procap/eval.py` scores F1). A real downloaded screencast
(gitignored, `corpus/real/`) is the robustness check.

procap is built on a forkable context-management system; `method/`, `docs/decisions/`, and the
`/`-skills are that machinery. See `CLAUDE.md`.
