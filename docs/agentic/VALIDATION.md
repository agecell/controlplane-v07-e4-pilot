# Validation — Control Plane v0.6 Release Candidate

**Status:** local suite green and regression-compared against v0.5; GitHub policy states
verified live; **the GitLab path has no live verification in this build**; real Claude
pilot pending.

Package-level validation, including the v0.5 baseline comparison and the list of defects
the rewire surfaced, is in `VALIDATION.md` at the package root. This file records the
in-repository view.

## v0.6 delta

- provider seam (`tools/agentic/forge.py`): GitLab or GitHub behind one normalized pull
  request. Two-provider tests in `test_v06_forge.py`, over a fake transport plus five
  real-Git integration-proof cases;
- project schema 3 → 4; `gitlab_host`/`gitlab_repo` become one `forge` block;
- how much merge policy the forge itself enforces is now read and recorded, not assumed;
- the full v0.5 suite was captured as a baseline first (**342 PASS, 0 failures**) and
  re-run afterwards. Every baseline test is still present and still passing.

## Executed suites (v0.5 phase history, all still passing in v0.6)

| Suite | Result |
|---|---:|
| v0.4 execution regression (`test_v04.py`) | **99 PASS** |
| Phase 1 contract core | **17 PASS** |
| Phase 2 project config/migration | **21 PASS** |
| Phase 3 assurance-aware precheck | **28 PASS** |
| Phase 4 task manifest | **21 PASS** |
| Phase 5 generic assurance registry | **28 PASS** |
| Phase 6 human attestation/waiver | **30 PASS** |
| Phase 7 gate-local revalidation | **23 PASS** |
| Phase 8 merge assurance enforcement | **15 PASS** |
| Phase 9 traceability index | **12 PASS** |
| Phase 10 capability assurance | **14 PASS** |
| Phase 11 compatibility/migration | **19 PASS** |
| Phase 12 final runtime audit | **15 PASS** |
| Installer | **14 PASS** |
| **Total** | **356 PASS** |

Long Git/worktree suites were executed in bounded groups to stay inside execution limits; each complete unittest class/module returned PASS.

## Phase 12 integration gaps found and fixed

### 1. Claude permissions for v0.5 helpers

`contract.py` and `assurance.py` were part of the runtime but were not explicitly present in `.claude/settings.json` Bash allow rules. Release Candidate now allows direct reviewed helper calls for both `python` and `python3`.

### 2. Remote readiness guard

Direct push/pull-request guard readiness requires:
- project schema 4 / kit 0.6 (v0.6: this gate was still on 3 / 0.5 after the rewire, which
  denied every remote operation under a v0.6 configuration; fixed);
- `configuration_approved=true`;
- remote actions approved;
- `assurance_policy.approved=true` with a real approval ref.

This closes a defense-in-depth mismatch where runtime helpers were assurance-aware but the outer direct-remote guard still reflected v0.4 readiness semantics.

### 3. Protected v0.5 policy documents

Guard protected paths now include `ASSURANCE.md`, `TRACEABILITY.md`, `MIGRATION.md`, and `CONTROLS.md` in addition to the inherited policy/helper files.

### 4. Maintainer boundary for project migration

`contract.py project-migrate-v04 ... --apply` is recognized as a reviewed helper but PreToolUse returns `ask`: project schema migration remains an explicit maintainer-reviewed action.

## Additional smoke validation

- fresh install into a clean temporary Git repo: PASS;
- installed settings include `contract.py` and `assurance.py`: PASS;
- fresh project remains schema 4 / kit 0.6 with assurance unapproved by default and an unresolved `forge` block: PASS;
- helper CLI `--help` smoke for `ae.py`, `pool.py`, `contract.py`, `assurance.py`, `merge.py`, `cleanup.py`: PASS.

## Test boundary

Local Git/worktrees are real temporary repositories. Forge REST in tests is a controlled
fake transport. These tests do **not** prove:
- Claude Code tool/skill/subagent dispatch behavior;
- forge auth/permission specifics;
- protected-branch or branch-protection behavior on a live project;
- real CI timing/status semantics;
- Windows/Git Bash/WSL differences;
- two simultaneous developer sessions against one real remote.

**GitLab specifically has no live verification in this build.** No `glab` and no GitLab
instance were available, so the GitLab adapter is exercised only against the recorded
field shapes v0.5 consumed. The claim is "behaviour preserved versus v0.5", not "tested".

GitHub's three policy states were verified against a real repository, and the read paths
were re-verified live after the rewire. Candidate-level GitHub checks — approval counting
and required-check verification — have test coverage but no live pull request behind them.

Use `PILOT.md` before controlled team adoption.

## Package-level checks

- Python files compiled: **29/29 PASS**;
- JSON files parsed: **14/14 PASS**;
- tracked package files in SHA-256 manifest: regenerated as the final packaging step;
- stale Phase-11 checkpoint markers in core Claude/runtime authority: none;
- Claude executable on build environment: **NOT INSTALLED**, therefore pilot not falsely claimed.
