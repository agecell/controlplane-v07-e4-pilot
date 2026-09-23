# Control Plane v0.6 reading map

**Start with `START_HERE.md`.** For setup/upgrade/first pilot, continue to `QUICKSTART.md`. After that, open other documents only as needed.

- `WORKFLOW.md` — team workflow concepts and lane/Story/role mechanics.
- `USER_GUIDE.md` — daily usage.
- `PLAYBOOK.md` — situation-based recipes.
- `PROJECT_CONFIG.md` — schema-4 configuration, including the `forge` block.
- `ASSURANCE.md` — Story/capability gates and human attestation.
- `TRACEABILITY.md` — requirement → merge/capability records.
- `MIGRATION.md` — legacy v0.4 compatibility.
- `PILOT.md` — full pilot matrix.
- `POOL.md`, `MERGE_POLICY.md`, `CLEANUP.md`, `CONTROLS.md` — runtime/guardrail details.

Implementation Phase 1–12 are kit build history and **not the user reading order**.

## v0.5 Release Candidate

New BUILD work always uses schema 3 and is bound to a normalized Story contract. An existing schema-2 Story is a compatibility exception. Fast path does not receive specialist/capability gates automatically. `BUILD COMPLETE` does not mean deployed/released.
