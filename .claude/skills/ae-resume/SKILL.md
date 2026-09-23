---
name: ae-resume
description: Verify checkpoint facts before continuing an assignment.
disable-model-invocation: true
---
# ae-resume — v0.7 Release Candidate / Pilot

Input: `<checkpoint-path>`. Read coordinator rules, project/assurance policy, assignment, records, pool registry, and local merge intents. Check actual disk state, HEAD/branch/worktrees, processes, remote/MR, and integration state. Do not restart work if the task already exists.

Task schema 3 must still match the registered contract digest/policy snapshot. Task schema 2 is a legacy exception: check current project/Tier additional assurance before FIX/REVIEW/QA/INTEGRATE; if a new obligation exists, STOP mutation and perform explicit contract binding + task upgrade. PREP may be used to prepare the migration.

Unknown lease/request → recovery, never auto-steal. If the server already merged, record the integration fact; a legacy Story that now has a new assurance obligation remains `ASSURANCE_INCOMPLETE`, not PASS/BUILD COMPLETE. If source changed, rebind exact review/checks/revalidation according to schema/policy.

$ARGUMENTS
