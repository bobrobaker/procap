# procap — tech-debt shelf

Project-local deferred work: refactors, architecture concerns, "fix later" items spotted
while editing. One row per item, append-only; check off when done, delete when it stops
mattering. Not the roadmap (planned work) or `docs/decisions/` (calls made).

**Capture trigger:** after a logical chunk, make one pass over the functions you just
edited and append anything deferred here, with enough locus to act on it cold.

**Row format:** `- [ ] path:symbol — observation (why deferred)`

## Shelf

- [ ] procap/extract.py:sample_frames — re-decodes the whole video to PNGs on disk; fine for
  short clips, wasteful for long recordings (defer streaming/selective decode until a real
  long-video case exists).
- [ ] procap/golden.py:classify — over-segments real screencasts: one golden segment per durable
  *visual* change with no semantic grouping, so pan/zoom/panel-toggle each become a "step" (29
  golden steps for a 3-min KiCad clip, see docs/decisions/2026-06-26-real-corpus-findings.md).
  This is the VLM-refinement's job (merge/relabel inconsequential changes) — NOT a threshold fix;
  raising change_threshold drops genuine actions too. Defer to the golden/VLM workstream.
