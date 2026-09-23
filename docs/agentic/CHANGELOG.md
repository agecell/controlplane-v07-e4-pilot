# Implementation Changelog — Control Plane v0.6

## v0.6 — Provider abstraction (GitLab and GitHub)

- added `tools/agentic/forge.py`: a `Forge` base class with `GitLabForge` and
  `GitHubForge`, and named operations over a normalized pull request. Provider-specific
  REST no longer reaches into `runtime.py`, `merge.py` or `guard.py`;
- project schema 3 → 4: `gitlab_host`/`gitlab_repo` become one `forge` block carrying
  provider, host and repo. No implicit migration from schema 3;
- `policy_state()` reports how much the forge itself enforces, as `FORGE_VERDICT`,
  `ENFORCED`, `NOT_CONFIGURED` or `UNAVAILABLE_PLAN`. The last two are degraded:
  `AGENT_MERGE` is refused, `DEVELOPER_REVIEW` carries the decision, and
  `MR_READY_FOR_HUMAN_REVIEW` records `forge_policy`;
- GitLab's verdict stays delegated, GitHub's is assessed from branch protection.
  Deliberately asymmetric: re-deriving GitLab's inputs would change behaviour for every
  existing GitLab project on a claim this build could not test;
- GitHub approvals are counted only against the candidate SHA, so an approval of an
  earlier commit never counts even where the repository does not dismiss stale reviews;
- GitHub `mergeable: null` is treated as a conflict — unknown state fails closed;
- guard denies the `gh` equivalents alongside `glab`;
- `config_ready` validates the forge block only on the `remote=True` path (F12), so
  `pool init`, lane lease and build run before a team has chosen a forge;
- attestation refs keep each provider's spelling: GitLab still writes
  `gitlab:mr:{iid}:note:{id}:user:{name}` byte for byte as v0.5 did, so pre-upgrade
  records still parse; GitHub writes `github:pr:{n}:comment:{id}:user:{login}`. Both
  `attestor_ref` and `evidence_ref` are derived from the adapter's own ref rather than
  rebuilt, so neither spelling can drift;
- the assurance attestation payload reports `allowed_forge_users`;
- traceability records carry `provider` and `repo` instead of an embedded `gitlab:`
  string, so a record stays readable after a team changes forge;
- fixed: `integration_proof` and both traceability writers read GitLab raw field names
  while being handed a normalized pull, which broke the post-merge proof path for both
  providers; the traceability validators rejected the `provider` key the builders had
  begun writing; `ae.config_gaps` and `guard.remote_ready` still gated on schema 3 /
  kit 0.5, the latter denying every remote operation under a v0.6 configuration;
- agent merge is restricted to merge results that can still be proven afterwards. On
  GitHub that is `merge_commit` only: squash and rebase rewrite history and GitHub does
  not report which method produced the result. The refusal happens before the merge;
- added `upgrade_v05_to_v06.py`, `V05_BASELINE.sha256` and two-provider tests
  (`test_v06_forge.py`). `upgrade_v04_to_v05.py` became `upgrade_v04_to_v06.py`, since
  v0.4 now migrates straight to schema 4;
- GitLab has no live verification in this build: no `glab` and no GitLab instance were
  available. GitHub policy states were verified against a real repository.

# Implementation Changelog — Control Plane v0.5

## Phase 12 — Final Validation / Claude Pilot Preparation

- final local suite: 356 PASS;
- added final runtime audit tests;
- allowed reviewed `contract.py` and `assurance.py` commands in Claude settings;
- hardened remote guard with assurance-policy approval;
- protected v0.5 assurance/traceability/migration/control policy docs;
- maintainer review required for project migration apply;
- added `PILOT.md` and pilot evidence template in package root;
- real Claude/GitLab/CI/two-developer pilot remains pending user execution.

## Phase 11 — Compatibility / Migration Completion

- added strict schema-2 legacy task recognizer without synthesizing v0.5 contract fields;
- added project/Tier additional-assurance compatibility check for legacy execution and merge;
- preserved v0.4 technical review as existing review semantics rather than duplicate assurance;
- blocked schema-2 FIX/REVIEW/QA/INTEGRATE when current project/Tier policy introduces additional assurance; PREP remains available for migration preparation;
- added explicit `project-migrate-v04` preview/apply path with clean-repo/no-active-lane gate, exact local backup, schema 3 output, and assurance left unapproved;
- added explicit `task-upgrade-v04` preview/apply path that binds a real normalized Story contract, preserves legacy evidence, and initializes new assurance as PENDING;
- prevented silent risk-Tier change for an existing legacy candidate and prevented broadening a legacy DEVELOPER_REVIEW restriction;
- added `LEGACY_V04_TRACEABILITY` / `LEGACY_V04_CONTRACT` factual integration record;
- already-integrated legacy Story with newly required policy assurance is recorded as `ASSURANCE_INCOMPLETE_AFTER_INTEGRATION`, never false PASS/BUILD COMPLETE;
- pool registry remains schema 2 and new BUILD remains schema 3;
- added 19 Phase-11 migration/compatibility tests; cumulative mechanical scenarios are now 341 PASS including installer.

## Phase 10 — Capability Assurance

- implemented schema-1 local capability assurance state under `.agentic/local/capabilities/<capability-id>.json`;
- capability state is created only when an integrated schema-3 Story has POST_INTEGRATION assurance obligations;
- registered exact Story integration inputs using candidate SHA, MR IID, merge-result SHA, integrated target SHA and Story traceability ref;
- rejected ambiguous aggregation across different canonical backlog revisions;
- implemented generic POST_INTEGRATION capability gates with `INTEGRATED_CAPABILITY` binding;
- added capability-level human attestation using `AE-ASSURE <gate> <capability> <binding_digest>`;
- added capability-level human waiver using exact bounded `AE-WAIVE ...` proof;
- implemented capability tool/control-plane evidence and satisfaction paths;
- capability aggregation allows human-understanding/operability/NFR to close once per exact capability composition when policy/ownership allows;
- new Story composition reopens stale capability gates and resets product acceptance instead of silently reusing old closure;
- implemented exact-target product capability acceptance evidence recording;
- implemented capability closure and `BUILD COMPLETE` only after product + mandatory engineering capability acceptance close;
- finalization writes `docs/agentic/records/team/<capability-id>/assurance.json` and binds it back to every Story task/traceability input;
- preserved release pointers without adding release authority;
- updated Control Plane agent guidance, ASSURANCE/TRACEABILITY docs and capability example template;
- added 14 Phase-10 capability tests; cumulative mechanical scenarios now 322 PASS including installer.

## Previous phases

Phase 1 Contract Core, Phase 2 Project Config Schema 3, Phase 3 Assurance-aware Precheck, Phase 4 Task Manifest Schema 3, Phase 5 Generic Assurance Registry, Phase 6 Human Attestation, Phase 7 Gate-local Revalidation, Phase 8 Merge Assurance Enforcement, and Phase 9 Traceability Index remain included in this cumulative package.
