# Clean up branches, not the pool — Control Plane v0.6

## Three different states

`INTEGRATED` means the merge result is proven. `LANE_RELEASED` means the folder assignment is finished and safe to reuse. `CLEANUP_COMPLETE` means all Story branch/tracking refs in scope have been removed or confirmed absent. None of these states replaces another.

`lane-01` through `lane-N` folders are **always preserved** by the cleanup helper. A folder may remain detached on an older candidate. Story cleanup never runs `git worktree remove`, deletes lane folders, or resets them to the target.

## Normal sequence

Main/Integrator verifies the MR merge, source SHA, and target result. Ensure every Story lease is released through the pool. Publish important records on the records branch; check QA/dependency/retention needs. Fill `cleanup_checks` on the local manifest created by the pool: timestamp, `dependencies_clear/ref`, `processes_stopped/ref`, `records_published/ref`, and `retention_required=false` only when that is actually true. Flags record main's checks; they are not automatic proof.

```text
python tools/agentic/cleanup.py --task .agentic/local/tasks/ae-dev-a-STORY-101.json
python tools/agentic/cleanup.py --task .agentic/local/tasks/ae-dev-a-STORY-101.json --apply
```

Skill `/ae-cleanup STORY-101 --check` maps to helper preview; `/ae-cleanup STORY-101 --apply` maps to apply. Operators do not need to type this for routine closure; main follows the procedure after merge when standing cleanup policy is approved.

## What the helper evaluates

The manifest must represent the registered Story owned by this coordinator, with the exact work branch and MR/project/target. The helper reads open MRs including dependency source/target branches, verifies normal/FF/squash merge, confirms local/remote head has not changed since the candidate, and rejects target/protected/default branches. A branch checked out in any worktree is held; cleanup never changes that checkout.

Remote deletion uses the expected full head and rechecks before deleting; local refs are removed compare-and-swap. Exact tracking refs are pruned only when the remote Story branch is confirmed absent. No mass sweep, arbitrary force-delete, or cleanup of records/tags/another developer's branch. Conditional ref deletion exceptions exist only through the reviewed helper.

If `lane-01` has already been reused by STORY-104, cleanup of STORY-101 must not touch the lane folder or lease for STORY-104. It only verifies that no lease for STORY-101 remains active. If the old local Story branch gained a new commit, KEEP it; do not decide only from the previously merged MR.

## Held does not mean forgotten

QA retention, dependencies, unique work, permission failure, changed SHA, incomplete MR listing, or ambiguous squash proof results in KEEP/NEEDS_REVIEW/CLEANUP_PENDING. Main stores a `cleanup-report.md` with remaining resources, reason, owner, review trigger, actual target/candidate, and what was not done. Integration remains valid even if cleanup is delayed.

On resume or sprint/feature close, main checks this developer's pending cleanup. Status-only/check and stop do not delete anything. Closed sessions do not monitor. A deletion timeout requires reading remote state before retrying; partial outcomes are reported per resource.

## Migration and boundaries

Older manifest/task-worktree formats are not processed by pretending they are new. Read `MIGRATION.md`; never rename folders or edit schema numbers to bypass compatibility. Preserve unique source first. Reducing pool size or decommissioning a repository is separate maintainer work, not Story cleanup.

The helper cannot prove that every computer/process/QA activity has stopped or that records were correctly published. Main checks and organization controls remain necessary. Local tests use fixtures; kit development does not delete real project branches.
