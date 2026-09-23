# Project configuration — Control Plane v0.6

**Active schema: 4. Kit: 0.6.** Fill this before the pilot. Placeholders are not real project values. `.agentic/project.json` remains the versioned configuration read by the coordinator.

## The forge block (new in v0.6)

v0.6 replaces the v0.5 pair `gitlab_host` / `gitlab_repo` with one block naming the
provider as well:

```json
"forge": {
  "provider": "__CONFIGURE__",
  "host": "__CONFIGURE__",
  "repo": "__CONFIGURE__"
}
```

- `provider` — `gitlab` or `github`. The distribution ships `__CONFIGURE__` on purpose:
  the kit must not pick a forge on a team's behalf.
- `host` — `gitlab.example.com`, or `github.com`. A port is allowed; a scheme is not.
- `repo` — `group/project` on GitLab, `owner/name` on GitHub.

`host` and `repo` must identify the same repository as the Git remote. `verify_remote`
checks the fetch and push URLs against them before any remote effect and refuses if they
disagree, so a typo surfaces as a refusal rather than as an operation against the wrong
repository.

**The forge block is required only for remote work.** `runtime.config_ready(cfg)`
validates it only on the `remote=True` path, so `pool init`, taking a lane and building
all run before a team has chosen a forge. This is deliberate: requiring one up front
meant a team evaluating the kit had to either commit prematurely or put placeholder
values into a configuration it had already approved.

## The assurance policy block (from v0.5)

All v0.4 fields remain. v0.5 added one top-level `assurance_policy` block so a project can define baseline assurance without forcing every Story through unnecessary gates.

```json
"assurance_policy": {
  "approved": false,
  "approval_ref": "__CONFIGURE__",
  "project_required_gates": [],
  "tier_required_gates": {"1": [], "2": [], "3": []},
  "human_attestors": {},
  "waiver_policy": {"default_waivable": false, "gates": {}},
  "custom_gates": {}
}
```

## Configuration principles

- `assurance_policy.approved=false` in the distribution is a **safety default**. A maintainer must review policy before execution.
- `project_required_gates` is only for gates that truly apply universally to the project.
- `tier_required_gates` may remain empty; use it only for simple deterministic Tier rules.
- A Story may add stricter assurance through its contract, but may not remove project/Tier gates.
- `human_attestors` is the server-identity allowlist for human-owned gates/modes that actually require it.
- `human_understanding` does not use a global allowlist by default; the attestor comes from the Story/capability engineering owner.
- `waiver_policy.default_waivable` must remain `false`; exceptions are opened per gate by an authorized human risk owner.
- `custom_gates` use IDs `project_defined:<id>` and must at least point to `policy_ref`; generic assurance registry enforces the runtime semantics.

## Migration to schema 4

There is no implicit migration from schema 3 or schema 2; `runtime.config()` refuses
both by name. A project already on v0.5 uses the package-level helper:

```text
python upgrade_v05_to_v07.py --repo <repo>          # preview
python upgrade_v05_to_v07.py --repo <repo> --apply
```

It folds `gitlab_host` and `gitlab_repo` into the forge block unchanged and writes
`provider: "gitlab"` — a fact read off the source, since v0.5 supported no other forge,
not a default chosen for you. Standing merge, remote, cleanup and assurance approvals
carry across untouched, and `merge_mode` is left alone.

**Moving to GitHub is a separate maintainer decision**, done by hand after the upgrade:
change `forge.provider` and `forge.repo`, and re-point the Git remote to match. Changing
forge changes where merge authority lives, so no script does it silently.

A schema-2 (v0.4) project migrates straight to schema 4 in one step. To inspect a
proposed config without writing:

```text
python tools/agentic/contract.py project-migrate-preview .agentic/project.json
```

The output shows a schema-4 proposal with `assurance_policy.approved=false` and the
forge block folded in. A maintainer reviews the result, policy, and human/security
effects, then updates the active file through normal change/review. Standing
`merge_mode`, merge approval, remote-action approval, and cleanup approval are **not
automatically changed** by preview.

After the active schema-4 file has been reviewed:

```text
python tools/agentic/contract.py project-validate .agentic/project.json
```

`runtime.config_ready()` continues to reject execution while `configuration_approved` or `assurance_policy.approved` is not true with a legitimate approval reference.

## Fast-path default

The new project template does not add blanket assurance gates:

```text
project_required_gates = []
tier_required_gates 1/2/3 = []
```

This keeps routine Stories close to the v0.4 lifecycle. Additional assurance appears only when project policy or the Story contract requires it.

## How much the forge enforces

`merge_mode = AGENT_MERGE` requires a merge policy the **forge itself** enforces. The
kit reads that rather than assuming it, and reports one of four states:

```text
FORGE_VERDICT      GitLab computed its own mergeable verdict; the kit delegates to it
ENFORCED           requirements read from GitHub branch protection and verified
NOT_CONFIGURED     GitHub offers branch protection; the target branch has none
UNAVAILABLE_PLAN   this plan or visibility does not offer branch protection at all
```

The last two are degraded: nothing on the forge prevents a direct push to the target
branch, so `AGENT_MERGE` is refused and `DEVELOPER_REVIEW` carries the decision. The
`MR_READY_FOR_HUMAN_REVIEW` result records `forge_policy` so the degradation appears in
the record. The adapter never substitutes a policy of its own — that would let two teams
run at different real safety levels without either noticing.

On GitHub, `merge_method` must be `merge_commit` for `AGENT_MERGE`: squash and rebase
merges rewrite history and GitHub does not report which method produced the result, so
the merged commit could not be tied back to the candidate SHA afterwards.

## Authority boundary

Schema 4 does not grant deploy/release/migration permission. Protected branch, server approval, CI, merge policy, remote effects, and organization policy remain authoritative. `assurance_policy` must not be used to weaken those hard controls, and neither must the forge block.

## Explicit project migration

The installer never silently converts a schema-2 or schema-3 config; it names the upgrade script for the version it finds and stops. Use `contract.py project-migrate-preview` or `project-migrate-v04` for a v0.4 source; apply only to the exact `.agentic/project.json`, with a clean repository and no active lane lease.

Migration preserves standing merge/remote/cleanup decisions. From v0.4 it adds `assurance_policy` with `approved=false`, which a maintainer must review and give a separate approval reference before the runtime is ready. Placeholders are carried through unchanged: migration reshapes a configuration, it never completes one.
