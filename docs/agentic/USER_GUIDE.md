# User guide for the team — Control Plane v0.6

This guide is for developers and team members who will use the system without needing to understand every internal helper.

## 1. What do I need to do?

For daily use, you mainly provide an approved Story and respond only when a genuine human decision is needed.

### Give a normal instruction

```text
I am dev-a. Work on STORY-101 from backlog/stories/STORY-101.md.
Use Control Plane v0.6 and the project's merge mode.
Do not deploy or release.
```

Or use:

```text
/ae-start backlog/stories/STORY-101.md dev-a assignment-01
```

### What should happen?

The coordinator reads authority, checks readiness, leases a reusable lane, invokes Builder, records the candidate, invokes an independent Reviewer when required, requests QA proof/fix when needed, handles assurance, prepares the MR, and follows the configured merge mode.

You should receive concise status and blockers, not every internal raw log.

## 2. Running the first Story

### Step 1 — point to the source of work

Give the exact Story/backlog path and your developer identity.

### Step 2 — confirm understanding

Read the coordinator's short summary. If the Story asks for a filter but the agent starts adding unrelated export/permissions, correct the scope before the change expands.

### Step 3 — let technical work proceed

Builder writes/tests on a leased lane. The coordinator saves the result and releases the lane safely. Reviewer validates the exact candidate from another independent lane. QA is used only when additional proof is needed. Named findings go back to Builder for a bounded fix.

Do not manually change a leased lane's branch or edit its folder while an assignment is active.

### Step 4 — continue according to merge mode

- `AGENT_MERGE`: after all gates are valid, the coordinator/integrator may merge through the helper.
- `DEVELOPER_REVIEW`: the coordinator stops at an MR ready for human review.

### Step 5 — read the final result

Look for: change summary, AC, test evidence, review verdict, exact commit, MR, integration proof, traceability, cleanup, and remaining work. `NO_CHANGE_NEEDED` is a valid outcome when the requirement was already satisfied.

To ask for status without starting new work:

```text
/ae-status STORY-101
```

## 3. Reading reports and knowing when to intervene

A useful report answers:
- What Story/package is this?
- What state is it in?
- Which exact candidate/MR/target is involved?
- What passed, failed, was not run, or is pending?
- Is there an assurance or human decision blocker?
- What cleanup remains?
- Who owns the next action?

### When do I need to decide?

Intervene when the system reports real product ambiguity, risk/authority decisions, required human assurance, organization approval, or DEVELOPER_REVIEW merge responsibility. Do not manually approve routine internal state that policy already authorizes.

## 4. Giving my sprint backlog

You may give the Control Plane a bounded list assigned to you. It may continue between independent/eligible Stories according to package instructions, but it must not reprioritize the team's sprint.

Example:

```text
Assigned to dev-a:
1. STORY-201 — priority 1
2. STORY-202 — depends on 201
3. STORY-203 — independent
When 202 is blocked on 201, 203 may proceed.
```

### Mode behavior

With AGENT_MERGE, dependent work may continue only after upstream integration is verified. With DEVELOPER_REVIEW, a dependent Story waits for human merge of its dependency unless an explicit safe preparation path exists.

## 5. Giving one complete feature

A feature may span multiple Stories. Keep Story-level source/review/merge identity; do not collapse everything into one giant unreviewable change.

### How is it divided?

Split around coherent behavioral/technical boundaries and dependencies. The Control Plane tracks each Story. Only when the feature has a true downstream obligation is capability assurance created across the integrated composition.

### When is the feature complete?

`BUILD COMPLETE` requires required product capability acceptance plus mandatory engineering capability assurance for the exact integrated target. It still does not mean deployed/released.

## 6. Which folder do I work in?

You normally open Claude from the repository root/coordinator checkout. Agent work happens in reusable linked worktrees:

```text
<repo>/.claude/worktrees/lane-01
<repo>/.claude/worktrees/lane-02
...
```

Do not create one folder per Story. Do not permanently map lane-01=Builder, lane-02=Reviewer. Lanes are reusable workspaces; roles are temporary assignments.

### You do not need seven manual sessions

The pool is capacity. The Control Plane leases only what it needs. Idle lanes are normal.

## 7. Stop today, continue tomorrow

### When you want to stop

Use:

```text
/ae-checkpoint STORY-101
```

The system records factual state and stops new dispatch. It must not force-clean work simply to look finished.

### When continuing

Use:

```text
/ae-resume <checkpoint-path>
```

Resume verifies actual source, lane, process, MR, and integration state before doing anything new.

### After a timeout

Do not assume the previous operation failed. The system should inspect server/local state and recover rather than blindly repeat requests.

### Moving developer or computer

Publish source and records, establish a new team owner claim, and use the new machine's own pool. Never copy local lease registries/tokens as authority.

## 8. What is cleaned after merge?

The Story branch may be removed when proven safe. Reusable lane folders stay.

### Order to understand

```text
merge verified
→ INTEGRATED
→ traceability/evidence preserved
→ Story leases released
→ safe branch cleanup
→ CLEANUP_COMPLETE or CLEANUP_PENDING
```

### What can hold cleanup?

Open dependency, QA retention, unique work, changed SHA, active checkout/lease, permission failure, or ambiguous proof. A pending cleanup item should name its reason, owner, and next trigger.

## 9. Maintainer section: installation and project mode

Install through a reviewed setup change. Configure the real target branch, the forge block (provider, host, repository), merge method, CI/check commands, team coordination reference, and approval boundaries. Keep assurance unapproved until it is actually reviewed.

For a first pilot, `DEVELOPER_REVIEW` is easier to observe. Test `AGENT_MERGE` separately after the basic flow is understood.

## 10. Test first and know the limits

Start with Pilot 0 (read-only startup) and Pilot 1 (one routine Story). Expand only after the team can explain the flow. Python helpers and Markdown rules are controls, not a perfect sandbox. Real forge/CI/server permissions and organization policy remain essential. On GitHub in particular, agent merge requires branch protection the forge actually enforces.
