# Assurance runtime — Control Plane v0.6 Release Candidate

Engineering Assurance authority remains in the **Engineering Assurance Standard v0.2** and the canonical backlog/Story contract. This document describes the v0.5 Release Candidate runtime integrated through capability assurance and legacy compatibility.

## Principles

- fast path creates no additional gate or capability registry;
- `technical_review` continues to use the v0.4/v0.5 `review` object as the single source of truth;
- PRE_MERGE assurance is stored per Story and enforced by `merge.py` for `AGENT_MERGE`;
- human/composite assurance uses a server-backed forge comment — a GitLab note or a GitHub pull-request comment — bound to the exact digest;
- merge approval `APPROVE <SHA>` is different from assurance note `AE-ASSURE ...`;
- `WAIVED` is an auditable exception, not PASS;
- candidate changes are revalidated per gate, not by rerunning the full Story;
- POST_INTEGRATION assurance is created **only when a downstream obligation exists**;
- human-understanding/operability/NFR may be aggregated at capability level when contract/policy permits;
- `BUILD COMPLETE` does not mean deployed or released.

## Story PRE_MERGE assurance

A schema-3 task manifest stores only effective PRE_MERGE gates that are required. `AGENT_MERGE` rechecks current contract, policy union, candidate binding, evidence, current allowlist/human attestation, waiver, CI/MR/target, and integration lease.

Validly closed:

```text
SATISFIED
WAIVED  (only while the current bounded waiver remains valid)
```

Blocking:

```text
PENDING
IN_PROGRESS
FAILED
BLOCKED
NEEDS_REVALIDATION
invalid / expired WAIVED
wrong contract / policy / candidate / attestor
```

`DEVELOPER_REVIEW` may hand pending assurance to a human without removing the obligation. A human/manual merge outside the helper does not automatically make assurance complete.

## Capability POST_INTEGRATION assurance

After a Story is integrated and its traceability index exists, `merge.py` registers capability state **only when the task has a `POST_INTEGRATION` obligation**.

Local state:

```text
.agentic/local/capabilities/<capability-id>.json
```

If there is no POST_INTEGRATION gate, the file is not created. This keeps the fast path close to v0.4.

Capability state binds:

- capability ID;
- canonical backlog + revision;
- exact integrated target SHA;
- Story inputs + candidate/MR/merge result + Story traceability pointer;
- only required POST_INTEGRATION gates;
- product capability acceptance evidence;
- downstream release pointers;
- `build_complete`.

When another Story joins the same capability, the integrated composition changes. Previously closed capability gates are reopened for the exact new composition, product acceptance returns to `PENDING`, and old evidence may be reused only after valid revalidation. Critical PRE_MERGE security/architecture gates cannot be moved to capability level.

### Aggregation

Human understanding, operability/recovery, and NFR/performance may become one capability-level gate covering multiple Stories when:

- capability ID and canonical backlog/revision are the same;
- covered Story scope is explicit;
- integrated target SHA is clear;
- policy/contract permits aggregation;
- gates that truly require PRE_MERGE closure were completed before Story merge.

For an `ENGINEERING_OWNER` capability gate, all aggregated Stories must resolve to one consistent engineering-owner identity on the configured forge. If ownership differs, policy must use an appropriate authorized capability attestor; the helper must not guess one owner.

## Capability human attestation

The generic format remains:

```text
AE-ASSURE <gate_id> <capability_id> <binding_digest>
```

The note is posted on a representative/final MR that remains verifiable. The binding digest—not MR status—binds the exact capability composition. The helper verifies server author, allowed identity, exact note body, current integrated target, gate mode, and current capability digest.

## Capability waiver

Waiver remains human-only:

```text
AE-WAIVE <gate_id> <capability_id> <binding_digest> <waiver_digest>
```

`WAIVED` may close engineering readiness only when project policy makes the gate waivable, the risk owner is valid, binding/expiry match, required compensating controls exist, and no hard external control still blocks. The versioned record still shows `WAIVED`.

## Product capability acceptance

Engineering assurance must not invent product acceptance. Runtime only **records** a stable evidence/authority reference for a specific integrated target:

```text
python tools/agentic/assurance.py capability-product-accept \
  --capability <FEAT-ID> \
  --evidence-ref <stable-product-acceptance-ref> \
  --actor-ref <authorized-actor-ref> \
  --apply
```

The helper does not infer product truth from the reference string. Product/behavior acceptance remains governed by the canonical backlog and organization/project authority.

## BUILD COMPLETE

Final closure requires both sides:

```text
Product capability acceptance = SATISFIED
AND
all required capability engineering gates = SATISFIED / valid WAIVED
```

Preview:

```text
python tools/agentic/assurance.py capability-finalize --capability <FEAT-ID>
```

Apply performs server re-verification for human-owned capability gates/waivers and writes the versioned record:

```text
docs/agentic/records/team/<capability-id>/assurance.json
```

It then binds that pointer to the local tasks and Story traceability indices that are inputs to the capability.

Output `BUILD_COMPLETE` means required product + engineering acceptance has closed for the exact integrated composition. It **does not** grant deployment/release/migration authority.

## Capability commands

```text
python tools/agentic/assurance.py capability-status --capability <FEAT-ID>
python tools/agentic/assurance.py capability-check --capability <FEAT-ID> [--verify-server]
python tools/agentic/assurance.py capability-evidence --capability <FEAT-ID> --gate <gate> --evidence-ref <ref> [--apply]
python tools/agentic/assurance.py capability-satisfy-tool --capability <FEAT-ID> --gate <gate> --attestor-ref <ref> --evidence-ref <ref> [--apply]
python tools/agentic/assurance.py capability-human-request --capability <FEAT-ID> --gate <gate>
python tools/agentic/assurance.py capability-satisfy-human --capability <FEAT-ID> --gate <gate> --note-id <id> [--evidence-ref <ref>] [--apply]
python tools/agentic/assurance.py capability-waiver-request ...
python tools/agentic/assurance.py capability-apply-waiver ...
python tools/agentic/assurance.py capability-product-accept ...
python tools/agentic/assurance.py capability-finalize --capability <FEAT-ID> [--apply]
```

## Publication boundary

Local capability state is runtime state. After finalization, the capability assurance record and updated Story traceability records live in the records tree and still must be published according to records-branch policy. The helper does not create hidden records commits.

## Legacy schema-2 assurance

A schema-2 task does not receive a fabricated assurance registry. The compatibility resolver calculates only current **project + risk-Tier additional assurance**; exact v0.4 `technical_review` remains the existing review mechanism.

- no additional assurance → legacy Story may continue with v0.4 semantics;
- additional assurance exists → execution/agent merge is blocked until the Story is bound to a normalized contract and the task is explicitly upgraded to schema 3;
- after upgrade, new gates start `PENDING`; old review/test evidence does not automatically become assurance PASS.
