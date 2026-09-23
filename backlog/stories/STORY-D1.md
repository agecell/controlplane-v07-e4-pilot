# STORY-D1 — slugify helper

```bp-meta
{
  "artifact_type": "story",
  "artifact_schema_version": 2,
  "handoff_schema_version": 1,
  "story_id": "STORY-D1",
  "feature_id": "FEAT-D1",
  "capability_id": "FEAT-D1",
  "canonical_backlog_path": "backlog/features/FEAT-D1.md",
  "story_card_path": "backlog/stories/STORY-D1.md",
  "canonical_revision": "REV-01",
  "traceability_root": [
    "REQ-D1"
  ],
  "ac_ids": [
    "AC-D1-SLUGIFY",
    "AC-D1-TESTS"
  ],
  "risk_tier": 1,
  "scope_ref": "backlog/stories/STORY-D1.md#scope",
  "preserve_ref": "backlog/stories/STORY-D1.md#preserve",
  "dependency_ref": "backlog/stories/STORY-D1.md#dependencies",
  "build_start_ref": "backlog/features/FEAT-D1.md#build-start-planning",
  "final_acceptance_ref": "backlog/features/FEAT-D1.md#final-capability-acceptance",
  "engineering_impact": {
    "architecture": "ROUTINE",
    "scalability_nfr": "N/A",
    "security_privacy_data": "ROUTINE",
    "operability_recovery": "N/A",
    "human_ownership": "ROUTINE",
    "traceability_audit": "ROUTINE"
  },
  "engineering_owner": null,
  "required_assurance": [],
  "merge_override": null,
  "preparation_state": "BUILD READY",
  "evidence_expectations": [
    {
      "ac_id": "AC-D1-SLUGIFY",
      "kind": "automated_test",
      "method": "unittest case asserting the documented slug for three inputs",
      "stage": "BUILD",
      "owner": "story_builder",
      "initial_status": "NOT_RUN"
    },
    {
      "ac_id": "AC-D1-TESTS",
      "kind": "automated_test",
      "method": "the suite runs green from a clean checkout",
      "stage": "REVIEW",
      "owner": "reviewer",
      "initial_status": "NOT_RUN"
    }
  ]
}
```

## Outcome

`src/textutil.py` exposes `slugify(text)`, which lowercases its input and collapses every
run of non-alphanumeric characters into a single hyphen, with no leading or trailing
hyphen. It is covered by a unittest case.

## Scope

- add `src/textutil.py` with `slugify`;
- add `src/test_textutil.py` covering the acceptance criteria.

## Preserve

Nothing else in the repository changes. The kit under `tools/agentic/` and the
configuration under `.agentic/` are not touched.

## Dependencies

None.

## Acceptance criteria

- **AC-D1-SLUGIFY** — `slugify("Scope, Exclusions & Preserve")` returns
  `scope-exclusions-preserve`; `slugify("  A  B  ")` returns `a-b`; `slugify("--x--")`
  returns `x`.
- **AC-D1-TESTS** — the unittest suite passes from a clean checkout.
