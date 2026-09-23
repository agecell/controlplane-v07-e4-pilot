# Migration notes — v0.4 and v0.5 → v0.6

This document describes compatibility and migration into Control Plane v0.6. The project
configuration moved twice: v0.4 (schema 2) added `assurance_policy` in v0.5 (schema 3),
and v0.6 (schema 4) replaced `gitlab_host`/`gitlab_repo` with one `forge` block. A v0.4
source migrates straight to schema 4 in one step; a v0.5 source uses the package-level
`upgrade_v05_to_v07.py`, which preserves its standing approvals.

Task manifest schema stays 3 and the pool registry stays schema 2. v0.6 changed neither.

## 1. Do not upgrade during active work

Before project-config migration:

- the repository must be clean;
- all lane leases must be released;
- legitimate source/records must already be saved;
- never reset/clean/delete merely to make state appear safe.

Pool registry `.git/agentic/pool.json` remains schema 2 and is **not** recreated.

## 2. Project config migration

Preview remains available:

```bash
python tools/agentic/contract.py project-migrate-preview .agentic/project.json
```

Explicit apply path:

```bash
python tools/agentic/contract.py project-migrate-v04 .agentic/project.json
python tools/agentic/contract.py project-migrate-v04 .agentic/project.json --apply
```

Apply:

1. accepts only the repository's exact `.agentic/project.json` at schema 2 / kit 0.4;
2. requires a clean repository and no active lane lease;
3. backs up the exact source to `.agentic/local/migrations/project-schema2-<digest>.json`;
4. changes `schema_version=4`, `kit_version=0.6`;
5. preserves standing merge/remote/cleanup approvals;
6. adds `assurance_policy` with `approved=false`;
7. folds `gitlab_host` and `gitlab_repo` into `forge` with `provider: "gitlab"` — read
   off the source, since v0.4 spoke GitLab only. Placeholders carry through unchanged:
   migration reshapes a configuration, it never completes one.

After apply, runtime intentionally remains not ready until a maintainer reviews assurance policy and records a legitimate approval.

The installer **does not** auto-convert a v0.4 or v0.5 project config; it names the upgrade script for the version it finds and stops. It never receives authority to silently overwrite customized files.

## 3. Existing schema-2 Story

An existing schema-2 task is not rewritten merely to look like v0.5.

Compatibility mode:

```text
current project/Tier additional assurance = NONE
→ LEGACY_V04_COMPATIBLE
→ Story may continue with v0.4 semantics
```

`technical_review` is not counted as additional assurance because exact technical review is already a v0.4 runtime gate.

If current project/Tier policy adds another PRE_MERGE, POST_INTEGRATION, or RELEASE_POINTER assurance obligation:

```text
schema-2 continuation / agent merge
→ BLOCKED
→ explicit contract binding + task upgrade
```

PREP-only work may still prepare the migration, but Story mutation/execution must not bypass the compatibility check.

## 4. Explicit task upgrade

Use an already prepared/registered normalized contract:

```bash
python tools/agentic/contract.py task-upgrade-v04 \
  .agentic/local/tasks/ae-<developer>-<story>.json \
  --repo .

python tools/agentic/contract.py task-upgrade-v04 \
  .agentic/local/tasks/ae-<developer>-<story>.json \
  --repo . --apply
```

Rules:

- task must be schema 2 `REUSABLE_LANE_POOL`;
- there must be no active lane lease for the Story;
- Story/developer/publication contract must be valid;
- an existing candidate must not silently move to another risk Tier;
- legacy `DEVELOPER_REVIEW` restriction must not be relaxed;
- schema-2 backup is created in `.agentic/local/migrations/tasks/`;
- old review/check/MR evidence is preserved;
- newly required v0.5 assurance starts as `PENDING`;
- no legacy evidence is automatically converted into human/tool assurance PASS.

## 5. Legacy integration record

A compatible schema-2 Story that ultimately merges receives a versioned factual marker:

```text
record_type     = LEGACY_V04_TRACEABILITY
contract_status = LEGACY_V04_CONTRACT
```

The record contains no fabricated Requirement/AC/contract digest. It only shows Story/candidate/review/MR/merge facts actually available from v0.4.

If a human already merged outside the helper and policy later adds assurance, resume still records the factual integration but marks `ASSURANCE_INCOMPLETE_AFTER_INTEGRATION`; there is no false PASS and no `BUILD COMPLETE`.

## 6. Rollback boundary

Migration helpers do not rewrite Git history, push, merge, deploy, or release. Local backups support audit/recovery; rollback must still be based on actual repository/task/pool state, not blind overwrite.
