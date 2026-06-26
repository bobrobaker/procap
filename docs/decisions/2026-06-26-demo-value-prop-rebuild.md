---
doctype: decision
status: decided
date: 2026-06-26
---
# Demo rebuilt outside-in: value first, then proof, then details

## Decision

Re-anchor the web demo on the **user's questions in order** — (1) what is this and what
bottleneck does it kill, (2) would I want it / can I judge if it'd help me, (3) can I try and
inspect it — instead of on the pipeline's internal artifacts. The honest internals from the
earlier honesty pass (`2026-06-26-demo-and-honesty-pass.md`) are **kept but demoted** below the
value story. Page spine: **pitch → proof → how it works → details/FAQ.**

Provenance: confer thread
`brain2/handoffs/archive/2026-06-26-confer-demo-value-prop-rebuild.md` (brain2 as PM, driven by
a real human read of the live page).

### What shipped (`procap/webdemo.py`, stdlib-only, no new deps)
- **Value hero** at the top: a plain-language pitch ("procap watches a screen recording of you
  doing a task and writes the timed, step-by-step procedure for you — mis-clicks and dead time
  already trimmed"), a **trim story** with real counts ("From 15.0s of recording, procap dropped
  1 dead-end/idle stretch and wrote 4 timed steps"), an **input→output transform** card (only
  when a step has real text), and a record→run→review→export flow.
- **Buried-proof fix:** the landing now opens on a run with ground-truth labels so the accuracy
  overlay (the proof the keep/drop call works) leads, instead of the alphabetically-first run.
- **Clickable keyframe + step inspection:** a pure-CSS `:target` lightbox (no JS) — click any
  keyframe or step thumbnail to see KEPT→became-step-N / DROPPED-as-dross + the reason +
  confidence. Lets a skeptic audit any single call.
- **Progressive disclosure:** a collapsed "How it works" (the four stages in user language) and
  a **FAQ framed as the user's questions** (what videos? need a key? how accurate? what's it bad
  at? how do I use it?), with the honesty caveats as the answer to "what's it bad at."

## Why

- **The earlier honesty pass optimized the wrong axis.** It made a trustworthy *inspector* —
  but the one real human who read the live page said "I can't tell what this is or what the
  value proposition is." Correctness rigor on an artifact nobody can read is the wrong order;
  value legibility comes first. (brain2's frame challenge, not a metric.)
- **Never fabricate in the pitch.** The first hero rebuild fell back to a hardcoded "Start the
  feed pump" step title when no step had real text — which is exactly the *default offline user's*
  case (placeholder titles), reintroducing the fabricated-headline sin the honesty pass removed,
  in the most prominent pixel. Fixed by: showing only real step text in the transform, and
  leading with the **trim story**, which is deterministic and true offline for any video.
- **Stdlib-only held.** Clickable inspection and layering are a CSS `:target` lightbox and
  `<details>` — zero JavaScript, zero new dependencies, consistent with the minimal-deps ethos.

## Consequences

- The demo's primary frame is now the pitch; the per-stage inspector is the "inspect the
  example" evidence layer beneath it. The honesty surfaces (eval overlay, in-sample caveat,
  held-time accounting, method-labelled audit) are unchanged — only repositioned.
- No contract or pipeline change; this is presentation. Tests remain 37/37.
- Relationship to the honesty pass: this decision **reorders and reframes** that work, it does
  not reverse it — the internals stayed honest; they stopped being the headline.
