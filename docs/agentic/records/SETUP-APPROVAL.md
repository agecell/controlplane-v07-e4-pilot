# Setup approval — Control Plane v0.7 E4 verification

Approved by: agecell (maintainer of this verification repository)

This repository exists to prove the GitHub write path for Control Plane v0.7 Wave D. It
holds no product code. Standing approvals recorded here:

- `configuration_approved` — the project configuration was reviewed;
- `remote_actions_ready` — push and pull-request effects are permitted;
- `merge_policy.approved` — `AGENT_MERGE` is permitted, with `human_review_required`
  true and `agecell` as the only approver;
- `cleanup.approved` — `SAFE_MERGED` controlled cleanup is permitted;
- `assurance_policy.approved` — the default assurance policy is accepted.

`backlog_authority_ref` is set to `refs/remotes/origin/main`, because that is where this
project publishes its backlog.
