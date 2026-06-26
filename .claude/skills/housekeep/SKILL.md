---
name: housekeep
description: Sweep the system's transient stores for orphaned or over-horizon residue — unwrapped sessions, dead-session flag files, stale handoffs and confer threads, abandoned worktrees, queued upstream-candidates, global skill-farm drift — then auto-reap only the mechanical-and-safe class and propose the rest. Use when the user invokes /housekeep, says "do housekeeping", "tidy up", "check for orphaned/stale things", or as a daily cadence pass. Silent when clean. NOT a doc/summary-drift audit (that's the broader Tier-3 pass) and NOT a mining/codify pass.
---

# housekeep

You are running one on-demand sweep over the transient stores this system leaves
behind, reporting what has gone stale or orphaned and reaping only what is safe to reap
without a decision. The design mirrors the Tier-3 audit philosophy in
`method/tooling.md`: **report and propose; auto-apply only the mechanical class** — and
apply that same write-permission split to this skill itself.

Two invariants, both load-bearing:

- **Fail open.** Every probe is independent. A missing directory, an unreadable file, a
  non-git cwd, or a tool that errors → skip that one category silently and continue.
  Never abort the run, never delete on doubt.
- **Silent when clean.** This is meant to run daily. If every category comes back empty,
  print one line (`✓ housekeep: nothing stale`) and stop. No headers, no empty sections.

## The three tiers

Classify every finding into exactly one:

1. **Auto-reap** — mechanical, safe, *cannot lose information*. Do it, then report it as
   done. Only two things qualify (see F and B below). Nothing else is ever auto-applied.
2. **Propose** — a real change that needs a yes: deletion, archival, running a tool. List
   each with the specific action; act only on confirmation.
3. **Report-only** — surfaced for the user's judgment; this skill takes no action.

## The sweep

Run these probes (each guarded; on any error skip that category). Collect findings, then
present them grouped by tier.

### A — Unwrapped sessions → propose backfill

`archive/backfill.py --dry-run` already computes exactly the set of recent sessions with
no archive summary; do not reinvent that selection.

```bash
[ -f archive/backfill.py ] && python3 archive/backfill.py --dry-run 2>/dev/null
```

If it lists N sessions: **propose** `python3 archive/backfill.py` (optionally `--days`,
`--limit`). Don't run it unprompted — it makes model calls and costs tokens.

### B — Orphaned flag files

Flag files live in a machine-global dir shared by every concurrent session. A file is an
orphan only if its session is **dead** (its id is absent from the live registry
`~/.claude/sessions/*.json`). Never touch a live session's file.

```bash
d=~/.claude/session-flags; reg=~/.claude/sessions
for f in "$d"/*.md; do
  [ -e "$f" ] || continue
  id=$(basename "$f" .md)
  # live if a registry record names this id; indeterminate registry → treat as live
  if [ -d "$reg" ] && ! grep -rqsF "$id" "$reg"/*.json 2>/dev/null; then
    if [ -s "$f" ]; then echo "ORPHAN-NONEMPTY $f"; else echo "ORPHAN-EMPTY $f"; fi
  fi
done 2>/dev/null
```

- `ORPHAN-EMPTY` (dead session, nothing to mine) → **auto-reap**: `rm` it.
- `ORPHAN-NONEMPTY` (dead session, un-mined lessons) → **propose**: run `/mine-session`,
  which drains dead-session files by design. Never `rm` a non-empty flag file here — that
  would silently destroy the lessons the orphan-safety design exists to preserve.

### C — Over-horizon handoffs

Scan `$CMS_LANDING_ZONE/handoffs/` (if set) and `<repo>/handoffs/`, top level only
(exclude `archive/`). A handoff has `status: open` + a `created:` date. **Exclude confer
threads** (`type: confer` frontmatter — those are category D).

```bash
for base in "${CMS_LANDING_ZONE:+$CMS_LANDING_ZONE/handoffs}" "$(git rev-parse --show-toplevel 2>/dev/null)/handoffs"; do
  [ -d "$base" ] || continue
  for f in "$base"/*.md; do
    [ -e "$f" ] || continue
    grep -qs '^type: confer' "$f" && continue
    grep -qs '^status: open' "$f" || continue
    cr=$(grep -m1 '^created:' "$f" | sed 's/created:[[:space:]]*//')
    echo "OPEN $f created=$cr"
  done
done 2>/dev/null
```

Horizon depends on kind (`handoff/SKILL.md`): a **carry-forward** handoff is stale in
**days**; a **bridge** handoff is patient (~2 weeks). The frontmatter rarely says which,
so use conservative buckets and let the user resolve:

- `created:` > ~3 days → **propose** review ("carry-forward may have missed its pick-up").
- `created:` > ~14 days → **propose** as stale regardless of kind.

Offer the three resolutions for each, don't pick one: work died → delete; quietly became
a runbook → promote to a durable doc; live-but-stalled → do the pick-up or consciously
deprioritize-then-delete.

### D — Stale confer threads

Confer threads (`type: confer`, named `YYYY-MM-DD-confer-<slug>.md`) live in `handoffs/`
and archive to `handoffs/archive/` with an `archived:` stamp. A thread open ≳1 week means
the decision stopped mattering or was made out-of-band.

```bash
for base in "${CMS_LANDING_ZONE:+$CMS_LANDING_ZONE/handoffs}" "$(git rev-parse --show-toplevel 2>/dev/null)/handoffs"; do
  [ -d "$base" ] || continue
  for f in "$base"/*.md; do
    [ -e "$f" ] || continue
    grep -qs '^type: confer' "$f" || continue
    grep -qs '^archived:' "$f" && continue
    age=$(( ( $(date +%s) - $(stat -c %Y "$f") ) / 86400 ))
    [ "$age" -ge 7 ] && echo "STALE-CONFER $f age=${age}d"
  done
done 2>/dev/null
```

**Propose**, per `confer/SKILL.md` close-out: confirm the resolution actually landed in the
owning repo's spec/roadmap/decision shelf, then move the thread to `handoffs/archive/`
(add `archived: YYYY-MM-DD`). Never auto-archive — landing the outcome first is a judgment
call.

### F — Abandoned git worktrees

```bash
git worktree prune --dry-run 2>/dev/null   # admin entries whose dirs are already gone
git worktree list 2>/dev/null              # everything currently registered
```

- Anything `prune --dry-run` reports → **auto-reap**: `git worktree prune` (it only drops
  bookkeeping for directories that no longer exist — no working files are touched).
- A still-present worktree that looks abandoned (old, on a throwaway branch, no uncommitted
  changes — typical `/spawn` or Agent `isolation:worktree` leftovers) → **propose**
  removal. Don't remove a worktree with uncommitted changes without flagging that.

### E — Queued upstream-candidates

```bash
f="$(git rev-parse --show-toplevel 2>/dev/null)/handoffs/upstream-candidates.md"
[ -f "$f" ] && grep -c '^' "$f"
```

**Report-only.** Whether a queued candidate has already landed upstream can't be
determined locally, so surface only that the queue exists and roughly how full it is:
"N lines queued in upstream-candidates.md — mirror-back may be due." Resolving it is the
mirror-back sweep's job, not this skill's.

### G — Monition rows due for audit — DEFERRED

Not covered yet. Reaping monition rows (retire noise-heavy rows, widen/fold never-fired
ones) is the audit cadence in `method/takeaway-store.md`, whose intended engine is
`monition score` — a later Monition phase, **pending an ongoing refactor**. When that
ships, add a propose-tier section here that surfaces candidates by count (≥N noise & 0
helpful; or active & 0 firings past X days). Until then, print one line so it isn't
forgotten: `· G (monition row audit) deferred — pending monition score`.

### H — Global skill-farm drift

`~/.claude/skills/` is a symlink farm: each entry must be a symlink into a source repo
(this clone, or the author's dotfiles). Two drift modes rot silently — a skill authored
as a **real directory** here is tracked by no repo and lost on the next reinstall; a
**dangling symlink** means its source moved or was deleted. Skip the whole category if
the farm doesn't exist (a fork that never ran `bootstrap --link-global`).

```bash
farm=~/.claude/skills
if [ -d "$farm" ]; then
  for e in "$farm"/*; do
    [ -e "$e" ] || [ -L "$e" ] || continue
    if [ ! -L "$e" ]; then echo "LOOSE $e"          # real file/dir — tracked by nothing
    elif [ ! -e "$e" ]; then echo "DANGLING $e -> $(readlink "$e")"  # source gone
    fi
  done 2>/dev/null
fi
```

Both are **propose**, never auto-reap — each needs a judgment call and deleting either
loses information:

- `LOOSE` → propose: give it a tracked home, then symlink back. The home depends on the
  skill — forkable machinery goes in this clone's `.claude/skills/`; a personal or
  adviser-coupled skill goes in the author's dotfiles. Ask which; don't guess and don't
  `rm` it (that destroys the only copy).
- `DANGLING` → propose: repoint the link to the source's new location, or remove it if
  the skill was retired. Never assume "delete" — a moved source wants a repoint.

## Output

Group findings under the three tier headings, most actionable first. Auto-reaps are
already done — state what was reaped. Proposes are a numbered list the user can accept
selectively. Then the one deferred line for G. If nothing fired in any category, the
single silent-when-clean line instead.

## Anti-goals

- **Not an auto-deleter.** The only unattended actions are the two mechanical reaps (empty
  dead-session flag files; `git worktree prune` of already-gone dirs). Every other change
  is proposed.
- **Doesn't reimplement its dependencies.** It *calls* `backfill.py`, points at
  `/mine-session` and the mirror-back sweep — it never duplicates their logic.
- **Not the Tier-3 doc audit.** This reaps orphan/staleness residue. Drift between
  summaries and their sources, merge candidates, and always-on-layer leanness are the
  broader Tier-3 pass, out of scope here.
- **Not a mining pass.** It never reads orphaned flags into the store or codifies anything
  — that needs the judgment `/mine-session` and `/codify` carry.
