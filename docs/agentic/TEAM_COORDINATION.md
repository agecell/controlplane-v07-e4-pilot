# Coordination for 2–3 developers — v0.5

Each developer has their own clone/coordinator/pool. A local pool does not assign the team backlog. Sprint assignment comes from the team's existing process. One Story has one owner and one clear set of source branch(es) and MR(s).

Record claims through the team's official Issue/board/coordination mechanism: owner, Story, shared file/contract, dependency, active writer slot, release condition, and approver. A shared/high-risk claim lasts until integration or explicit handoff, not merely until Builder finishes. The absence of a remote branch is not evidence that an area is free. An idle role/slot is not a reason to take another developer's Story.

Default for pilot: 1 local Builder, 2 repository writers, 1 shared/high-risk writer. Repository-wide limits are decided by the team and verified by main through coordination sources; a local helper cannot count all laptops. Add parallelism only when contracts/files/resources are independent and expected benefit exceeds conflict/rework risk. PREP/read-only work may continue while coding must wait.

## Merge turn

AGENT_MERGE clients use one remote integration ref `ae/locks/integration`. Atomic create and expected delete protect the lease among cooperating clients; the marker is released after the result is verified. There is no auto-expiry. If the holder crashes, a maintainer checks intent/MR/source/process state before recovery. Never steal the lock for speed.

Actors outside the helper, including human reviewers who merge, must still follow the team turn. This ref is not a transaction lock on the protected target. Do not claim that the human UI is automatically blocked. Restrict server permissions, ensure no other automation merges outside the protocol, and test races in a practice repository. Pipeline effects of the lock branch are reviewed during setup.

In DEVELOPER_REVIEW, the MR note explains that a developer/maintainer owns the next step. The agent never approves on behalf of a human. After a human merges, main reads the actual result in an active/resumed session and performs authorized cleanup.

## Handoff

Publish source commit and records, record the new owner in the team coordination system, then ensure only one active owner remains. A new machine uses its own pool; never copy absolute paths, lease tokens, or the registry as authority. Recover unsaved unique source first. Releasing a local lane and releasing a team claim are separate actions.
