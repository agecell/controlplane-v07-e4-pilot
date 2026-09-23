---
name: ae-cleanup
description: Check or clean merged Story branches while preserving reusable pool folders.
disable-model-invocation: true
---
# ae-cleanup — v0.7

Input: `<story> --check|--apply`. Default `--check`. Read `CLEANUP.md` and this owner's original manifest; inspect actual MR/SHA/retention/dependency/process/records and Story lease. Fill checks from facts, not examples. `--check` → `cleanup.py --task <manifest>` (without `--apply`); `--apply` only with the required authority/policy and helper checks. No merge permission is implied. Do not delete worktrees, target, records, tags, another person's branch, or a lane folder already reused by a new Story. Report integration, lane release, remote/local/tracking state, and complete/pending status with owner/trigger.

$ARGUMENTS
