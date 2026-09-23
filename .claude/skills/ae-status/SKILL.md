---
name: ae-status
description: Show Story or package status without starting or mutating work.
disable-model-invocation: true
---
# ae-status — v0.7

Input: `<story-or-package>`. Read records/manifest, `pool.py status`, and allowed read-only Git/MR state. Distinguish task state from lane state; report SHA, role/assignment, MR, blockers, integration, cleanup, unknowns, and next owner. Do not lease/release, build, fix, merge, apply cleanup, or edit local status merely to make it look tidy. No polling after the session closes.

$ARGUMENTS
