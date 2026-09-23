# Fixed reusable lane pool — technical guide v0.7

## Model

By default, seven linked-worktree workers live at `.claude/worktrees/lane-01` through `lane-07`, plus the existing coordinator checkout. `pool.size` can be set from 2–32 before initialization; the number of active Builders is controlled separately. A backlog with 1,000 Stories does not create 1,000 folders. There are no permanent Builder/Reviewer folders: roles change according to need.

```text
C:/Projects/RequestPortal/                 branch ae/records/dev-a/sprint-01
  .git/agentic/pool.json                   local registry, not a team file
  .claude/agents/                          role definitions
  .claude/worktrees/lane-01/               Story branch or detached snapshot
  .claude/worktrees/lane-02/               another exact snapshot
  ... lane-07/
  .agentic/local/tasks/                    local per-Story manifests
  .agentic/local/evidence/                 local logs/diffs
  docs/agentic/records/dev-a/STORY-101/    versioned records
```

In a linked checkout, `.git` is a pointer file; common Git metadata stores the real registry. A lane is a full checkout for the selected commit, not only the edited files. The pool root is fixed by this helper; do not change it without code changes and tests.

## Initialize the pool

Kit/config must already have reached the target through maintainer review. Start the coordinator on `ae/records/<developer>/<sprint>` through a safe human setup process; preserve any existing work first. Do not use the target branch as the records branch. Fetch the target explicitly, read the full SHA, then from coordinator root:

```text
python tools/agentic/pool.py init --developer dev-a --base <FULL_TARGET_SHA>
python tools/agentic/pool.py init --developer dev-a --base <FULL_TARGET_SHA> --apply
python tools/agentic/pool.py status
```

Without `--apply`, init/lease/release are previews only. Init uses a detached commit rather than checking out `main` in every lane. Pool initialization does not modify remote state or product source. If initialization partially fails or the registry disagrees with disk state, stop and reconcile through a maintainer; never silently adopt old folders.

## Lease a lane

The Control Plane chooses an idle lane, an available team slot, and a Story that is actually ready. Internal example—not a command the operator must type for every task:

```text
python tools/agentic/pool.py lease --developer dev-a --story STORY-101 --lane lane-01 --role BUILD --sha <FULL_BASE_SHA> --invocation build-101-01 --apply
```

The helper creates `work/dev-a/STORY-101` on the selected lane and manifest `.agentic/local/tasks/ae-dev-a-STORY-101.json`. The result includes lease token, generation, and absolute workspace. **LANE_PREPARED does not mean ACTIVE**: main must then actually invoke `ae-builder`. Record runtime invocation when available; a local assignment ID is not a claim about a runtime ID.

Subagent commands must be explicit, for example `git -C "<absolute-lane>" status` or `cd "<absolute-lane>" && <test>` in the same call. Read/Write/Edit tools also use absolute paths. A custom pool does not automatically set cwd for subagents. Do not use native worktree-isolation options for pool tasks.

## Release without losing work

Main checks source commit, processes/servers, report, and files, then fills `lane-release-checks.json` from the template using real facts. `processes_stopped_ref` and `records_saved_ref` must point to checks that actually occurred. Run:

```text
python tools/agentic/pool.py release --lane lane-01 --token <CURRENT_TOKEN> --head <FULL_CURRENT_SHA> --checks <LOCAL_CHECKS_JSON> --apply
```

Release preserves the branch, moves the lane to the current detached SHA, and keeps the folder. The lane becomes IDLE. It does not automatically move back to the latest target; the next task's base must still be verified. Reader/QA assignments must not create extra commits. Dirty/untracked work blocks release. An old token/generation must never be reused for a new assignment.

A lane where **nothing was committed** records no candidate: the release reports `candidate: NOT_RECORDED` and the Story stays at `BUILD_PREPARED`. Before v0.7 the lane head was promoted unconditionally, so a lane leased against the wrong base recorded the *base* as the candidate and the Story could then neither be resumed nor re-leased.

A lane's ignored files are only released when something has declared them reusable. The refusal now names the exact undeclared paths and the configuration key, so the fix is to declare the real directory rather than widen the list with a broad pattern. Artifacts the kit itself creates — currently `__pycache__` at any depth — never block a release; that list is fixed in the helper and is deliberately not configurable, because a project-extensible version would be the same weakening under another name.

## Discard a Story that was started wrongly

A BUILD lane leased against the wrong base has no forward path: there is no candidate to resume from, and re-leasing is refused as a duplicate. `abort` exists for exactly that, and for nothing else:

```text
python tools/agentic/pool.py abort --developer dev-a --story STORY-101 --lane lane-01 \n    --token <CURRENT_TOKEN> --reason-ref docs/agentic/records/dev-a/STORY-101/abort.md --apply
```

It refuses unless **all four** hold: no commit exists on the work branch beyond its base; the branch was never published to the forge; no pull request is recorded or open for it; and no assurance gate has been satisfied or waived and no traceability record exists. Each refusal says which condition failed. If any of them is false the Story has real history, and the path is FIX or the recovery flow — **abort is never a way to discard a real candidate, unpublished built work, or collected attestation.**

Applying it deletes the local branch and the task manifest, returns the lane to IDLE at the base, keeps the folder, and writes a local record under `.agentic/local/aborts/` carrying the reason reference. A Story that was started and abandoned is still history. Afterwards the Story can be leased again normally.

A task can be waiting for developer MR review while its lane is already idle after source/report are safe. The branch remains available and can be leased again for FIX on an allocated lane. By default the implementation owner owns the fix; the physical lane may differ when ownership/handoff is clear.

## Review, QA, and role changes

After Builder release, main leases a REVIEW lane that has not contributed to that Story, detached at the exact candidate. REVIEW/QA/PREP/INTEGRATE use detached HEAD. QA and Reviewer may run concurrently when folders/resources are independent; all Story leases must be released before FIX/merge. Git rejects opening the same branch in two linked worktrees; use detached snapshots instead of overriding Git.

Dynamic roles do not erase author history. Reviewer-independence checks are conservative: the REVIEW invocation **and lane** must never have built that Story. If no independent lane is available, wait; never erase history just to pass the check.

## Cache, private files, and stuck state

The ignored-directory allowlist is empty by default. A maintainer may add known reusable cache directories such as `node_modules/` or `backend/node_modules/` after evaluating lockfiles, environment, and data boundaries. Values must be exact relative directories ending in `/`, with no wildcard/parent/hidden root. Cache is not automatically deleted and is not evidence that dependencies are correct.

`.env`, unknown fixture output, untracked files, or ignored files outside the allowlist block lane movement. Do not run reset/clean or overwrite ignored files. Checkout uses `--no-overwrite-ignore`; a cache collision with tracked source is rejected. Stop old processes; remove only identified authorized task output, or escalate. Worktrees containing unsupported submodule/lock/prunable/path-link states are not automatically processed by this helper.

Registry states: IDLE, PREPARING, LEASED, RELEASING, RECOVERY_REQUIRED. There is no expiry/steal. A lease left after a crash does not become idle automatically. Main checks actual processes and reports; when normal release is insufficient, a maintainer performs targeted recovery. A stale local lock is not deleted merely because it is old.

## Technical boundaries

The helper locks local ledger transactions and checks token, Git, and paths. It does not track every Windows process, lock other computers, or authenticate who filled flags. Process checks, records, contract, QA resources, and team slots remain Control Plane responsibilities. A token identifies lane use; it is not a security credential. Separate folders do not automatically isolate caches, ports, databases, or test accounts.
