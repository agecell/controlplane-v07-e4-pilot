# Task brief — v0.5

Fill from verified facts; replace placeholders. Do not publish secrets or lease tokens.

```text
TASK_ID: <Story>
ASSIGNMENT_ID / ATTEMPT: <unique local ID>
RUNTIME_INVOCATION_ID: <actual if available; otherwise NOT_AVAILABLE>
ROLE: <BUILD | FIX | REVIEW | QA | PREP | INTEGRATE>
LANE_ID / GENERATION: <from helper; integration execution may use coordinator>
WORKSPACE: <absolute path supplied privately>
BASE_SHA / CANDIDATE_SHA: <full SHA>
SOURCE_BRANCH: work/<developer>/<story>
REQUIREMENT / AC: <reference>
SCOPE / EXCLUSIONS / PRESERVE: <bounded contract>
CHECKS / QA: <names, commands, expected results, safe environment>
AUTHORITY: <local write/publish/merge mode; no deploy>
RECORDS_PATH: docs/agentic/records/<developer>/<story>/
RETURN_STATE: <expected result>
LEASE_TOKEN: <LOCAL_ONLY; omit from published brief>
```
