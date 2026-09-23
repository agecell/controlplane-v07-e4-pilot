# Control Plane v0.6 configuration and local data

`project.json` is the versioned schema-4 configuration. The `assurance_policy` block must be reviewed by a maintainer; the distribution ships with `approved=false` and does not enable blanket assurance gates.

The `forge` block names the provider (`gitlab` or `github`), host and repository. It ships as `__CONFIGURE__` because the kit must not pick a forge for a team, and it is validated only for remote work — `pool init`, taking a lane and building all run before a forge is chosen. `forge.host` and `forge.repo` must identify the same repository as the Git remote.

`local/` is ignored by Git and contains normalized contracts, task/evidence runtime state, capability state, migration backups, and local merge intents. Do not publish tokens/credentials/local state as authority.

The pool registry remains in common Git metadata at `agentic/pool.json` schema 2; upgrading **does not** recreate lanes. New BUILD work always uses task schema 3, which v0.6 did not change — only the project configuration moved to schema 4. Existing task schema 2 is only a compatibility exception as described in `docs/agentic/MIGRATION.md`.

Do not fabricate approval/attestation, edit registry/status to bypass a gate, or convert legacy evidence into an assurance PASS.
