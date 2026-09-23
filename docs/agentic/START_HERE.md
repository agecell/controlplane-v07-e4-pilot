# START HERE — Control Plane v0.7

> **Want to understand why we moved from v0.4 to v0.5?**  
> Read `00A_BACKGROUND_UPGRADE_v0.4_to_v0.5.docx`. It explains the four primary drivers of v0.5: **developer understanding, scalability, security, and auditability**.
>
> **v0.6 is a narrower change:** the kit no longer speaks only GitLab. It works on
> GitLab or GitHub, and project configuration says which in one `forge` block. See
> `CHANGELOG.md`.

## 1. What you need to understand first

The system has only three conceptual layers:

```text
1. BACKLOG PREPARATION
   Defines: what are we going to build?

2. CONTROL PLANE
   Defines: how will the Story be executed safely and consistently?

3. ENGINEERING ASSURANCE
   Defines: what additional evidence is actually required before/after merge?
```

You **do not operate these three layers manually**. Backlog Preparation produces a clear Story. Then you open Claude Code as `ae-control-plane` and give it the Story. The Control Plane manages subagents, lanes, review, evidence, merge path, and records.

## 2. What changed in v0.6?

One thing, deliberately: **which forge you use is now configuration, not an assumption
in the code.** `.agentic/project.json` carries a `forge` block naming the provider, host
and repository, and everything above the adapter works with a normalized pull request
instead of GitLab's own REST fields.

Two consequences you will meet in practice:

- **You only need your own forge's CLI** — `glab` for GitLab, `gh` for GitHub.
- **On GitHub, how much the forge itself enforces is read rather than assumed.** Where
  GitHub declares no merge policy — a private repository on a free plan cannot —
  `AGENT_MERGE` is refused and a human carries the decision. The kit does not substitute
  a policy of its own, and it records what it found. Step 4b of
  `02_STEP_BY_STEP_GUIDE.md` covers this before your pilot.

The forge block is required only for remote work, so a team can install the kit, take a
lane and build before choosing a forge at all.

## 3. What changed from v0.4 to v0.5?

The core v0.4 mechanics remain:

```text
Story → reusable lane → Builder → candidate SHA → Reviewer → MR → merge → cleanup
```

v0.5 adds stronger controls and continuity:

```text
normalized Story contract
+ assurance only when required
+ genuinely server-backed human attestation
+ revalidation only for affected gates
+ requirement → merge traceability
+ capability acceptance after integration when required
```

So v0.5 is not a brand-new workflow. It is v0.4 with an additional **contract + assurance + traceability layer**, and v0.6 keeps that workflow unchanged.

## 4. Common terms

| Term | Simple meaning | Do you fill it manually? |
|---|---|---|
| Story | An agreed unit of work | Yes, it comes from the backlog |
| Lane | Reusable agent workspace | No, the Control Plane manages it |
| Role | Builder / Reviewer / QA / Integrator | No, the Control Plane chooses it |
| Normalized contract | Machine-readable summary of the approved Story/backlog | Not for daily work; the Control Plane creates the local derivative |
| Assurance gate | Additional evidence that is explicitly required | Sometimes a human action is required |
| Traceability | Requirement → Story → candidate → MR → merge index | No, the system writes it |
| Capability | Multiple Stories combined into one product capability | Only when the feature needs combined final acceptance |

## 5. Fast path vs material path

### Routine Story

```text
/ae-start
→ precheck
→ build
→ test
→ independent review
→ MR
→ merge according to mode
→ traceability
```

No meeting or specialist gate is added automatically.

### Material Story

For example: auth/session changes, architecture boundaries, recovery flows, or security-sensitive areas.

```text
/ae-start
→ routine flow
→ only the assurance gates that are required
→ human/tool evidence according to each gate
→ merge only after PRE_MERGE gates are valid
→ capability acceptance when there is a POST_INTEGRATION obligation
```

## 6. What is your job as a developer/PO?

You mainly need to:

1. make sure the requirement/backlog is correct;
2. give the correct Story to the Control Plane;
3. read the summary before coding starts when there is a blocker/ambiguity;
4. make human decisions when product/risk/authority genuinely requires them;
5. provide human attestation only when a gate requires an authorized person;
6. review the MR when the project uses `DEVELOPER_REVIEW`;
7. never assume `MERGED = RELEASED`.

Choosing lanes, invoking Builder/Reviewer, storing the candidate SHA, revalidation, and writing traceability are **not your everyday administrative tasks**.

## 7. Implementation Phase folders can be ignored

`Implementation_Phase1` … `Implementation_Phase12` document how v0.5 was built and tested. They are not user manuals.

For pilot/daily use, use this **Release Candidate package**.

Continue with `QUICKSTART.md`.
