---
name: ae-start
description: Start one assigned Story using contract/precheck, the reusable pool, and the project's merge mode.
disable-model-invocation: true
---
# ae-start — v0.7 Release Candidate / Pilot

Input: `<story-path> <developer> <implementation-phase-or-assignment>`. Run as `ae-control-plane`. Read AGENT_RULES, OPERATING_RULES, project config, and the canonical Story/backlog authority.

For **new BUILD** work, normalized Story contract schema 1 must already be registered and pass assurance-aware precheck; leasing a BUILD lane creates task manifest schema 3. The contract is a **derived artifact**: produce it with `contract.py handoff-generate` from the Story card at its publication commit, never by hand. `handoff-check` first tells you whether the card can produce one. Precheck also asks the forge whether each configured attestor identity exists: `CONTRACT_PRECHECK_BLOCKED` with `NO_RESOLVABLE_ATTESTOR` means a required gate has no one who could ever close it — fix the identity, do not lease a lane. `forge_verified: false` means the forge could not be asked; that is reported and does not block. Precheck also asks Git whether the authority the contract is bound to has moved: `AUTHORITY_SUPERSEDED` means the Story card or canonical backlog was changed on the target branch after the declared publication commit — re-bind the contract, do not build against a revision that has been corrected. `authority_currency.state: UNDETERMINED` means the question could not be answered and is reported as such, never as current. Verify the actual target, dependencies/hotspots, writer slot, pool, baseline/effects, then return AUTHORIZED/BLOCKED before mutation.

If an **existing schema-2 v0.4 task** is found, do not fabricate a contract. Check legacy compatibility: project/Tier additional assurance must be empty to continue with v0.4 semantics. If it is not empty, use PREP and explicit `contract.py task-upgrade-v04` after a normalized contract is available; new assurance starts as PENDING.

After BUILD, run review/proof/fix, gate-local revalidation, required PRE_MERGE assurance, then follow the merge mode. Open the MR with `pull_request.py`, never with `glab mr create` / `gh pr create`. AGENT_MERGE uses the merge helper only after all mandatory gates are valid; DEVELOPER_REVIEW stops at MR-ready before merge. Do not create a worktree per Story or expand scope.

$ARGUMENTS
