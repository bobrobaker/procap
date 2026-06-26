---
name: architecture-width-scan
description: Audit architecture for AI-executor pain: context width, ownership ambiguity, coupling, source drift, weak contracts, and validation gaps. Use for maintainability, refactor, coupling, architecture-debt, context-width, or AI-maintenance audits — NOT for an isolated bugfix or style-only refactor.
---

# Architecture-Width Scan

Use for maintainability, refactor, coupling, architecture debt, context-width, or AI-maintenance audits.

Suggest/run when:
- change spans several files or subsystems
- a generic layer absorbs feature/domain assumptions
- source-of-truth duplicates across code/config/tests/registry/policy
- helper/state surfaces accumulate unrelated or one-off responsibilities
- repeated executor confusion, broad greps, or fragile fixes suggest architecture pain

Do not run for:
- isolated bugfix with clear owner and focused test
- local cleanup with no cross-boundary implications
- style-only refactor
- a long file alone, unless length causes discovery/ownership/review pain

## Goal

Find architecture that makes future AI-executor changes require excess context, guessing, coordination, or recovery work.

Optimize for bounded safe execution: small read sets, clear ownership, explicit contracts, localized tests, reviewable diffs.

## Scan Lens

Ask:

> "For the next likely change, what must an executor read, infer, edit, and validate to avoid breaking hidden invariants?"

Default scan scenarios:
- add one feature/concept
- change one policy/scoring rule
- add one action/type/protocol variant
- fix one resolver/execution behavior
- add one testable domain exception

Pain categories:
- **Discovery:** hard to find owner/source-of-truth
- **Ownership:** unclear who owns rule/identity/behavior
- **Context:** one change requires broad simultaneous reads
- **Coordination:** one feature requires synchronized subsystem edits
- **Contracts:** strings/loose fields encode protocols
- **Invariants:** important rules live away from edit surface
- **Validation:** only broad runs catch local mistakes
- **Observability:** traces/logs insufficient to localize failures
- **Review:** large diffs mix unrelated concepts

## Look For

- broad modules with unrelated responsibilities
- generic layers knowing feature-specific names/rules
- duplicated constants, identities, priority lists, config, registry facts
- helper/state surfaces accumulating one-off fields/functions
- stringly protocols crossing boundaries without typed wrappers/builders
- awkward dependency direction/imports
- policy/scoring code hardcoding domain identity
- resolver/generator/orchestrator owning feature-specific behavior
- tests requiring broad setup for local behavior
- large files where executor must inspect unrelated code to edit one concept

For AI-maintained code, many small owner files plus an explicit index/registry often beat one large file. The index gives discovery; owner files bound context.

## Preferred Shapes

- a feature/concept owns its behavior, constants, local helpers, policy profile
- shared helpers only for repeated structure across unrelated owners
- generic layers orchestrate through hooks; no feature-specific branches
- a registry maps identity to owner without duplicating internal rules
- static metadata has one static source; runtime behavior lives with the owner
- feature-specific state moves into typed substructures/accessors where useful
- policy consumes owner-provided profiles, not central name lists
- dependency direction: orchestration calls domain hooks; domain avoids internals

## Procedure

1. Use user-supplied scan scenarios; otherwise test against the default scenarios in "Scan Lens."
2. Trace the required read/edit/test path; separate required reads from insurance reads.
3. Mark the pain category.
4. Propose a target ownership model: who owns rule, identity, state, behavior, tests.
5. Sequence the backlog: dependency-enabling seams first, then local migrations.

## Output

### Executive Summary
Highest-leverage problem + recommended direction.

### Hotspots, Priority Ordered
For each:

- **Priority:** P0 frequent executor mistakes / P1 common cross-file work / P2 drift risk / P3 local cleanup
- **Pain category:** discovery / ownership / context / coordination / contracts / invariants / validation / observability / review
- **Current shape:** concise
- **Executor pain:** why future agents struggle
- **Failure mode:** likely bad patch
- **Target shape:** safer ownership
- **Backlog task:** imperative title + one-sentence goal
- **Edit surface:** likely files
- **Acceptance:** test/observable result
- **Risk:** invariant to preserve
- **Dependencies:** what must happen first

### Sequencing
Dependency order; keep each task to one focused session.

### Non-Goals
Tempting refactors to avoid now.

### Open Questions
Only questions that materially affect architecture or sequencing.

## Avoid

- refactoring because a file is merely long
- merging files to reduce navigation
- splitting without an index/registry
- generic abstraction before a repeated shape exists
- one-off behavior in shared helpers
- dynamic auto-registration unless an explicit registry is worse
- hiding source-of-truth in policy/tests/orchestration
- mixing move-only cleanup with behavior changes
