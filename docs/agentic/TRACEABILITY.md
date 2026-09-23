# Traceability Index — Control Plane v0.6 Release Candidate

## Story traceability

After a Story is proven `INTEGRATED`, the Control Plane writes:

```text
docs/agentic/records/<developer>/<story>/traceability.json
```

The index connects:

```text
Requirement / Decision
→ Acceptance Criteria
→ Story
→ backlog revision
→ contract digest
→ candidate SHA
→ technical review / required PRE_MERGE assurance
→ MR
→ merge result
→ capability / release pointer when applicable
```

This file is **not an evidence warehouse**. Scanner output, benchmark reports, ADRs, runbooks, private chat transcripts, tokens, credentials, and local normalized contracts are not copied when a stable pointer is sufficient.

## When the Story index is written

`merge.py --apply` writes/updates the index only after integration proof succeeds for:

- successful `AGENT_MERGE`;
- resume after an MR is known to have been merged by a human;
- verified merge recovery.

Preview writes nothing. If merge is already factual but Story traceability cannot be written, return `TRACEABILITY_PENDING`; do not repeat the merge.

## Capability pointer

If a Story carries a POST_INTEGRATION obligation, integration registers the Story in local capability state. After capability finalization succeeds, a versioned capability record is written to:

```text
docs/agentic/records/team/<capability-id>/assurance.json
```

Each input Story traceability record then receives:

```text
downstream.capability_record_ref = docs/agentic/records/team/<capability-id>/assurance.json
```

The capability record stores the exact integrated composition, capability gates, product-acceptance reference, waiver/residual-risk refs, and release pointers. It does not delete or replace each Story's own traceability.

## Factual assurance

Pending assurance remains pending. `WAIVED` remains `WAIVED`. Integration does not automatically create `BUILD COMPLETE`.

`BUILD COMPLETE` may be written to the capability record only after the exact integrated target satisfies product capability acceptance and all mandatory engineering capability gates are validly closed.

## Publication

Helpers write Story/capability records into the coordinator records tree but **do not automatically create records commits**. The main Control Plane publishes records according to the team's branch/review policy. This prevents merge helpers from creating hidden records commits or mixing unrelated coordinator work.

## Boundary

Traceability/capability records do not grant deployment, release, activation, or production-migration authority.

## Legacy v0.4 marker

An existing schema-2 task does not have a v0.5 normalized Story contract. Runtime must not fabricate requirement/AC/contract digests to fill that gap.

If a legacy Story is compatible and actually integrated, its published record uses:

```text
record_type      = LEGACY_V04_TRACEABILITY
contract_status  = LEGACY_V04_CONTRACT
```

The record points only to Story/candidate/technical-review/MR/merge facts that genuinely exist. `build_complete_eligible` is always false. If current policy adds assurance after a human merge, the record shows `ASSURANCE_INCOMPLETE_AFTER_INTEGRATION`, not PASS.

A Story explicitly upgraded to schema 3 uses normal v0.5 Story traceability; the legacy backup remains immutable local history and is not copied into records as new authority.
