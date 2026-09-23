# Design decisions — Control Plane v0.6

These are starter-kit design choices, not claims that every capability is built into Claude Code.

| Decision | Rationale and consequence |
|---|---|
| Reusable pool, default 7 | Workspaces follow capacity, not backlog size. Pool size is configurable; active Builders remain limited separately. |
| Roles are not permanently attached to lanes | A folder may be used for build, review, QA, or prep according to need. Author history still restricts independent review. |
| Branch follows Story | Source remains reviewable/resumable after its lane is released. Records/evidence follow the Story. |
| AGENT_MERGE as configurable default | The team grants standing authority during setup. After gates close, the agent continues integration instead of requesting routine administrative permission. |
| DEVELOPER_REVIEW alternative | Projects/Stories requiring a human path can stop at MR without changing the build/review flow. |
| Separate local and remote leases | Local lease prevents duplicate pool use; remote lease serializes integration clients following the protocol. Neither is a sandbox or a lock on human UI. |
| Separate source and records | Saving reports does not mutate a frozen candidate. |
| Clean branches; preserve pool | Branches do not become permanent archives, while lane folders remain reusable. |
| One automatic-fix cycle by default | Avoids unproductive loops. Remaining defects require diagnosis and a next decision. |
| Proportional review/evidence | Risk Tier and delta determine checks; green tests or agent count do not guarantee quality. |

Changes across kit versions require code/config/agent/docs migration together. The installer does not silently overwrite existing configuration. Package construction itself makes no change to a product repository.
