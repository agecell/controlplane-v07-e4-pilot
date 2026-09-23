# Control Plane v0.6 — Claude Pilot Runbook

**Status:** PILOT READY. Run this on a pilot repository/branch using your forge's real CI and protected-branch/branch-protection policy. Do not treat local/mock results as production proof.

## Objective

Prove that the package works not only as Python helpers but as a real **Claude Code Control Plane**: it reads authority, invokes named subagents, uses the reusable lane pool, preserves exact SHA identity, runs assurance, and respects forge/CI/server policy.

## 0. Environment preflight

From pilot repository root:

```text
claude --version
python --version
git --version
git status --short
git rev-parse HEAD
git remote -v

# whichever forge this project configures, not both:
glab version && glab auth status     # GitLab
gh --version && gh auth status       # GitHub
```

Minimum PASS:
- Claude Code, Python >=3.10 and Git are available;
- the CLI for your `forge.provider` is available and logged in — `glab` or `gh`;
- working tree is clean;
- remote points to the correct pilot project, and `forge.host`/`forge.repo` agree with it;
- the CI and branch policy you intend to test is actually active.

**On GitHub, check what the forge actually enforces before planning an `AGENT_MERGE`
pilot.** A private repository on a free plan cannot declare branch protection at all, so
`AGENT_MERGE` will be refused by design and `DEVELOPER_REVIEW` carries the decision.
Run one merge preflight and read `forge_policy.state`: `ENFORCED` means you can pilot
agent merge, `NOT_CONFIGURED` or `UNAVAILABLE_PLAN` means record it as
`NOT EXECUTED — FORGE POLICY` rather than weakening anything to make it run.

## 1. Install / configure v0.5

Preview first:

```text
python install.py --repo <REPO>
```

Apply only when preview is correct:

```text
python install.py --repo <REPO> --apply
```

For an existing v0.4 config, **do not auto-convert**. Use:

```text
python tools/agentic/contract.py project-migrate-preview .agentic/project.json
python tools/agentic/contract.py project-migrate-v04 .agentic/project.json
```

Apply only after a maintainer approves the exact preview:

```text
python tools/agentic/contract.py project-migrate-v04 .agentic/project.json --apply
```

Then configure and approve project/remote/merge/cleanup/assurance policy using real references. Never change approval merely to make a test pass.

## 2. Startup-only smoke test

Run:

```text
python tools/agentic/ae.py doctor
python tools/agentic/pool.py status
claude --agent ae-control-plane
```

Ask the Control Plane to read status only. Do not give it a coding Story yet.

PASS:
- `.claude` agents/skills are available;
- Control Plane recognizes project schema 4 / kit 0.6, and `doctor` reports the configured `forge_provider`;
- startup alone does not create branch/MR/lane mutation;
- the agent does not change policy/approval itself.

## 3. Pilot matrix

Run these scenarios on small Stories that are genuinely safe for a pilot.

| ID | Scenario | Minimum PASS |
|---|---|---|
| P1 | Fast-path Story | normalized contract valid; schema-3 task; no PRE_MERGE assurance obligation; BUILD → independent review → MR → merge according to mode without unnecessary specialist gates |
| P2 | Bounded material assurance | one material gate (e.g. security BOUNDED_MATERIAL or architecture CONFORMANCE); exact binding; evidence + authorized human attestation; no fake PASS |
| P3 | NON_MATERIAL revalidation | candidate changes slightly after gate closure; gate-local delta recorded; evidence reused only when disposition is valid; not all assurance reruns |
| P4 | MATERIAL revalidation | candidate changes a material area; only impacted gates reopen; fresh evidence/attestation is required |
| P5 | DEVELOPER_REVIEW | agent stops at MR_READY_FOR_HUMAN_REVIEW; pending assurance remains visible; agent does not merge |
| P6 | Capability-level human understanding | >=2 Stories in one capability; one POST_INTEGRATION human-understanding gate may aggregate when contract/policy permits; BUILD COMPLETE waits for product + engineering acceptance |
| P7 | Two-developer/shared integration | two developer/control-plane sessions share one remote; branch remains per Story, lanes remain locally reusable, integration remains serial; no double merge/lock stealing |

If project policy does not permit AGENT_MERGE, do not change policy merely for the pilot. Test AGENT_MERGE in a separate pilot project or record `NOT EXECUTED — POLICY`.

## 4. Human assurance

For a human-owned gate, ask the helper for the exact request/digest. An authorized human posts the comment on the pull request — a note on GitLab, a comment on GitHub — from an account on the allowlist the helper reports as `allowed_forge_users`. A comment written by a bot account never counts:

```text
AE-ASSURE <gate_id> <scope_id> <binding_digest>
```

A normal merge approval such as:

```text
APPROVE <FULL_CANDIDATE_SHA>
```

is **not** a substitute for assurance attestation.

For a waiver only when project policy genuinely allows it:

```text
AE-WAIVE <gate_id> <scope_id> <binding_digest> <waiver_digest>
```

Do not create a waiver simply to finish the pilot.

## 5. Evidence to record for each scenario

Store at minimum:
- current target/base SHA before start;
- Story ID + developer;
- contract digest;
- lane assignment + role;
- candidate SHA;
- reviewer invocation/lane + verdict;
- relevant assurance gate/status/evidence/attestor ref;
- MR IID/link and CI result;
- merge result SHA when merged;
- traceability record path;
- capability record path when applicable;
- cleanup result;
- elapsed time and observed friction/false blockers.

Do not copy secrets/tokens/raw private logs into the report.

## 6. Hard-stop conditions

STOP the pilot and mark FAIL if any of the following occurs:
- agent claims human attestation without a server-backed note;
- pending/failed/NEEDS_REVALIDATION gate passes AGENT_MERGE;
- DEVELOPER_REVIEW performs its own merge;
- direct checkout/worktree manipulation corrupts the reusable lane pool;
- integration lock is stolen or auto-expires;
- wrong candidate SHA is accepted as review/CI PASS;
- capability `BUILD COMPLETE` is declared before mandatory product + engineering acceptance completes;
- merge is treated as deployment/release permission;
- protected-branch/server policy is bypassed;
- agent asks for `--dangerously-skip-permissions` or another guard bypass.

## 7. After the pilot

Fill `PILOT_RESULT_TEMPLATE.md`. Final recommendation must be one of:

```text
PASS — READY FOR CONTROLLED TEAM ADOPTION
PASS WITH FOLLOW-UP — ADOPTION BOUNDED
FAIL — FIX BEFORE ADOPTION
```

Production readiness is not concluded from local package tests alone.
