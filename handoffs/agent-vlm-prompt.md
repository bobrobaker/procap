You are an autonomous implementer working in a git worktree of the `procap` project. Read
`CLAUDE.md`, `README.md`, `road.md`, and `docs/decisions/2026-06-26-pipeline-and-contracts.md`
first to understand the architecture. procap turns a screenshare of a technical GUI into a
time-estimated written procedure via 4 stages: extract → golden → procedure → audit.

## Your job: implement the VLM (vision-LLM) enhancement layer

Heuristics are the always-on baseline; the VLM only *enriches* when `ANTHROPIC_API_KEY` is
set. There is NO API key in this environment, so you implement the real calls AND unit-test
them with a mocked `VLM.ask`. The keyed path must be correct-by-construction and covered by
tests; the offline path must remain unchanged and keep passing.

Implement these three currently-stubbed VLM branches (each has a `TODO(stage-... agent)`):

1. `procap/golden.py::refine_with_vlm` — for each segment the heuristic marked with low
   confidence (or all ambiguous ones), show the VLM the segment's keyframe images and ask
   whether it is a consequential action or an abandoned/inconsequential one; update
   `kind`/`confidence`/`judged_by="vlm"`. Keep the heuristic result as the prior; only flip
   on a confident VLM judgment. Load keyframe paths from the `Keyframe` objects.
2. `procap/procedure.py::_describe` — show the golden segment's keyframes and ask for a
   concise imperative step **title** + 1–2 sentence **description** of the action performed.
   Fall back to the `[fill in]` placeholder on any error.
3. `procap/audit.py::audit` (the `if vlm.available:` branch) — replace the positional
   baseline with **semantic alignment**: for each generated step, ask the VLM which written-doc
   step (if any) describes the same action; emit `missing_step` when none matches,
   `out_of_order` when the matched index is out of sequence, `under_documented` when matched
   but thin. Keep the function signature and return type identical.

## Hard constraints

- Touch ONLY these files: `procap/vlm.py`, `procap/golden.py`, `procap/procedure.py`,
  `procap/audit.py`, and a NEW `tests/test_vlm.py`. Do NOT touch `procap/extract.py`,
  `procap/imageutil.py`, `procap/model.py`, `procap/eval.py`, the synthetic corpus, or the
  existing test files (another agent owns robustness/corpus work — avoid merge conflicts).
- Do NOT change the heuristic baselines or the existing public function signatures. The VLM is
  additive and guarded by `vlm.available`.
- Parse model replies robustly: ask the model to return a small JSON object and tolerate extra
  prose around it (regex-extract the JSON). Never let a VLM parse error crash the pipeline —
  fall back to the heuristic/placeholder and continue.
- `tests/test_vlm.py`: monkeypatch `procap.vlm.VLM.ask` (and set `available` True via a stub)
  to return canned replies, then assert each branch updates segments/steps/findings correctly.
  Also assert that with `available=False`, behavior is unchanged from the heuristic baseline.

## Setup & validation

- Create a venv in your worktree: `python3 -m venv .venv && .venv/bin/pip install -e .[dev]`.
  `ffmpeg`/`ffprobe` are already on PATH. Generate the test clip:
  `.venv/bin/python corpus/make_synthetic.py`.
- The full suite MUST stay green: `.venv/bin/python -m pytest -q`. Add your new tests to it.
- When done, commit on your branch with a clear message (do not push). Leave the worktree
  with all tests passing.

End your response with a `## Takeaways` block: decisions made, gotchas hit, lessons worth
keeping (especially anything about the VLM reply format or parsing).
