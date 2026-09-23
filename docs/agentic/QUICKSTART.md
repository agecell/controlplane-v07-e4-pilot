# Step-by-step guide — Control Plane v0.6

This is the normal user path. Do not start from the Implementation Phase documents.

# A. One-time setup

## Step 1 — Make sure the tools are available

From a terminal:

```text
claude --version
python --version
git --version
glab version   # if your project is on GitLab
gh --version   # if your project is on GitHub
```

Practical minimum:
- Claude Code is available;
- Python 3.10+ is available;
- Git is available;
- the CLI for **your** forge is available if you will use remote/pull-request/merge
  helpers: `glab` for GitLab, `gh` for GitHub. You need only the one you configure;
- the project repository already exists and can be opened with Git.

## Step 2 — Choose the installation path

### A. Project has never used Control Plane

Extract the package **outside the repository**, then preview:

```text
python install.py --repo C:/Projects/ProjectA
```

If the file list is correct:

```text
python install.py --repo C:/Projects/ProjectA --apply
```

The installer does not commit or push.

### B. Project already uses Control Plane v0.5

Use the upgrade helper, not the fresh installer:

```text
python upgrade_v05_to_v07.py --repo C:/Projects/ProjectA
```

The preview must show the schema change and the new forge block:

```text
project_schema = 3 -> 4
forge_block_after_upgrade = {"provider": "gitlab", "host": ..., "repo": ...}
```

If it is correct and the repository is clean:

```text
python upgrade_v05_to_v07.py --repo C:/Projects/ProjectA --apply
```

The upgrade helper:
- folds `gitlab_host` and `gitlab_repo` into the `forge` block, unchanged;
- writes `provider: "gitlab"` because v0.5 supported no other forge — it is reading a
  fact off your configuration, not choosing for you;
- preserves your standing merge, remote, cleanup and assurance approvals;
- leaves `merge_mode` alone;
- does not touch an active lane pool;
- stops rather than silently overwriting managed files that were manually modified;
- creates a local backup before replacing files.

**Moving to GitHub is a separate step, done by hand.** After the upgrade, change
`forge.provider` to `github` and `forge.repo` to `owner/name`, and re-point the Git
remote to match: the kit requires the fetch and push URLs to agree with the forge block.
Read "If your project is on GitHub" below before you do.

### C. Project already uses Control Plane v0.4

Same shape, different helper. v0.4 migrates straight to schema 4 in one step:

```text
python upgrade_v04_to_v07.py --repo C:/Projects/ProjectA
python upgrade_v04_to_v07.py --repo C:/Projects/ProjectA --apply
```

The preview must show `project_assurance_policy_approved_after_upgrade = false`. As in
v0.5, assurance is never auto-approved.

## Step 3 — Review `.agentic/project.json`

For a pilot, review at least:

```text
project_name
target_branch
forge.provider      gitlab or github
forge.host          gitlab.example.com, or github.com
forge.repo          group/project on GitLab, owner/name on GitHub
merge_mode
merge_method
ci_policy / commands
team_coordination_ref
```

`forge.host` and `forge.repo` must identify the same repository as your Git remote. The
kit checks this before any remote effect and refuses if they disagree, so a typo here
surfaces as a refusal rather than as an operation against the wrong repository.

The forge block is only required for remote work. `pool init`, taking a lane and
building all run before you have chosen a forge, so a team can evaluate the kit first.

Then review the approval boundaries:

```text
configuration_approved
remote_actions_ready
merge_policy.approved
cleanup.approved
assurance_policy.approved
```

Do not set values to `true` or invent approval references just to make doctor pass.

For the first pilot, the easier-to-understand mode is:

```text
merge_mode = DEVELOPER_REVIEW
```

After the team understands the flow, `AGENT_MERGE` can be tested separately.

## Step 4 — Run doctor

From the repository root:

```text
python tools/agentic/ae.py doctor
```

If doctor does not PASS, do not start coding through the Control Plane. Resolve the stated blocker first.

`doctor` reports `forge_provider` and `forge_configured`, and checks for the CLI that
provider needs — `glab` or `gh`, not both.

## Step 4b — If your project is on GitHub

GitLab publishes a single merge-readiness verdict that folds in approval rules,
protected branches and pipeline state. The Control Plane trusts that verdict, as it
always has. GitHub publishes no equivalent, so instead the kit reads **branch
protection** — where GitHub stores "how many reviews" and "which checks must pass" — and
verifies those declared requirements against your candidate.

That has a consequence worth knowing before your pilot, because it was found by testing
against a real repository rather than by reading documentation:

> On a **private repository under a free plan**, GitHub returns HTTP 403 for both branch
> protection and rulesets: *"Upgrade to GitHub Pro or make this repository public."* The
> forge declares no policy at all, and nothing prevents a direct push to your target
> branch.

In that situation the kit refuses `AGENT_MERGE` and `DEVELOPER_REVIEW` carries the
decision. It does not invent a policy of its own, because that would let two teams run
at different real safety levels without either noticing. The `MR_READY_FOR_HUMAN_REVIEW`
result records `forge_policy`, so the degradation is visible in the record instead of
being reconstructed later.

The four answers you may see under `forge_policy.state`:

```text
FORGE_VERDICT      GitLab computed its own verdict; the kit delegated to it
ENFORCED           requirements were read from branch protection and verified
NOT_CONFIGURED     GitHub offers branch protection, and none is set on the branch
UNAVAILABLE_PLAN   this plan or visibility does not offer branch protection at all
```

The last two are degraded. To reach `ENFORCED`, either make the repository public or
move to a plan that includes branch protection, then configure required reviews and
required status checks on the target branch. Two further points:

- Set at least one required approving review. Protection that requires zero approvals
  declares nothing for the kit to verify, and is refused.
- If `ci_required` is `true`, the branch must declare required status checks. That exact
  combination — `ci_required` true with an empty `required_status_checks` — is reported
  immediately rather than surfacing later as a missing approval.

On GitHub, set `merge_method` to `merge_commit` if you intend to use `AGENT_MERGE`.
GitHub's squash and rebase merges both rewrite history and GitHub does not report which
method produced the result, so the merge could not be tied back to your candidate SHA
afterwards. The kit refuses that combination before merging rather than after.

## Step 5 — Check/initialize the pool

Open Claude Code as the Control Plane:

```text
cd C:/Projects/ProjectA
claude --agent ae-control-plane
```

Inside the session:

```text
/ae-pool status
```

If the pool does not exist and setup has been approved:

```text
/ae-pool init
```

The default pool contains reusable lanes. Having multiple lanes **does not** mean all lanes should be active at once.

# B. Prepare the work

## Step 6 — Make sure the backlog is BUILD READY

The Control Plane is not the place to perform product discovery from scratch.

Use the output of Backlog Preparation, including:
- canonical backlog;
- Story card;
- acceptance criteria (AC);
- dependencies;
- risk/engineering impact;
- required assurance, when applicable;
- build-start/final-acceptance reference.

The final preparation status is:

```text
BUILD READY FOR CONTROL-PLANE SCHEDULING
```

This does not yet authorize coding. The Control Plane still performs a fresh precheck.

# C. Execute one Story

## Step 7 — Start the Story

Example:

```text
/ae-start backlog/stories/STORY-101.md dev-a assignment-01
```

Or use a normal instruction:

```text
I am dev-a. Work on STORY-101 from backlog/stories/STORY-101.md.
Follow Control Plane v0.6 and the project's merge mode.
Do not deploy or release.
```

## Step 8 — What will the Control Plane do?

You do not need to invoke Builder/Reviewer one by one.

The Control Plane will:
1. read the Story + canonical authority;
2. create/validate the local normalized contract;
3. calculate effective assurance;
4. check target/dependency/hotspot/pool/config state;
5. return `AUTHORIZED TO START` or `BLOCKED`;
6. lease a Builder lane;
7. build/test/save the candidate;
8. perform independent review;
9. run a bounded fix when needed;
10. execute only the assurance that is required;
11. prepare the MR;
12. continue according to merge mode;
13. write traceability after integration;
14. run capability acceptance only when it is required.

## Step 9 — Check status at any time

```text
/ae-status STORY-101
```

A status-only operation must not start a new build, merge, or cleanup.

# D. When human assurance is required

The Control Plane/helper will generate an exact request, for example:

```text
AE-ASSURE security_review STORY-101 <binding_digest>
```

The authorized human posts the exact note on the configured MR/provider. The agent then verifies the note from the server.

Do not replace human assurance with a local flag or with a simple "approved" message in chat.

# E. When the MR is ready

## If `DEVELOPER_REVIEW`

The Control Plane stops at:

```text
MR_READY_FOR_HUMAN_REVIEW
```

You review the MR and merge it through your organization's normal process. Then use resume/status so the Control Plane can verify integration and finish records/cleanup.

## If `AGENT_MERGE`

The agent may merge only when:
- the exact candidate is valid;
- technical review is valid;
- CI/checks are valid;
- every mandatory PRE_MERGE assurance gate is closed;
- the **forge itself** enforces a merge policy, and that policy is satisfied — a
  delegated verdict on GitLab, verified branch protection on GitHub. Where the forge
  declares no policy, agent merge is refused; see Step 4b;
- the configured `merge_method` is one whose result can still be proven afterwards;
- the integration lock is valid.

# F. After merge

`INTEGRATED` means the merge has been proven on the target.

The Control Plane then:
- writes Story traceability;
- performs safe cleanup;
- creates capability state **only when** there is a post-integration obligation.

A feature may be called:

```text
BUILD COMPLETE
```

only after product capability acceptance and mandatory engineering capability assurance are complete.

# G. Stop / resume

Before closing an unfinished session:

```text
/ae-checkpoint STORY-101
```

In a new session:

```text
/ae-resume <checkpoint-path>
```

Resume always checks actual Git/MR/lane state. It must never assume a previous operation definitely completed.

# H. Recommended order for the first pilot

Do not test every v0.5 feature at once.

```text
1. doctor
2. pool status
3. start the Control Plane
4. /ae-status with no mutation
5. one small routine Story
6. DEVELOPER_REVIEW first
7. after the flow is understood, test material assurance
8. test AGENT_MERGE last
```

See `04_FIRST_PILOT.md`.
