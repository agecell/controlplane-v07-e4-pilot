# Sources and reference boundaries — v0.5

**Reference review date: 10 September 2026.** Team requirements and revision decisions are the basis of this package design. The Request Portal example is fictional; there is no claim of measured productivity improvement or real application outcomes.

- **H1 — Git worktree/checkout.** Linked-checkout semantics, branches, detached HEAD, and ignored-file protection. https://git-scm.com/docs/git-worktree ; https://git-scm.com/docs/git-checkout
- **H2 — Claude Code subagents/skills.** Markdown role definitions, tools/model, delegation/context, cwd, and worktree options. https://code.claude.com/docs/en/sub-agents ; https://code.claude.com/docs/en/skills
- **H3 — GitLab merge requests/notes/approvals.** MR identity/state, merge request, source SHA, and note reading. https://docs.gitlab.com/api/merge_requests/ ; https://docs.gitlab.com/api/notes/ ; https://docs.gitlab.com/api/merge_request_approvals/
- **H3b — GitHub pull requests, reviews, comments, checks and branch protection.** Pull-request identity/state, review state bound to `commit_id`, issue-comment reading, check-runs plus commit statuses, and the branch-protection settings the adapter reads instead of inventing a policy. https://docs.github.com/rest/pulls/pulls ; https://docs.github.com/rest/pulls/reviews ; https://docs.github.com/rest/issues/comments ; https://docs.github.com/rest/checks/runs ; https://docs.github.com/rest/commits/statuses ; https://docs.github.com/rest/branches/branch-protection
- **H4 — Claude Code hooks/permissions.** PreToolUse, permission settings, and tool boundaries. https://code.claude.com/docs/en/hooks ; https://code.claude.com/docs/en/permissions
- **H5 — Git conditional refs and forge branch APIs.** Expected-ref push/delete and branch metadata. https://git-scm.com/docs/git-push ; https://git-scm.com/docs/git-update-ref ; https://docs.gitlab.com/api/branches/ ; https://docs.github.com/rest/branches/branches

The seven-lane pool, schema model, lease registry, AGENT_MERGE/DEVELOPER_REVIEW modes, remote-lease protocol, exact approval notes, `ae-*` agent/skill names, helpers, and checklists are **implementations in this package**, not assumed built-in features. The references describe primitives used; they do not certify this kit implementation.

WORKFLOW/USER_GUIDE/PLAYBOOK are reading editions. OPERATING_RULES, AGENT_RULES, PROJECT_CONFIG, POOL, MERGE_POLICY, CLEANUP, and CONTROLS are agent/maintainer references. Approved config and organization policy still limit actions. VALIDATION records what was actually tested and what remains unproven. No private or real-project data is required by the examples.
