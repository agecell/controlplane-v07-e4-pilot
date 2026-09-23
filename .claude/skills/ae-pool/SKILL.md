---
name: ae-pool
description: Initialize the fixed reusable pool or show lane usage.
disable-model-invocation: true
---
# ae-pool — v0.7

Input: `init|status`, default `status`. Read `POOL.md`. `status` → run `pool.py status` and report actual state without mutation. `init` → only after a maintainer has approved configuration, the coordinator records branch, and the exact current target contains the reviewed kit; run preview, then apply initialization only when setup is explicitly requested/authorized. Default is 7 lanes, or `config.pool.size`. Do not create folders based on Story count. Lease/release are coordinator-internal operations governed by POOL rules, not inferred from role names. Do not adopt old folders or delete the registry; partial/unknown state requires recovery.

$ARGUMENTS
