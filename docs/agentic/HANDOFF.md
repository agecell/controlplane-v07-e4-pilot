# The Backlog Preparation → Control Plane handoff interface — version 1

This document specifies a **shared contract between two packages**. It is versioned
independently of both: `handoff_schema_version` does not track Backlog Preparation's
version or Control Plane's. Either side may implement it, and the validator runs from
either side.

Read this before changing `bp-meta` on one side or `contract.py` on the other.

## Why it exists

Both packages had well-specified internal schemas and no specified interface between
them. In the v0.6 pilot, turning a Story card into a normalized contract took three
human judgements, and the same three recurred for every Story:

1. **Reference formats were incompatible.** A card said
   `"scope_ref": "STORY-001§Scope, Exclusions & Preserve"`. Control Plane's `REF_ID`
   allows no spaces and no `§`, so it had to become
   `backlog/stories/STORY-001.md#scope-exclusions-preserve` by hand — which required
   knowing both the heading and the anchor-slug convention.
2. **Four required fields had no source at all.** `build_start_ref`,
   `final_acceptance_ref`, `dependency_ref` and each obligation's `evidence_plan_ref` are
   mandatory in the contract and appeared nowhere in `bp-meta`. They were read off
   headings by hand.
3. **Types disagreed.** `"Tier 1"` against the integer `1`; one `traceability_root`
   string against a list.

Each was a chance to get an authority reference subtly wrong, and nothing checked the
result until much later.

## The shape of the interface

Backlog Preparation emits complete machine-readable metadata. Control Plane
**generates** the normalized execution contract from it. The contract is therefore a
**derived artifact** — hand-authoring it is no longer the supported path.

```text
Story card (bp-meta schema 2)
        │  validate_handoff()          ← runs on either side, before handoff is claimed
        ▼
   HANDOFF_READY
        │  contract.py handoff-generate --publication-sha …
        ▼
normalized Story contract schema 2     ← derived, deterministic, validated
```

## bp-meta schema 2

Required, and every reference already in a form `REF_ID` accepts:

| Field | Type | Notes |
|---|---|---|
| `artifact_type` | `"story"` | |
| `artifact_schema_version` | `2` | |
| `handoff_schema_version` | `1` | this interface |
| `story_id`, `capability_id` | identifier | |
| `canonical_backlog_path`, `story_card_path` | repo-relative path | |
| `canonical_revision` | string | the revision this card was written against |
| `traceability_root` | list of refs, ≥1 | **was a single string in schema 1** |
| `ac_ids` | list of refs, ≥1 | |
| `risk_tier` | integer 1–3 | **was `"Tier N"` in schema 1** |
| `scope_ref`, `preserve_ref`, `dependency_ref` | `path#anchor` | last one **new** |
| `build_start_ref`, `final_acceptance_ref` | `path#anchor` | **both new** |
| `engineering_impact` | the six lens keys | `N/A` \| `ROUTINE` \| `MATERIAL` |
| `engineering_owner` | null or `{owner_ref, attestation_provider, attestation_identity}` | |
| `required_assurance` | list of `{gate_id, stage, mode, evidence_plan_ref}` | `evidence_plan_ref` **new** |
| `merge_override` | null or `"DEVELOPER_REVIEW"` | |
| `preparation_state` | string | |
| `evidence_expectations` | list of `{ac_id, kind, method, stage, owner, initial_status}` | `kind` **new**; see below |

Carried and accepted, but not consumed when generating a contract: `requirement_refs`,
`dependencies`, `stop_conditions`, `implementation_state`, `exclusions_ref`,
`feature_id`.

**Anything else is rejected.** A tolerated unknown key is a typo nobody notices.

### Evidence kinds, and what they decide

Schema 1 described evidence in prose: `"method": "captured build log from documented
commands"`. Prose cannot be derived from, so schema 2 adds `kind` — a lowercase
identifier the **project** maps to the checks that evidence can satisfy:

```json
"merge_policy": {
  "mandatory_checks": ["build", "scope_check"],
  "default_checks": ["focused_tests"],
  "check_evidence_kinds": {"focused_tests": ["automated_test", "integration_test"]}
}
```

`mandatory_checks` is required of every Story and derived from nothing; **a Story cannot
affect it**. A `default_checks` entry is required only of a Story whose acceptance
evidence carries a kind mapped to it.

`kind` is a free identifier rather than a fixed vocabulary, because what a team can
produce evidence of is not ours to enumerate. What is not free is leaving one unmapped: a
`default_checks` entry with no `check_evidence_kinds` entry is a **configuration gap**
that blocks the merge. It is never treated as non-applicable and never silently skipped,
because an unmapped check is a question nobody answered rather than a decision anybody
made.

`method` stays, because a human still needs to know *how* the evidence is produced. It is
documentation, and nothing derives from it.

### `authority_pointer` — on the **Feature** card

Backlog Preparation publishes it; Control Plane reads it and never writes it. Writing it
would make Control Plane a second authority over a backlog it does not own.

```json
"authority_pointer": {
  "feature_id": "FEAT-001",
  "current_revision": "REV-03",
  "revision_published_at": "<full SHA>",
  "superseded_revisions": ["REV-01", "REV-02"]
}
```

`feature_id` and `current_revision` are required. `revision_published_at` is optional —
its absence degrades the verdict rather than faking one. `superseded_revisions` lets a
known-superseded revision be told apart from one the Feature has never heard of.

**It belongs on the Feature card, not the Story card.** A Story bound to a superseded
revision is exactly the condition being detected, so a Story cannot be trusted to say what
is in force: it would vouch for itself. One appearing on a Story card is accepted so that
nothing emitted between releases breaks, and it is not read.

Control Plane looks for the Feature card at `backlog_authority_ref` first, then the
remote-tracking target branch, then the local one, and the answer always names which
source replied. The target branch is one candidate, not the only one — in the v0.6 pilot
the backlog was maintained on a records branch that was never merged into the target, so
anchoring only there could not see the revision that had superseded the one under test.

Where no pointer can be read the verdict is `UNDETERMINED`: reported, never blocking, and
never read as current.

## Using it

```text
# Can this card produce a valid contract? Names what is missing.
python tools/agentic/contract.py handoff-check backlog/stories/STORY-101.md

# Derive the contract. Preview by default.
python tools/agentic/contract.py handoff-generate backlog/stories/STORY-101.md \
    --developer dev-a --sprint sprint-01 --publication-sha <FULL_SHA> --apply
```

`handoff-check` without a publication commit reads the **working tree** — that is the
gate to run before claiming BUILD READY. Generation always reads the card **as published
at `--publication-sha`**, and the two can disagree.

That distinction is not pedantry. Reading metadata from the working tree while binding
blob SHAs to a publication commit produces a contract that can state a revision the
commit does not contain, and the publication binding exists precisely to make that
statement true. It was caught against the pilot's own backlog, where the working tree
carried REV-03 while the Story's publication commit carried REV-02.

An existing registered contract is never silently replaced. Identical output reports
`CONTRACT_CURRENT`; different output is refused as `CONTRACT_DIFFERS` and **names the
fields that differ**, because that contract may already be bound to a task manifest.

## Schema 1 cards keep working

A card still on schema 1 is accepted for at least one release. What can be converted
mechanically is converted in memory, with a warning; nothing on disk is touched.

| Schema 1 | Handled how |
|---|---|
| `risk_tier: "Tier 1"` | read as `1`, with a warning |
| `traceability_root: "REQ-11"` | read as `["REQ-11"]`, with a warning |
| `scope_ref: "STORY-001§Some Heading"` | translated to `<card path>#some-heading`, with a warning |
| `dependency_ref`, `build_start_ref`, `final_acceptance_ref`, `evidence_plan_ref` | **reported as missing** |

The last row is the important one. Those fields have no source in schema 1, and they are
**never inferred**. Either raise the card to schema 2, or supply them explicitly:

```text
--ref "build_start_ref=backlog/features/FEAT-001.md#build-start-planning"
--ref "required_assurance[0].evidence_plan_ref=backlog/stories/STORY-002.md#required-assurance"
```

An override is recorded in the result as having come from the operator rather than from
the card. The judgement a human made invisibly in v0.6 is still a human judgement; it is
now named, validated and reviewable.

**A translated reference is only syntactically valid.** Nothing here proves the anchor it
names exists in the document. The pilot is its own example: a human wrote `#preserve`
where the mechanical slug of that heading is `#scope-exclusions-preserve-preserve`. Check
translated references, or override them.

## The two fields schema 1 cannot supply

A schema-1 card has no `kind` on its evidence expectations, and no Feature-card pointer.
Neither is invented:

* a missing `kind` is **reported as missing**, like the four references, and supplied with
  `--ref "evidence_expectations[N].kind=automated_test"`. Guessing one would silently
  decide which checks a Story must satisfy, which is the discretion F3/C2 removes;
* with no pointer published, current authority is `UNDETERMINED` and nothing blocks on it.

A contract migrated from schema 1 carries an empty `evidence_expectations` list and
therefore derives nothing. That is safe only because the v0.6 → v0.7 upgrade moves every
previously required check into `mandatory_checks`, which derive from nothing either.
