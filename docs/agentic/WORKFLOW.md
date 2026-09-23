# Working with Claude Code — Control Plane v0.6

This is the readable workflow edition. The binding runtime rules remain in `OPERATING_RULES.md`, `AGENT_RULES.md`, project config, and organization policy.

## 1. Start with the simple picture

```text
Approved Story
   ↓
Control Plane precheck
   ↓
Builder on reusable lane
   ↓
exact candidate SHA
   ↓
independent review / required proof
   ↓
MR + merge according to project mode
   ↓
traceability + safe cleanup
```

### What is the benefit?

The developer should not have to manually route every prompt between Builder, Reviewer, QA, and Integrator. The Control Plane keeps the Story identity, candidate SHA, lane ownership, review state, and merge path aligned.

### System boundary

The workflow starts from approved work. It does not replace product discovery, team prioritization, organization policy, or deployment/release authority. One Control Plane coordinates one developer's local work in one repository.

## 2. Know the work, workspace, and role

Three concepts must remain separate:

- **Story** — the unit of work and source branch.
- **Lane** — a reusable linked-worktree workspace.
- **Role** — Builder, Reviewer, QA, Prep, or Integrator for one assignment.

A lane is not permanently assigned to one role. A Story branch can survive after a lane is released.

### Who does what?

- Control Plane: sequencing, state, dispatch, records, gate decisions, integration coordination.
- Builder/Fix Owner: writes and tests the bounded delta.
- Reviewer: independently evaluates the exact candidate.
- QA: runs requested additional proof on the exact snapshot.
- Prep: read-only investigation that reduces ambiguity.
- Integrator: mode-aware merge/cleanup inspection or execution through helpers.

## 3. Follow one Story until its result is clear

### Before starting

The Control Plane checks Story authority, owner, AC, dependencies, risk, normalized contract, project policy, current Git state, team writer slots, and pool availability. Result is AUTHORIZED TO START or a specific blocker.

### During BUILD

The Control Plane leases an idle lane. Builder works only on the assigned branch/workspace, makes the smallest valid delta, runs focused checks, commits the candidate, and returns its full SHA. Source and evidence must refer to the same candidate.

### Review and fix

For Tier2/3, review occurs on another independent lane detached at the exact candidate. Reviewer may ACCEPT, request bounded proof, require a named fix, escalate risk, or block review. QA adds only requested proof. FIX returns to the source owner and creates a new candidate that requires a new verdict.

### After required checks are complete

The Control Plane verifies PRE_MERGE assurance when applicable, prepares/updates the MR, then follows `AGENT_MERGE` or `DEVELOPER_REVIEW`. Integration is only complete after the target result is verified.

## 4. Fixed worktrees, not one folder per Story

The default pool lives under:

```text
.claude/worktrees/lane-01
...
.claude/worktrees/lane-07
```

A lane contains a full linked checkout. New Stories reuse these folders; the backlog size does not determine folder count. The coordinator checkout is separate and should not become the general production-writing lane.

A typical Story may use:

```text
lane-01 → BUILD work/dev-a/STORY-101
lane-02 → REVIEW detached at candidate SHA
lane-03 → QA detached at candidate SHA when needed
```

After the assignments finish, leases are released and the folders return to IDLE.

## 5. Reuse lanes safely

Lane release happens only after source/report are saved, required processes are stopped, and no untracked/dirty work would be lost. Release preserves the Story branch and keeps the lane folder.

### Example reuse

```text
Morning: lane-01 BUILD STORY-101
Later:   lane-01 REVIEW STORY-104
```

The lease token/generation and author history distinguish assignments.

### A BUILD lane's base is the target branch head

Not the coordinator's records branch. The records branch does not contain the work already merged into the target, so a Story leased against it is built on a base its dependencies are missing from. Fetch the target explicitly and read the full SHA before leasing. If it happens anyway and nothing was committed, `pool.py abort` discards the start; once anything was committed, pushed or attested, it is FIX or the recovery flow.

### When a lane must not be reused

Do not reuse a lane while an agent/process still uses it, while uncommitted/untracked work needs preservation, or while registry/disk state is unknown. Use RECOVERY_REQUIRED rather than stealing or force-cleaning.

## 6. Parallel work is allowed, but not “as much as possible”

The goal is cycle time, not agent occupancy. Default pilot limits are intentionally conservative: one local Builder per developer, two production writers per repository, and one writer on a shared/high-risk surface.

Parallelize only when work is independent enough that conflict/rework risk is bounded. Read-only PREP can run while a writer waits.

### Review depth follows risk

- Tier1: bounded/mechanical; main may review if independent from the writing.
- Tier2: normal bounded production behavior; independent Reviewer.
- Tier3: security/privacy/data/migration/concurrency/destructive semantics; deeper review and risk-specific proof.

### Trustworthy evidence

Evidence identifies Story, exact SHA/snapshot, command, cwd, environment/time, result, and artifact. `NOT_RUN` is better than a fabricated PASS. Reuse evidence only when the affected seam remains valid.

## 7. Choose how the MR proceeds

Two merge modes remain:

```text
AGENT_MERGE
  gates complete → helper-controlled merge → verify integration

DEVELOPER_REVIEW
  gates summarized → MR_READY_FOR_HUMAN_REVIEW → human continues
```

### Automatic does not mean unconditional

AGENT_MERGE still requires exact candidate review, named checks/CI/QA, project policy, mandatory PRE_MERGE assurance, server rules, and the integration lease. It never means “merge whatever the AI wants.”

## 8. One Story, a sprint backlog, or one feature

The Control Plane can continue across a bounded assigned list, but it does not reprioritize the team's sprint.

### Backlog example

If Story B depends on Story A, B waits until A is actually integrated. An independent Story C may continue if package instructions allow it.

### Feature example

A feature can contain multiple Stories. Each Story still has its own source/review/integration trace. Capability-level acceptance is used only when there is an explicit POST_INTEGRATION obligation across the combined feature.

## 9. After merge: clean branches, keep lanes

Normal closure:

```text
INTEGRATED
→ write traceability
→ preserve required evidence
→ release leases
→ delete only safe Story branch/tracking refs
→ keep reusable lane folders
```

### Reading cleanup results

- `CLEANUP_COMPLETE` — scoped branch/tracking cleanup is complete.
- `CLEANUP_PENDING` — integration remains valid, but a named resource must be revisited by a stated owner/trigger.

## 10. Concerns, stop, and resume

A stop request halts new dispatch. Save checkpoint facts instead of forcing the system into a fake clean state.

### When stopping

Record source branch/full SHA, task manifest, active/unknown lease, MR/remote state, pending checks, and next action. Preserve unique work.

### When resuming

Read the checkpoint, then verify actual Git/worktree/process/MR/integration state. Never assume a timed-out request failed or that an old lease expired.

## v0.5 legacy transition rule

Existing schema-2 v0.4 Stories can continue with v0.4 semantics only when current project/Tier policy adds no new assurance. If new obligations exist, mutation/agent merge is blocked until the Story is bound to a real normalized contract and explicitly upgraded to schema 3. Legacy evidence is preserved but never converted into fake v0.5 assurance PASS.
