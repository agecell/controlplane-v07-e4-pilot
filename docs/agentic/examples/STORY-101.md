# STORY-101 — Request-status filter

Fictional example, not an automatic assignment. Owner: dev-a, sprint-01. An internal request portal has web and API code in one repository.

Outcome: users can filter requests by open/closed status. AC: filter reaches the API, invalid values are rejected, results match status, pagination preserves the filter, and existing access control remains unchanged. Scope: list component, endpoint, and related tests. Does not add export, new statuses, or permissions. Tier2. The list/pagination dependency contract is approved. Use project merge mode; the Story may be restricted to DEVELOPER_REVIEW when its owner requires it.

Source names and commands must come from real project configuration. Example code/results have not been executed in a production application.
