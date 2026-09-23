---
name: ae-checkpoint
description: Stop new dispatch and save a factual continuation point.
disable-model-invocation: true
---
# ae-checkpoint — v0.7

Input: `<story-or-package>`. Stop new tasks and do not chase a merge. Check known child/process state; save records/checkpoint, source branch/full SHA, manifest, local lane/token/generation, merge intent, remote/MR, pending checks, and next step. Do not assume a lease expired or a worker finished without evidence. Preserve dirty/untracked work; do not force-commit, clean, delete, or mass-kill. Record unknowns as RECOVERY_REQUIRED. Private tokens/paths must not enter versioned reports. Main may save safe checkpoint notes; that does not authorize new source/remote mutation.

$ARGUMENTS
