# Operating rules — v0.5 (v0.4 execution baseline preserved)

This document governs agent behavior. The user-facing guides explain the same rules with examples. Project configuration defines the actual technology, target branch, checks, merge mode, and permissions. Organization policy must not be bypassed.

## 1. System boundary

Input is an approved requirement/Story assigned to one developer. One product lives in one repository. A Control Plane coordinates local subagents, not every computer in the team. Priority remains a team decision. Operation is interactive; checkpointing is required when moving between sessions. Actual Git and forge state overrides stale snapshots for branch, SHA, pull-request, and merge facts.

Default: one active Builder per developer; initial budget of two production writers per repository, adjustable by the tech lead. A related shared/high-risk surface has only one writer. Seven lanes are workspace capacity, not an instruction to activate seven agents. Different files do not necessarily mean different contracts. PREP_ONLY/WAIT are valid when safer or more useful than adding work.

## 2. Roles, identity, and workspace

Main coordinates assignment and records. Builder writes, tests, and fixes; Reviewer assesses without editing; QA adds proof; Prep reads specific gaps; Integrator inspects or performs integration according to mode. Main may perform the integration function itself after gates are complete. Builder does not merge on its own initiative during BUILD; main issues a separate INTEGRATE assignment when that role is needed.

The pool uses `lane-01` through the configured count. A lane may change role between assignments, but Story author history remains recorded. Source branch follows the Story. The coordinator uses a records branch. Lease tokens distinguish old and new use even when the same folder is reused. One invocation holds one lane; do not assume conversational memory persists merely because the folder is reused.

## 3. Readiness and brief

Match owner, Story, requirement source, AC, scope/non-scope, dependencies, behavior that must be preserved, and risk. Preflight checks the normalized Story contract, effective assurance from project + Tier + Story, Git, push/MR/merge effects, team slots, source, and tools. Precheck does not run every assurance gate up front and does not claim PASS. Preserve the user's existing changes. If AC is already satisfied, return NO_CHANGE_NEEDED with evidence; do not manufacture a branch/MR.

The brief includes full base/candidate SHA, lane and absolute path, invocation, allowlist, exclusions, checks, authority, and required return state. Main classifies BUILD_NOW, PREP_ONLY, or WAIT. No new mini-spec is required when the brief and existing authority are sufficient.

## 4. Execution and review

Default flow: READY → PREFLIGHT → BUILD → CANDIDATE_SAVED → REVIEW → FIX when needed → gate-local revalidation → required PRE_MERGE assurance → MR/MERGE_READY → integration mode → INTEGRATED → optional capability assurance → cleanup and LANE_RELEASED. Task state is different from lane state.

Main prepares a lease before invoking a named subagent. An actual tool invocation—not planning text—is evidence of dispatch. Builder checks path/branch/SHA, makes the minimum delta, and runs focused tests. Save source on the branch, stop processes, save the report, then release the lease. Reviewer receives another lane detached at the same candidate; main prepares diff/source without changing the candidate. All readers must finish and release before FIX/merge for that Story.

Tier 1: mechanical/light change, bounded main review when main was not the writer. Tier 2: bounded feature/API change, one primary independent review. Tier 3: auth, privacy, stored data, migration, concurrency, destructive semantics, or risky contract; deep review and additional proof proportional to risk. Tier follows risk, not diff size or lane number.

Evidence names the Story, full SHA or proven-equivalent snapshot, command, cwd, time, environment, result, and artifacts. PASS/FAIL/NOT_RUN/PENDING must be truthful. Regression tests must demonstrate that the bug can be caught. Reviewer reuses valid evidence; PROOF_REQUEST states the doubt and minimum test. QA runs proof on a QA lease at the exact candidate, never on production/shared data without authority.

A blocking finding includes ID, location, contract, evidence, impact, and minimum fix. Style/debt outside the requirement is not automatically blocking. FIX returns to the source owner; default maximum is one automatic fix cycle, then diagnosis/checkpoint if unresolved. A new SHA requires a new verdict; delta-only review and evidence reuse are allowed when other areas remain valid. Never swap reviewers until someone accepts or weaken tests to obtain a pass.

## 5. Merge modes and controls

`AGENT_MERGE` is the default after setup is approved. Main/Integrator verifies a valid Story contract, exact-SHA review, named checks, mandatory CI/QA, all mandatory PRE_MERGE assurance validly closed, current target, dependencies, scope, remote effects, and server readiness. No per-MR administrative approval is required unless policy requires it. Organization/project human gates still apply, including `merge_policy.human_review_required` when configured.

`DEVELOPER_REVIEW` stops at MR_READY_FOR_HUMAN_REVIEW. A human developer/reviewer/maintainer continues the process. Main does not execute merge in this mode, even after approval; the mode is deliberately the human path. Task override may only select DEVELOPER_REVIEW. Project/permission changes go through maintainer review, not ad-hoc changes when the agent is blocked.

Use `merge.py`, not direct target push or arbitrary merge commands. It checks the current MR and uses a source-SHA guard; it does not queue delayed auto-merge. Remote lease `ae/locks/integration` serializes cooperating kit clients. A local lease only locks helper transactions in one local repository. Neither one locks the human merge button or guarantees another tool cannot change the target. All integration actors must follow the turn; if that cannot be guaranteed, use the developer path. See `MERGE_POLICY.md` for recovery.

A changed target requires reassessment of relevant impact/evidence, not ritual rebasing. Conflicts that require source changes go back to Builder and review. Normal/FF merge is verified from resulting commit and ancestry; squash from the MR mapping and correct delta. Never write INTEGRATED merely because a request was accepted.

## 6. Storage, cleanup, and packages

Shareable records live in `docs/agentic/records/<developer>/<story>/` on `ae/records/<developer>/<sprint>`. After integration, each Story has a traceability index; a capability with POST_INTEGRATION obligations may have `docs/agentic/records/team/<capability-id>/assurance.json`. Reports written after freeze do not modify the source branch. Logs, private paths, lease tokens, and manifests stay in `.agentic/local/` or common Git metadata, not public commits. Secrets/real data never enter source, reports, or evidence. Main writes records; subagents return results and do not edit one shared global-status file.

Lane release preserves the source branch and keeps the folder. CLEANUP after merge deletes only the exact Story branch proven safe. Never delete targets, tags, records, another person's branch, open dependencies, unique work, or pool folders. A snapshot/candidate may remain detached on an idle lane; that does not mean the branch must be retained. Evidence preservation and retention needs must be explicit. Cleanup from an old task must not move a lane already reused by a new task.

CLEANUP_PENDING must include reason, owner, and trigger. Merge remains INTEGRATED even when housekeeping is delayed. Recheck on resume/package close. Status-only/check/stop never deletes. An idle pool does not prove a Story was merged or released.

A sprint/feature package is a bounded list owned by one developer with priority, dependencies, MR boundaries, continuation permission, and stop rules. AGENT_MERGE may continue a dependent Story only after integration is verified. In DEVELOPER_REVIEW, dependent work waits for human merge; independent work continues only when allowed. One small feature may use one coherent MR; a sprint should not become one giant MR. Validate combined features on the actual target. Keep MR-ready, integrated, QA, cleanup, and release states distinct.

## 7. Stop, resume, and maintenance

Stop new dispatch immediately; save known facts and uncertainties. Do not reset/clean/force-commit or mass-kill processes. Active/UNKNOWN leases do not automatically expire. On resume, read checkpoint/registry/manifest and verify source, worktrees, processes, remote, MR, and integration intent. Do not repeat a request that may already have succeeded. Handoff to another computer requires published source and records; local ledger/path state does not move with it.

Upgrade is performed while no mutation is running, through branch/review/test/pilot. Setup does not authorize deploy/release, live migration, destructive data operations, or product-scope expansion. Helpers and Markdown are not a sandbox, a semantic-evidence judge, or a guarantee of compatibility with every tool version. Code/test boundaries are documented in `CONTROLS.md` and `VALIDATION.md`.
