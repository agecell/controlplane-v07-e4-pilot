# Merge policy — Control Plane v0.6 Release Candidate

## Two modes are retained

`AGENT_MERGE` and `DEVELOPER_REVIEW` retain the core v0.4 semantics. A task may only tighten the mode to `DEVELOPER_REVIEW`; it may not broaden project authority.

Merge always happens through an MR. Merge is not deployment, release, production migration, or destructive-data authorization.

Throughout this kit, "MR" means the forge's change-proposal object: a merge request on GitLab, a pull request on GitHub. The field names (`mr_iid`) and result names (`MR_READY_FOR_HUMAN_REVIEW`) keep the v0.5 spelling on both.

## AGENT_MERGE is now assurance-aware

Before an agent can execute merge, all previous controls still apply:

- exact candidate SHA;
- source/remote/MR/target identity;
- exact independent technical review appropriate to the Tier;
- named checks/CI/QA — see **Which checks apply** below;
- scope/dependency/blocker/remote-effect checks;
- project human merge approval when required;
- current mergeability/server rules;
- integration lease/source-SHA guard.

v0.5 adds:

- valid normalized Story contract + publication binding;
- current assurance policy/effective project + Tier + Story obligations;
- all mandatory PRE_MERGE assurance validly closed;
- current server-backed human/composite attestation;
- valid bounded waiver when a gate uses an exception;
- no mandatory gate in `NEEDS_REVALIDATION`.

v0.6 adds two forge conditions:

- **the forge itself must enforce a merge policy, and that policy must be satisfied.**
  On GitLab the kit delegates to the forge's own mergeable verdict, as v0.5 did. On
  GitHub it reads branch protection — the repository's declaration of required reviews
  and required checks — and verifies each against the candidate. Where the forge declares
  no policy at all, `AGENT_MERGE` is **refused**: nothing on the forge would prevent
  someone merging around Control Plane, so the kit would be the only guard and could be
  bypassed out of band. The adapter never substitutes a policy of its own, because that
  would let two projects run at different real safety levels without either noticing.
  `DEVELOPER_REVIEW` remains fully supported there, and records `forge_policy`;
- **the configured `merge_method` must produce a result that can still be proven.**
  `integration_proof` ties the merged result back to the candidate SHA by ancestry, or by
  an exact blob/mode delta comparison for a squash. GitHub's squash and rebase merges
  rewrite history and GitHub reports no field identifying which produced the result, so
  on GitHub only `merge_commit` qualifies. This is asserted before the merge, while it is
  still reversible, rather than discovered afterwards.

On GitHub, an approving review counts only when its `commit_id` equals the candidate SHA.
An approval of an earlier commit never counts, regardless of whether the repository
dismisses stale reviews.

```text
SATISFIED → closed
WAIVED    → closed only while the waiver remains valid; still visibly WAIVED
PENDING / IN_PROGRESS / FAILED / BLOCKED / NEEDS_REVALIDATION → block
```

Human merge approval:

```text
APPROVE <FULL_CANDIDATE_SHA>
```

is different from assurance:

```text
AE-ASSURE <gate_id> <scope_id> <binding_digest>
```

and from waiver:

```text
AE-WAIVE <gate_id> <scope_id> <binding_digest> <waiver_digest>
```

One human may hold multiple roles when policy permits, but these three evidence meanings remain distinct.

## DEVELOPER_REVIEW

The agent does not merge. The MR may be ready for human review while pending assurance remains explicitly visible. This mode does not remove assurance requirements and does not make a human merge equivalent to assurance PASS.

If an MR is merged outside the helper while assurance is still incomplete, resume may only state the factual `INTEGRATED` result; BUILD COMPLETE still depends on correct assurance/capability acceptance.

## Current-state recheck

Merge preflight is rerun after the shared integration lease is acquired. Current target, MR state, CI, contract/policy, assurance binding, server-backed human proof, and waiver validity are rechecked before the merge request is sent.

## Recovery / cleanup remains v0.4-compatible

Lost-response recovery, integration-lock ownership, normal/ff/squash proof, branch cleanup, and reusable lane-folder semantics are preserved.

## Which checks apply, and why

v0.6 had one list, `merge_policy.required_checks`, demanded of every Story whether or not
that Story could produce the evidence. The pilot met the consequence directly: a Story
with no automated tests could only be let through by deleting `focused_tests` from the
project policy — weakening it for **every** Story rather than for the one that could not
satisfy it.

Schema 5 splits it in two, with deliberately unequal authority:

```json
"merge_policy": {
  "mandatory_checks": ["build", "scope_check"],
  "default_checks": ["focused_tests"],
  "check_evidence_kinds": {"focused_tests": ["automated_test", "integration_test"]}
}
```

- **`mandatory_checks`** is required of every Story and derived from nothing. **A Story
  cannot affect this list in any way** — it can neither remove an entry nor add one.
- **`default_checks`** is required only of a Story whose acceptance evidence carries a
  kind mapped to it in `check_evidence_kinds`.

Merge preflight reports every check with a status and a reason:

| Status | Meaning |
|---|---|
| `APPLIED` | required — either `PROJECT_MANDATORY`, or `EVIDENCE_EXPECTED` naming the ACs and kinds that applied it |
| `NOT_APPLICABLE` | no acceptance criterion expects evidence of a kind mapped to it, so this Story cannot satisfy it and is not required to |
| `CONFIGURATION_GAP` | a default check with **no** `check_evidence_kinds` entry |

**A configuration gap blocks the merge.** It is never treated as non-applicable and never
silently skipped: an unmapped check is a question nobody answered, not a decision anybody
made. Map it, or move it to `mandatory_checks`.

Applicability is resolved from the Story's acceptance evidence as it was **bound at BUILD
time**, carried in the task's contract snapshot, so what a Story must prove cannot drift
after binding. Project policy is read **current**, so a project that tightens its
mandatory checks takes effect immediately.

## Is this contract still the authority in force?

Publication binding proves a contract refers to a state that really *was* published. Git
history is immutable, so that check succeeds forever — including after the Story card has
been revised, corrected or withdrawn. In the v0.6 pilot, FEAT-001 was raised REV-02 →
REV-03 precisely to fix a wrong attestation identity, and the contract still bound to
REV-02 kept passing precheck.

Precheck now answers a second, different question, from two signals of unequal authority:

- **the published pointer.** Backlog Preparation states on the Feature card which revision
  is in force (`authority_pointer`; see `HANDOFF.md`). **This is the only thing that may
  establish `CURRENT`.**
- **the mechanical floor.** Git is asked whether anything has touched the authority files
  since the publication commit. It may raise an alarm and may never clear one — a file
  nobody touched can still have been superseded elsewhere.

| Verdict | When | Blocks new execution? |
|---|---|---|
| `CURRENT` | the pointer names the revision this contract is bound to, and nothing has moved | no |
| `SUPERSEDED` | the pointer names a different revision, **or** the files changed without the revision being raised, **or** the floor detected a change and no pointer could be read | yes |
| `UNDETERMINED` | no pointer could be read and the floor found nothing | no |

`UNDETERMINED` is reported as unknown and never as current. Where a team publishes no
authority pointer, the floor is all there is, and it can only refuse — never approve.

**Completed work is never re-judged.** This gates a contract being used to authorize *new*
execution. A traceability record written under an earlier revision stays valid and keeps
naming the revision it was actually bound to.

The Feature card is looked for at `backlog_authority_ref`, then the remote-tracking target
branch, then the local one; the result names which source replied. A backlog is not always
maintained on the branch the code integrates into — in the pilot it was not.

