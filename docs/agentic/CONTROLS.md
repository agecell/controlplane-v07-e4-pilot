# Real controls and boundaries — Control Plane v0.7

## Do not confuse instructions with security locks

| Layer | What it does | Boundary |
|---|---|---|
| Workflow/agent Markdown | Guides decisions, scope, review, evidence, and stop behavior | The model may still misunderstand; this is not a sandbox |
| Native permissions/tools | Restricts capability; Reviewer receives no shell/write tools | Effective organization/user settings may differ |
| PreToolUse guard | Rejects known sensitive action patterns; routes pool/merge/cleanup to helpers | Not a complete shell parser; does not prove caller identity |
| Python helpers | Validate schema, tokens, Git/SHA, refs, MR, and local transactions | Do not verify report semantics or every process |
| Forge/OS/accounts | Protected target, merge permissions, credentials, and data | Must be genuinely configured in the team environment. On GitHub the kit reads branch protection and refuses agent merge where the forge declares no policy; it never substitutes one |

Hooks and helpers must not be presented as complete security isolation. General shell can still write or access the network in forms the guard does not recognize. Use native least-privilege permissions, sandbox/OS isolation, and separate accounts according to organization policy. Never store credentials in source/evidence.

## Installed controls

`.claude/settings.json` invokes `guard.py` before Bash/Write/Edit. Agent/skill/settings/project-config/helper and authority documents are protected from routine editing. Direct switch/checkout/worktree mutation, target/lock pushes, branch deletion, human approval, and MR merge are routed/rejected. Legitimate operations go through `pool.py`, `pull_request.py`, `merge.py`, and `cleanup.py` from the coordinator. Creating the MR through `glab mr create` / `gh pr create` is rejected in v0.7: the CLI cannot reach a self-managed host that is not its own default, and a pattern check can only verify the flags it recognizes.

A helper allowlist is not permission to bypass its internal checks. Merge apply requires AGENT_MERGE and standing policy; DEVELOPER_REVIEW does not merge. Cleanup apply has its own policy. Helpers use conditional ref updates for locks/deletion, not force-push code to the target. Routine source/records push must use one explicit legitimate ref. Merge still happens through an MR.

Native Write/Edit in the pool is checked against a BUILD/FIX lease. The guard does not authenticate a caller against a token; main must provide the correct path/role and must not authorize other writers. REVIEW/QA receive no native Write/Edit. For mutating tests, QA shell still works only on assigned fixtures/lanes with process/cwd checks.

## Windows and effective permissions

Choose either Windows native + Git Bash or WSL for one workspace. Helpers use Python 3.10+. On WSL, use installer `--python-command python3`; the settings hook must reference an interpreter actually available to Claude Code. Native and WSL must not act as two writers on the same checkout. VPN, proxy, and certificates follow IT policy; do not disable SSL.

Role models use `inherit`. Start main with `claude --agent ae-control-plane`. Verify agent/skill files are actually supported by the Claude Code version in use. Valid YAML alone does not prove runtime behavior. Installer merges user/global settings additively; broad existing permissions are not automatically removed. Maintainers must inspect effective permissions, including newly available tools.

## Helpers

- `ae.py doctor` shows configuration gaps. `snapshot` stores exact-source identity/diff; `scope-check` verifies allowlisted delta. Old `create-task` is rejected; tasks are now created through pool lease.
- `pool.py init|status|lease|release|abort`: common-Git registry, fixed folders, token/generation, no auto-steal. Main still invokes subagents; the script does not. `abort` discards a Story start that produced nothing, and refuses unless no commit, no published branch, no pull request and no closed assurance exist.
- `pull_request.py --task ... --title ... [--description-file ...] [--apply]`: opens the MR on the configured forge, refuses a source branch outside `work/*` and `ae/records/*`, requires the branch to be published at the exact candidate, returns an already-open request instead of duplicating it, and sets no auto-merge, squash or delete-source-on-merge. It does not write `mr_iid` into the task manifest.
- `merge.py --task ... [--apply] [--recover]`: mode-aware quality/server/lease/exact merge.
- `contract.py handoff-check <story-card>`: states whether a Story card can produce a valid normalized
  contract, and names what is missing. Read-only, no Git state. `contract.py handoff-generate <story-card>
  --developer ... --sprint ... --publication-sha ... [--apply]` derives the contract from the card **as
  published at that commit**; preview by default, and it never replaces a different registered contract.
  See `HANDOFF.md`.
- `cleanup.py --task ... [--apply]`: cleans one Story branch after integration proof; never deletes a lane.

Use `--help` for parameters; for pool subcommands use `pool.py lease --help`. Helpers from the coordinator checkout are authoritative; do not use an older copy from a source lane.

## Smoke test before team use

Run in a practice repository with dummy data and no live deployment. Ensure setup has been merged into the target, project flags were approved from real results, and all developers follow the merge-turn protocol.

1. Main and all skills are visible; at least one named subagent is actually invoked. Token/lease/path match; main does not claim a helper invoked AI.
2. Pool init creates a fixed number of folders. Two consecutive Stories reuse folders. Test safe dirty/untracked/stale-token fixtures; helper rejects without losing files. Allowed cache remains; ignored secrets remain protected.
3. Builder produces a candidate and releases; a different Reviewer lane/invocation reads that SHA. Reviewer cannot Write/Edit/Bash. One named fix → new version → rereview; loop limit is respected.
4. AGENT_MERGE: with complete gates, one MR reaches the target and proof is recorded. Failed gate/developer mode/changed source/pending server approval must block merge. Tests do not use production.
5. Two practice coordinators compete for the remote integration ref; only one acquires it. Simulate timeout after a successful request: no second merge occurs; recovery inspects state then releases its own lease.
6. Cleanup removes only merged Story refs; all pool folders remain. Reassign a lane before old cleanup; cleanup must not replace new source. Test normal/FF/squash according to the project.
7. Stop/resume reads actual state. Uncertain lease/request remains RECOVERY_REQUIRED. Handoff publishes source and records, not only a transcript.

Use synthetic payloads or harmless commands in the practice repository for guard-rejection tests, never destructive experiments on a real project. Record results in a validation record.

## What is not automated

Helpers do not support every forge URL/fork form, every pipeline method, worktree submodules, or every OS-specific process tree. Only GitLab and GitHub are implemented, and only GitLab and GitHub merge results that can be proven afterwards are permitted for agent merge. Source-SHA CI and exact raw-delta squash are conservative baselines. Cross-team dependency checks, process-stop verification, semantic evidence validity, record publication, and human approval still require legitimate sources. The remote lease does not lock the human UI. There is no automatic rollback after partial changes; verify state before retry. See `VALIDATION.md` for what was actually tested.
