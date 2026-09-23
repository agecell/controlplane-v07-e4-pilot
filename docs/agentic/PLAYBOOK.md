# Team playbook — Control Plane v0.6

Use this document as a situation-based recipe. Binding rules remain in `OPERATING_RULES.md`, `AGENT_RULES.md`, project config, and organization policy.

## 1. Pick the situation, then follow the recipe

Common situations:
- one Story is ready;
- a bug is reproducible or not reproducible;
- Reviewer asks for proof or finds a defect;
- a lane needs to be reused;
- a bounded sprint backlog is assigned;
- one feature spans multiple Stories;
- AGENT_MERGE or DEVELOPER_REVIEW is in use;
- branches accumulate after merge;
- tests/tools/session fail;
- the kit needs broader adoption or upgrade.

### Shared rules

Always preserve exact Story/candidate identity, use the fixed reusable pool, keep writers bounded, record evidence truthfully, never fabricate human authority, and distinguish integration from deployment/release.

## 2. One ready unit of work

### A. Story or small feature

1. Verify BUILD READY authority and current Git/project state. Run
   `contract.py handoff-check <story-card>` first: it says whether the card can produce
   a valid contract and names what is missing, before anything is leased. Then derive
   the contract with `handoff-generate --publication-sha <FULL_SHA> --apply` rather than
   writing one by hand — it is a derived artifact now. A Story card still on bp-meta
   schema 1 works, with the references it has no source for supplied as `--ref` overrides.
   See `HANDOFF.md`.
2. Run assurance-aware precheck. From v0.7 this also asks the forge whether each
   configured attestor identity exists. A required gate whose every candidate is
   unknown returns `CONTRACT_PRECHECK_BLOCKED` - fix the identity, do not start the
   Story. Where the forge cannot be asked the result says `forge_verified: false`
   and does not block; local work stays possible without a reachable forge.
3. Lease one BUILD lane and invoke Builder.
4. Save exact candidate, release Builder lane safely.
5. Run risk-proportional review/proof/fix.
6. Close required PRE_MERGE assurance.
7. Publish the branch at the exact candidate, open the MR with
   `pull_request.py --task ... --title ... --apply`, record the returned `mr_iid`
   in the task manifest, then follow merge mode.
8. Verify integration, write traceability, clean safe branch refs.

### B. Bug can be demonstrated

Give Builder the reproduction/evidence and the intended preserved behavior. Add a regression test capable of failing on the bug. Reviewer validates both the fix and test effectiveness.

### C. Requirement is already satisfied

Return `NO_CHANGE_NEEDED` with proof. Do not create artificial code, branch, or MR just to show activity.

### D. Bug cannot be reproduced

Use PREP/QA to narrow the gap. Do not guess a source fix without a valid problem statement. Return DECISION_REQUIRED/BLOCKED when needed.

## 3. Reviewer requests proof or finds a problem

### A. Evidence is insufficient

Reviewer returns PROOF_REQUEST naming the doubt and minimum proof. Main leases QA on the exact candidate. QA returns proof; Reviewer reevaluates without automatically rerunning broad suites.

### B. Code has a defect

Reviewer returns FIX_REQUIRED with finding ID, location, contract/evidence, impact, and minimum fix. Main releases readers, leases FIX to the source owner, and creates a new candidate. The new SHA requires a new verdict.

### C. One cycle is not enough

Default is one automatic fix cycle. If the problem persists or scope/risk changes, checkpoint and diagnose instead of looping until someone says ACCEPT.

## 4. A lane is needed for another task

### A. Assignment finished using the lane

Save source/report, stop known processes, verify clean release conditions, then release through `pool.py`. Keep the folder.

### B. MR waits for a developer

The lane can already be idle when source and records are safe. DEVELOPER_REVIEW does not require holding a worktree hostage until human merge.

### C. Lane is dirty or holds important files

Do not reset/clean/delete. Preserve unique work and return BLOCKED/RECOVERY_REQUIRED until it is reconciled.

### D. Every lane is busy

Wait or run bounded read-only PREP where useful. Do not create unmanaged extra worktrees simply to increase occupancy.

## 5. Coordinator handles my sprint backlog

Give an ordered bounded list and dependencies. The Control Plane may continue to eligible work only within that assignment and package continuation rules.

### Example

```text
STORY-201 priority 1
STORY-202 depends on 201
STORY-203 independent and may run while 202 waits
```

### Package summary

Report per Story: state, candidate/MR/integration, proof, lane release, cleanup, blocker, and next owner. Keep package progress separate from release readiness.

## 6. One feature must work as a whole

Keep Story-level engineering evidence. After individual integrations, validate the combined behavior on the actual target. Create capability-level assurance only when the contract/policy has a POST_INTEGRATION obligation.

### Choose sensible change boundaries

Prefer coherent reviewable MRs, not one MR per file and not one sprint-wide giant MR.

### After components integrate

Register exact integrated Story inputs into capability state only when required, close allowed aggregated gates, record product acceptance, and finalize capability. A new Story composition reopens stale capability acceptance as needed.

## 7. AGENT_MERGE: continue without routine approval prompts

### Before remote action

Verify project/remote readiness, exact candidate, technical review, named checks/CI/QA, mandatory PRE_MERGE assurance, target, dependencies, server rules, and integration turn.

### Execution path

Use `merge.py` preview then apply. Never direct-push the target or use an alternate merge API when the helper blocks. Verify actual resulting target before writing INTEGRATED.

### If it stops

Read the blocker. Do not flip approval flags, weaken gates, or fabricate evidence. Timeout/uncertain response requires recovery from actual state.

## 8. DEVELOPER_REVIEW: human first

### When MR is ready

Return `MR_READY_FOR_HUMAN_REVIEW` with exact candidate, checks, assurance state, and remaining human obligations. The agent does not merge.

### Only one task needs developer review

A task may tighten an otherwise AGENT_MERGE project to DEVELOPER_REVIEW. It cannot loosen project policy in the opposite direction.

### Do not mix mode choice with permission changes

Project-level permission/policy changes require maintainer review, not an ad-hoc workaround because an agent is blocked.

## 9. Merged branches are accumulating: run bounded cleanup

### A. After one Story merges

Verify integration and records, release all Story leases, fill factual cleanup checks, then run cleanup preview/apply under approved cleanup policy.

### B. Lane has already been reused

Old cleanup must not alter the new lane checkout. Clean only exact old Story refs when safe.

### C. Branch/lane is not safe

Return KEEP/NEEDS_REVIEW/CLEANUP_PENDING with reason, owner, and trigger. Integration remains valid.

### D. Reconcile residue on resume/sprint close

Check only the developer's known pending cleanup; never perform a blind repository-wide deletion sweep.

## 10. Tests fail, tools are unavailable, or the session breaks

### A. Test or CI fails

Keep FAIL factual. Identify whether it is product code, test harness, environment, or unrelated infrastructure. Do not convert red to green by weakening tests without authority.

### B. Browser, API, or test database

Use only authorized environments/data. Record environment and untested boundaries. No live migration/deploy without separate authority.

### C. Session/tool interruption

Checkpoint facts. On resume, verify actual Git/process/remote state. Do not repeat uncertain operations blindly.

### D. Owner changes

Publish source/records, update team claim, and ensure only one active owner.

### E. Source and records disagree

Treat live Git and the configured forge as current engineering fact for branch/SHA/pull-request/merge, then reconcile versioned records without inventing history.

## 11. Expand adoption and update the kit

### Start with an inspectable pilot

First prove read-only startup, one routine Story, independent review, MR boundary, and developer-review stop. Add material assurance, revalidation, capability, AGENT_MERGE, and two-developer concurrency progressively.

### Measure benefit and cost

Track cycle time, human round trips, reruns, false blockers, stale work, review defects caught, and cleanup residue. Do not claim speed improvement without pilot data.

### Upgrade from an earlier version

Use the explicit migration path with clean repository, released lanes, backups, and maintainer review. Never overwrite customized managed files silently.

### New project or technology

Keep the same control model but configure real build/test/CI commands and project-specific policy. Validate the environment with a practice repository before wider adoption.
