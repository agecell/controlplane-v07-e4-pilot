#!/usr/bin/env python3
"""Forge provider adapters for Control Plane v0.7.

v0.5 spoke GitLab directly: `glab api`, `projects/{id}` paths, and GitLab REST field
names reached into runtime.py, merge.py and guard.py. That made the kit unusable for a
team on GitHub, and "add GitHub" alongside it would only have doubled the coupling.

This module is the seam. Everything above it works with a normalized pull request and
named operations; everything provider-specific lives in one adapter class below.

## The rule this module exists to protect

**An adapter reads the policy the forge declares. It never substitutes its own.**

On GitLab, `detailed_merge_status == 'mergeable'` is the forge's own verdict, computed
from approval rules, protected branches and pipeline state. Control Plane trusts it.

GitHub publishes no single equivalent. The temptation is to assemble a verdict from
whatever signals are available, but that would move a safety decision out of the forge
and into this file, and would let two teams run at different effective safety levels
without either noticing. So the GitHub adapter reads **branch protection** — where
GitHub stores the repository's own "how many reviews" and "which checks must pass" —
and verifies those declared requirements are met.

## Degraded mode, and why it exists

Measured against a real repository, that rule turned out to be unachievable for a large
class of users. On a **private repository under a free plan**, both branch protection
and rulesets return HTTP 403 with "Upgrade to GitHub Pro or make this repository
public". The forge declares no policy at all, and nothing prevents a direct push to the
target branch.

Refusing outright would make Control Plane unusable for those teams. Reconstructing a
policy here would be the silent divergence the rule exists to prevent. So the adapter
detects the situation instead, and `policy_state()` reports one of four answers. Where
no policy is enforced, **agent merge is refused and human review carries the decision** —
the degradation is explicit, detectable and recorded, never silent.

The two providers are therefore deliberately asymmetric: GitHub's policy is *assessed*,
GitLab's is *delegated*. Extending the same assessment to GitLab is reasonable future
work, but it would change behaviour for every existing GitLab project on a claim this
build could not test, since no `glab` or GitLab instance was available.

## Approval evidence

Human merge approval is a comment whose body carries `APPROVE <candidate_sha>`, written
by an account on the configured allow-list. That convention is already provider-neutral,
so both adapters implement the same `comment()` read; nothing about it is GitLab-shaped.
Approval is bound to the candidate SHA by the caller, not to any provider's own
stale-review setting, which differs between the two.

## Each provider spells its own references, in exactly one place

`comment()` returns a `ref`, and `runtime.verify_forge_comment` derives both the
recorded `attestor_ref` and `evidence_ref` from it rather than rebuilding either. GitLab
must keep writing `gitlab:mr:{iid}:note:{id}:user:{name}` byte for byte as v0.5 did, so
records written before an upgrade still parse and compare; GitHub writes
`github:pr:{n}:comment:{id}:user:{login}`. Building a "neutral" spelling above the seam
instead silently re-lettered GitLab's and made every pre-upgrade record fail the prefix
check in assurance.py. Add a provider's vocabulary here, never above.

## The proof has to survive the merge

`runtime.integration_proof` ties the merged result back to the candidate SHA: by
ancestry where the candidate commit itself reaches the target, or by an exact blob/mode
delta comparison where a squash replaced it. A forge that rewrites history on merge
without reporting the result as a squash cannot be proved either way.

GitHub does exactly that for squash and rebase, and publishes one `merge_commit_sha`
without saying which method produced it — there is no field equivalent to GitLab's
`squash_commit_sha` for the delta comparison to key off. So each adapter declares
`provable_merge_methods`, and merge.py asserts the configured method against it *before*
merging. This is not a policy choice and not a claim about what the forge permits, which
`project()['merge_methods']` answers; it is the narrower question of what can still be
proven afterwards, refused while it is still reversible rather than discovered after an
irreversible step.
"""
from __future__ import annotations

import json
from urllib.parse import quote

PROVIDERS = ("gitlab", "github")

# How much merge policy the forge itself is enforcing. Control Plane reads this rather
# than assuming, because the answer is not the same on every plan.
#
#   FORGE_VERDICT      the forge publishes a single mergeable verdict computed from its
#                      own rules; Control Plane delegates to it, as v0.5 did
#   ENFORCED           the forge declares explicit requirements that were read and
#                      verified against this candidate
#   NOT_CONFIGURED     the forge offers a policy mechanism, and none is set up
#   UNAVAILABLE_PLAN   the forge offers no policy mechanism on this plan or visibility
#
# The last two are degraded: nothing on the forge prevents someone merging around
# Control Plane, so agent merge is refused and human review carries the decision.
POLICY_FORGE_VERDICT = "FORGE_VERDICT"
POLICY_ENFORCED = "ENFORCED"
POLICY_NOT_CONFIGURED = "NOT_CONFIGURED"
POLICY_UNAVAILABLE_PLAN = "UNAVAILABLE_PLAN"
POLICY_DEGRADED = (POLICY_NOT_CONFIGURED, POLICY_UNAVAILABLE_PLAN)

# Merge methods, as Control Plane names them, mapped to what each forge calls the same
# thing. Control Plane asserts the project actually permits the configured method.
MERGE_METHODS = ("merge_commit", "fast_forward", "squash")


class ForgeError(RuntimeError):
    pass


def _need(condition, message):
    if not condition:
        raise ForgeError(message)


def _str(value):
    return isinstance(value, str) and value.strip() == value and bool(value)


class Forge:
    """Base adapter. Subclasses implement one forge's REST shape."""

    provider = ""
    cli = ""

    # Methods whose result Control Plane can still prove from the forge's own record
    # after the merge has happened. This is not a policy choice and not a statement
    # about what the forge permits -- `project()['merge_methods']` answers that. It is
    # the narrower question of which results `runtime.integration_proof` can tie back
    # to the candidate SHA, and it is asserted before an agent merge so an unprovable
    # combination is refused while it is still reversible.
    provable_merge_methods = MERGE_METHODS

    def __init__(self, runtime):
        # runtime is injected rather than imported to keep this module free of a cycle
        # and to let tests substitute a transport.
        self._r = runtime

    # -- transport ---------------------------------------------------------------

    def _argv(self, cfg, path, method, fields):
        argv = [self.cli, "api", "--hostname", cfg["forge"]["host"], "--method", method, path]
        for key, value in (fields or {}).items():
            # Both CLIs spell these the same way: -F is a *typed* field, where the value
            # is coerced to a boolean or a number and a leading '@' reads a file from
            # disk; -f is a raw string. Booleans need the typed form. Everything else
            # must use the raw form, because a title or description is free text and a
            # user must not be able to change a request's type -- or read a local file
            # into it -- by starting a sentence with '@' or typing only digits.
            if isinstance(value, bool):
                argv += ["-F", f'{key}={"true" if value else "false"}']
            else:
                argv += ["-f", f"{key}={value}"]
        return argv

    def api(self, repo, cfg, path, method="GET", fields=None):
        try:
            return json.loads(self._r.run(self._argv(cfg, path, method, fields), cwd=repo)[1])
        except ValueError as exc:
            raise ForgeError(f"Unreadable {self.provider} result; verify before retry.") from exc

    def api_optional(self, repo, cfg, path):
        """GET a path that may legitimately fail, returning (ok, payload, http_status).

        Both CLIs exit non-zero on an HTTP error and print the error body as JSON on
        stdout. Distinguishing 403 from 404 matters: one means the plan does not offer
        the feature, the other means it is offered but not configured. Those need
        different remediation, so they must not collapse into one failure.
        """
        code, out = self._r.run(self._argv(cfg, path, "GET", None), cwd=repo, allowed=(0, 1))
        try:
            payload = json.loads(out)
        except ValueError:
            if code == 0:
                raise ForgeError(f"Unreadable {self.provider} result; verify before retry.")
            return False, None, None
        if code == 0:
            return True, payload, 200
        status = payload.get("status") if isinstance(payload, dict) else None
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
        return False, payload, status

    # -- reads that every caller needs -------------------------------------------

    def project(self, repo, cfg):
        """Return {'id', 'full_name', 'merge_methods', 'deletes_source_branch'}.

        `deletes_source_branch` is the project's own default for removing the source
        branch when a request is merged. Controlled cleanup deletes it only after the
        integration proof and the traceability record exist, so a forge that removes it
        at merge time destroys the evidence first, and merge preflight refuses. The flag
        is surfaced here so `doctor` can say so at setup instead of leaving every new
        project to discover it at its first merge -- which is what the v0.6 pilot did.
        """
        raise NotImplementedError

    def pull_request(self, repo, cfg, task):
        """Return the normalized pull request for task['mr_iid'].

        Normalized shape, identical across providers:

            number                  int
            state                   'open' | 'closed' | 'merged'
            draft                   bool
            source_branch           str
            target_branch           str
            head_sha                str
            same_project            bool   False for a fork or cross-project request
            has_conflicts           bool   True when the forge reports or cannot rule out a conflict
            auto_merge_queued       bool
            deletes_source_on_merge bool
            ci                      {'status': 'success'|other, 'sha': str} or None

        Plus the merge result, which `runtime.integration_proof` reads to tie what landed
        on the target back to the candidate SHA. All four are None until the merge:

            merged_at               str or None
            base_sha                str   the target-side base of the diff
            merge_commit_sha        str or None
            squash_commit_sha       str or None, and only where the forge identifies a
                                    squash result as such. See `provable_merge_methods`:
                                    a provider that rewrites history without reporting it
                                    must report None here, not guess.
        """
        raise NotImplementedError

    def comment(self, repo, cfg, task, comment_id):
        """Return one comment on the pull request, normalized.

            id          int
            body        str
            author      str      account name
            system      bool     True when the forge or a bot produced it, not a person
            created_at  str      or None
            ref         str      provider-qualified provenance string

        `system` matters: a machine-generated comment must never satisfy a human
        attestation. GitLab flags this directly; GitHub has no equivalent field, so the
        adapter derives it from the author account type.
        """
        raise NotImplementedError

    def assert_policy_mergeable(self, repo, cfg, task, project, pull):
        """Raise unless the forge's own declared policy is satisfied for this head SHA.

        This is the gate v0.5 expressed as `detailed_merge_status == 'mergeable'`.
        """
        raise NotImplementedError

    def policy_state(self, repo, cfg):
        """Report how much merge policy the forge is enforcing. See POLICY_* above."""
        raise NotImplementedError

    def open_pull_requests(self, repo, cfg, page):
        """One page of open pull requests as [{'number', 'source_branch', 'target_branch'}].

        Cleanup pages through these to prove no open request still depends on a branch
        before deleting it, so a short read must never be mistaken for an empty result.
        Creation pages through the same listing to find a request that is already open
        for a branch, which is why the number is carried alongside the branch names.
        """
        raise NotImplementedError

    def branch(self, repo, cfg, name):
        """Return {'name', 'sha', 'protected', 'is_default'} for one remote branch."""
        raise NotImplementedError

    def merge(self, repo, cfg, task, method):
        """Perform the merge. `method` is one of MERGE_METHODS."""
        raise NotImplementedError

    def user_exists(self, repo, cfg, username):
        """Report whether an account by this exact name exists on the forge.

        Existence, and nothing more. This is **not** an authorization check: who may
        attest to a gate is decided by the project's own allow-lists, and that decision
        must never move into a lookup against the forge. What this answers is the
        narrower question of whether a configured identity could ever act at all, so a
        Story that no one can satisfy is refused before it costs a build cycle rather
        than at the attestation step at the end of one.

        Returns True or False when the forge answered, and raises ForgeError when it
        could not be asked. The caller reports the difference; it must never collapse
        "unknown" into either answer.
        """
        raise NotImplementedError

    # -- creation ----------------------------------------------------------------

    # A pull request may only be opened from a branch this workflow produces. The same
    # two prefixes the push guard enforces, for the same reason: a request opened from
    # anywhere else did not come through Control Plane, and integration would later have
    # no candidate to prove anything against.
    SOURCE_PREFIXES = ("work/", "ae/records/")

    def create_pull_request(self, repo, cfg, task, title, description):
        """Open the pull request for this task, or return the one already open for it.

        v0.6 had no creation operation at all. The step was delegated to `glab mr
        create` / `gh pr create` under a guard allow-list, and on a self-managed GitLab
        whose host is not the CLI's configured default that simply does not work -- the
        v0.6 pilot created both of its merge requests by hand in the web UI, outside
        every check this kit performs. Creation belongs behind the same seam as every
        other forge operation.

        The rules below are enforced here, once, rather than in each adapter:

        * the source branch is one this workflow produces;
        * the target is the configured target branch and nothing else;
        * an already-open request for that source branch is returned, never duplicated;
        * nothing is set that would let the forge act on its own afterwards -- no
          auto-merge, no squash, no delete-source-on-merge. Controlled cleanup deletes
          the branch only after the integration proof and the traceability record exist,
          and a forge that removes it on merge destroys that evidence first.

        Returns the same normalized shape `pull_request()` returns, re-read from the
        forge rather than assembled from the creation response, plus `created`.
        """
        source = task.get("branch")
        target = cfg.get("target_branch")
        _need(_str(source), "The task must record the source branch.")
        _need(
            source.startswith(self.SOURCE_PREFIXES),
            f"A pull request may only be opened from {' or '.join(x + '*' for x in self.SOURCE_PREFIXES)}; "
            f"'{source}' is outside the branches this workflow produces.",
        )
        _need(_str(target), "Configure target_branch before opening a pull request.")
        _need(source != target, "Source and target branch must differ.")
        _need(_str(title), "A pull request title is required; it is not derived.")
        _need(isinstance(description, str), "A pull request description is required; pass an empty string for none.")

        existing = self.find_open_pull_request(repo, cfg, source)
        if existing is not None:
            pull = self.pull_request(repo, cfg, dict(task, mr_iid=existing))
            _need(
                pull.get("target_branch") == target,
                f"A pull request is already open from '{source}' to '{pull.get('target_branch')}', "
                f"not to the configured target '{target}'. Resolve it before continuing; "
                f"a second request for one branch is never opened.",
            )
            pull["created"] = False
            return pull

        number = self._open_pull_request(repo, cfg, source, target, title, description)
        _need(
            type(number) is int and number > 0,
            f"{self.provider} did not report a pull request number; verify on the forge before retrying.",
        )
        pull = self.pull_request(repo, cfg, dict(task, mr_iid=number))
        # Re-read rather than trust the creation response. What was asked for and what
        # the forge actually recorded are different facts, and only the second one is
        # what every later step will be checked against.
        _need(
            pull.get("source_branch") == source and pull.get("target_branch") == target,
            "The created pull request does not carry the requested source/target; verify on the forge.",
        )
        pull["created"] = True
        return pull

    def find_open_pull_request(self, repo, cfg, source_branch):
        """Return the number of the open pull request for this source branch, or None.

        Pages to the end of the listing. A short read is the only evidence that the
        listing is complete; a truncated one would let a duplicate be created against a
        branch that already has a request open, so it raises instead of returning None.
        """
        for page in range(1, 101):
            rows = self.open_pull_requests(repo, cfg, page)
            for row in rows:
                if row.get("source_branch") == source_branch:
                    number = row.get("number")
                    _need(
                        type(number) is int and number > 0,
                        "An open pull request was listed without a usable number.",
                    )
                    return number
            if len(rows) < 100:
                return None
        raise ForgeError(
            "The open pull request listing did not end; refusing to create a request that "
            "may duplicate one already open."
        )

    def _open_pull_request(self, repo, cfg, source, target, title, description):
        """Provider REST for creation. Returns the new pull request number."""
        raise NotImplementedError


class GitLabForge(Forge):
    provider = "gitlab"
    cli = "glab"
    # All three, as v0.5 accepted. A GitLab fast-forward merge requires the source to
    # already sit on the target, so the candidate commit itself lands there; a squash
    # is reported in squash_commit_sha and proved by exact delta.
    provable_merge_methods = ("merge_commit", "fast_forward", "squash")

    def _prefix(self, cfg):
        return "projects/" + quote(cfg["forge"]["repo"], safe="")

    def project(self, repo, cfg):
        meta = self.api(repo, cfg, self._prefix(cfg))
        _need(
            meta.get("path_with_namespace") == cfg["forge"]["repo"] and type(meta.get("id")) is int,
            "Project identity mismatch.",
        )
        # GitLab exposes exactly one configured merge method per project.
        native = meta.get("merge_method")
        allowed = set()
        if native == "merge":
            allowed.add("merge_commit")
        elif native == "ff":
            allowed.add("fast_forward")
        elif native == "rebase_merge":
            allowed.add("fast_forward")
        return {"id": meta["id"], "full_name": meta.get("path_with_namespace"), "merge_methods": allowed,
                # GitLab seeds force_remove_source_branch on every new merge request from
                # this project setting, which is why unticking the box on one request is
                # not enough: Settings -> Merge requests -> "Enable 'Delete source
                # branch' option by default".
                "deletes_source_branch": meta.get("remove_source_branch_after_merge") is True,
                "_raw": meta}

    def pull_request(self, repo, cfg, task):
        prefix = self._prefix(cfg)
        project = self.project(repo, cfg)
        mr = self.api(repo, cfg, f'{prefix}/merge_requests/{task["mr_iid"]}')
        _need(mr.get("iid") == task["mr_iid"], "Wrong merge request.")
        same_project = mr.get("source_project_id") == mr.get("target_project_id") == project["id"]
        pipeline = mr.get("head_pipeline") or {}
        return {
            "number": mr.get("iid"),
            "state": "merged" if mr.get("state") == "merged" else ("open" if mr.get("state") == "opened" else "closed"),
            "draft": bool(mr.get("draft")) or bool(mr.get("work_in_progress")),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "head_sha": (mr.get("diff_refs") or {}).get("head_sha"),
            "same_project": same_project,
            # GitLab reports has_conflicts explicitly; anything other than a definite
            # False is treated as unknown, which is a conflict for our purposes.
            "has_conflicts": mr.get("has_conflicts") is not False,
            "auto_merge_queued": bool(mr.get("merge_when_pipeline_succeeds")) or bool(mr.get("auto_merge_enabled")),
            "deletes_source_on_merge": mr.get("force_remove_source_branch") is True,
            "ci": {"status": pipeline.get("status"), "sha": pipeline.get("sha")} if pipeline else None,
            # The merge result, needed to prove integration afterwards. GitLab reports a
            # squash result in its own field, so the two cases stay distinguishable.
            "merged_at": mr.get("merged_at"),
            "base_sha": (mr.get("diff_refs") or {}).get("base_sha"),
            "merge_commit_sha": mr.get("merge_commit_sha"),
            "squash_commit_sha": mr.get("squash_commit_sha"),
            "_raw": mr,
            "_project": project,
        }

    def comment(self, repo, cfg, task, comment_id):
        note = self.api(
            repo, cfg, f'{self._prefix(cfg)}/merge_requests/{task["mr_iid"]}/notes/{comment_id}'
        )
        _need(isinstance(note, dict), "GitLab note response must be an object.")
        author = (note.get("author") or {}).get("username")
        return {
            "id": note.get("id"),
            "body": note.get("body"),
            "author": author,
            "system": note.get("system") is not False,
            "created_at": note.get("created_at"),
            "ref": f'gitlab:mr:{task["mr_iid"]}:note:{comment_id}:user:{author}',
            "_raw": note,
        }

    def open_pull_requests(self, repo, cfg, page):
        rows = self.api(
            repo, cfg, f"{self._prefix(cfg)}/merge_requests?state=opened&per_page=100&page={page}"
        )
        _need(isinstance(rows, list), "Open MR listing unavailable.")
        return [
            {
                "number": row.get("iid"),
                "source_branch": row.get("source_branch"),
                "target_branch": row.get("target_branch"),
            }
            for row in rows
            if isinstance(row, dict)
        ]

    def _open_pull_request(self, repo, cfg, source, target, title, description):
        mr = self.api(
            repo,
            cfg,
            f"{self._prefix(cfg)}/merge_requests",
            method="POST",
            fields={
                "source_branch": source,
                "target_branch": target,
                "title": title,
                "description": description,
                # Set explicitly, never left to the project default. A project with
                # "delete source branch when merged" ticked would otherwise remove the
                # branch at merge time, before the integration proof and the
                # traceability record are written against it.
                "remove_source_branch": False,
                "squash": False,
            },
        )
        _need(isinstance(mr, dict), "GitLab merge request creation response must be an object.")
        return mr.get("iid")

    def user_exists(self, repo, cfg, username):
        # GitLab's username lookup is a filtered list, and an unknown name is an empty
        # list rather than an error. Anything that is not a list means the question was
        # not answered, which is reported as such rather than read as "no".
        rows = self.api(repo, cfg, f'users?username={quote(username, safe="")}')
        _need(isinstance(rows, list), "GitLab user lookup did not return a list.")
        return any(
            isinstance(row, dict) and row.get("username") == username for row in rows
        )

    def branch(self, repo, cfg, name):
        data = self.api(
            repo, cfg, f'{self._prefix(cfg)}/repository/branches/{quote(name, safe="")}'
        )
        return {
            "name": data.get("name"),
            "sha": (data.get("commit") or {}).get("id"),
            "protected": data.get("protected"),
            "is_default": data.get("default"),
        }

    def merge(self, repo, cfg, task, method):
        self.api(
            repo,
            cfg,
            f'{self._prefix(cfg)}/merge_requests/{task["mr_iid"]}/merge',
            method="PUT",
            fields={
                "sha": task["candidate_sha"],
                "auto_merge": False,
                "should_remove_source_branch": False,
                "squash": method == "squash",
            },
        )

    def policy_state(self, repo, cfg):
        """GitLab publishes one verdict, so Control Plane delegates rather than assesses.

        Deliberately asymmetric with the GitHub adapter. `detailed_merge_status` already
        folds in approval rules, protected-branch settings and pipeline state, and v0.5
        trusted it; re-deriving those inputs here would change behaviour for every
        existing GitLab project on a claim this build cannot test — no `glab` and no
        GitLab instance were available while it was written.

        Reading GitLab's protected-branch settings to detect a degraded project the way
        the GitHub adapter does is a reasonable future addition. It needs a live GitLab
        to validate first, so it is not claimed here.
        """
        return {
            "state": POLICY_FORGE_VERDICT,
            "source": "detailed_merge_status",
            "branch": cfg["target_branch"],
            "detail": "GitLab computes a single mergeable verdict from the project's own rules.",
        }

    def assert_policy_mergeable(self, repo, cfg, task, project, pull):
        # GitLab computes this from the project's own approval rules, protected-branch
        # settings and pipeline state. Trust its verdict; do not reconstruct it.
        _need(
            (pull.get("_raw") or {}).get("detailed_merge_status") == "mergeable",
            "GitLab has not confirmed mergeable; do not bypass required approvals/checks.",
        )


class GitHubForge(Forge):
    provider = "github"
    cli = "gh"
    # Only merge_commit. GitHub's squash and rebase merges both rewrite history, and
    # GitHub reports the result in merge_commit_sha without saying which method
    # produced it -- there is no field equivalent to GitLab's squash_commit_sha, so the
    # exact-delta comparison has nothing to key off. The merge would succeed and then
    # be unprovable, so an agent merge is refused up front instead. A human may still
    # squash-merge under DEVELOPER_REVIEW; integration_proof then refuses to certify
    # that particular result, which is reported rather than assumed.
    provable_merge_methods = ("merge_commit",)

    def _prefix(self, cfg):
        return "repos/" + cfg["forge"]["repo"]

    def project(self, repo, cfg):
        meta = self.api(repo, cfg, self._prefix(cfg))
        _need(
            meta.get("full_name") == cfg["forge"]["repo"] and type(meta.get("id")) is int,
            "Repository identity mismatch.",
        )
        allowed = set()
        if meta.get("allow_merge_commit"):
            allowed.add("merge_commit")
        if meta.get("allow_rebase_merge"):
            allowed.add("fast_forward")
        if meta.get("allow_squash_merge"):
            allowed.add("squash")
        return {"id": meta["id"], "full_name": meta.get("full_name"), "merge_methods": allowed,
                # GitHub spells the same setting delete_branch_on_merge, and applies it
                # per repository rather than per request.
                "deletes_source_branch": meta.get("delete_branch_on_merge") is True,
                "_raw": meta}

    def pull_request(self, repo, cfg, task):
        project = self.project(repo, cfg)
        pr = self.api(repo, cfg, f'{self._prefix(cfg)}/pulls/{task["mr_iid"]}')
        _need(pr.get("number") == task["mr_iid"], "Wrong pull request.")
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        head_repo_id = (head.get("repo") or {}).get("id")
        base_repo_id = (base.get("repo") or {}).get("id")
        if pr.get("merged"):
            state = "merged"
        elif pr.get("state") == "open":
            state = "open"
        else:
            state = "closed"
        return {
            "number": pr.get("number"),
            "state": state,
            "draft": bool(pr.get("draft")),
            "source_branch": head.get("ref"),
            "target_branch": base.get("ref"),
            "head_sha": head.get("sha"),
            "same_project": head_repo_id == base_repo_id == project["id"],
            # GitHub computes `mergeable` asynchronously and returns null while it is
            # working. Anything other than a definite True is treated as a conflict,
            # which fails closed rather than merging on an unknown state.
            "has_conflicts": pr.get("mergeable") is not True,
            "auto_merge_queued": pr.get("auto_merge") is not None,
            "deletes_source_on_merge": bool((project.get("_raw") or {}).get("delete_branch_on_merge")),
            "ci": self._ci_status(repo, cfg, head.get("sha")),
            "merged_at": pr.get("merged_at"),
            "base_sha": base.get("sha"),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            # GitHub reports one merge_commit_sha whatever method was used and does not
            # say which it was, so there is no field that identifies a squash result the
            # way GitLab's does. Reporting None keeps the proof on the ancestry path,
            # which refuses a rewritten history rather than guessing at it.
            "squash_commit_sha": None,
            "_raw": pr,
            "_project": project,
        }

    def _ci_status(self, repo, cfg, sha):
        """Collapse check-runs and commit statuses for one SHA into a single verdict.

        GitHub splits CI across two APIs. A commit can have Checks (GitHub Actions and
        apps) and legacy commit Statuses, and a repository may use either or both.
        """
        if not _str(sha):
            return None
        conclusions = []
        runs = self.api(repo, cfg, f"{self._prefix(cfg)}/commits/{sha}/check-runs")
        for run in (runs or {}).get("check_runs", []):
            if run.get("status") != "completed":
                conclusions.append("pending")
            else:
                conclusions.append(run.get("conclusion"))
        combined = self.api(repo, cfg, f"{self._prefix(cfg)}/commits/{sha}/status")
        for status in (combined or {}).get("statuses", []):
            conclusions.append(status.get("state"))
        if not conclusions:
            return {"status": "none", "sha": sha}
        ok = {"success", "neutral", "skipped"}
        status = "success" if all(c in ok for c in conclusions) else "failed"
        return {"status": status, "sha": sha}

    def comment(self, repo, cfg, task, comment_id):
        # Pull request comments live under the issues namespace on GitHub.
        note = self.api(repo, cfg, f"{self._prefix(cfg)}/issues/comments/{comment_id}")
        _need(isinstance(note, dict), "GitHub comment response must be an object.")
        # The comment must belong to this pull request; GitHub returns it by repository
        # id alone, so the link is checked rather than assumed.
        issue_url = note.get("issue_url") or ""
        _need(
            issue_url.rstrip("/").endswith(f'/issues/{task["mr_iid"]}'),
            "Comment does not belong to the recorded pull request.",
        )
        user = note.get("user") or {}
        author = user.get("login")
        return {
            "id": note.get("id"),
            "body": note.get("body"),
            "author": author,
            # GitHub has no system flag. A comment written by a bot account is the
            # closest equivalent and must not count as human attestation.
            "system": user.get("type") == "Bot",
            "created_at": note.get("created_at"),
            "ref": f'github:pr:{task["mr_iid"]}:comment:{comment_id}:user:{author}',
            "_raw": note,
        }

    def open_pull_requests(self, repo, cfg, page):
        rows = self.api(repo, cfg, f"{self._prefix(cfg)}/pulls?state=open&per_page=100&page={page}")
        _need(isinstance(rows, list), "Open pull request listing unavailable.")
        return [
            {
                "number": row.get("number"),
                "source_branch": (row.get("head") or {}).get("ref"),
                "target_branch": (row.get("base") or {}).get("ref"),
            }
            for row in rows
            if isinstance(row, dict)
        ]

    def _open_pull_request(self, repo, cfg, source, target, title, description):
        pr = self.api(
            repo,
            cfg,
            f"{self._prefix(cfg)}/pulls",
            method="POST",
            fields={
                "head": source,
                "base": target,
                "title": title,
                "body": description,
                "draft": False,
            },
        )
        _need(isinstance(pr, dict), "GitHub pull request creation response must be an object.")
        # GitHub has no per-request equivalent of GitLab's remove_source_branch; branch
        # deletion on merge is a repository setting. It is already surfaced as
        # `deletes_source_on_merge` and refused by merge preflight, so there is nothing
        # to set here -- and nothing that could be set here would change it.
        return pr.get("number")

    def user_exists(self, repo, cfg, username):
        ok, payload, status = self.api_optional(repo, cfg, f'users/{quote(username, safe="")}')
        if ok and isinstance(payload, dict) and payload.get("login"):
            # Exact, not case-insensitive. GitHub resolves `users/OCTOCAT` to the account
            # `octocat`, but every later comparison -- the attestation author against the
            # allow-list -- is exact against GitHub's canonical spelling. A configured
            # identity in the wrong case genuinely cannot attest, so reporting it as
            # existing would defeat the whole point of asking.
            return payload["login"] == username
        if status == 404:
            return False
        # 403, a rate limit, or an unreadable body means the question was not answered.
        # Reporting that as "does not exist" would block a Story on a network condition;
        # reporting it as "exists" would hide a real misconfiguration. Neither is honest.
        raise ForgeError(
            f"GitHub could not be asked whether '{username}' exists"
            + (f" (HTTP {status})" if status else "")
            + "; the identity is unverified rather than absent."
        )

    def branch(self, repo, cfg, name):
        data = self.api(repo, cfg, f"{self._prefix(cfg)}/branches/{quote(name, safe='')}")
        default_branch = (self.project(repo, cfg).get("_raw") or {}).get("default_branch")
        return {
            "name": data.get("name"),
            "sha": (data.get("commit") or {}).get("sha"),
            # GitHub reports `protected` on the branch itself, which stays usable even
            # where the protection settings endpoint is gated by plan.
            "protected": data.get("protected"),
            # GitHub has no per-branch default flag; it is a repository property.
            "is_default": data.get("name") == default_branch,
        }

    def merge(self, repo, cfg, task, method):
        native = {"merge_commit": "merge", "squash": "squash", "fast_forward": "rebase"}[method]
        self.api(
            repo,
            cfg,
            f'{self._prefix(cfg)}/pulls/{task["mr_iid"]}/merge',
            method="PUT",
            fields={"sha": task["candidate_sha"], "merge_method": native},
        )

    def policy_state(self, repo, cfg):
        """Report how much merge policy GitHub itself is enforcing on the target branch.

        Measured against a real repository rather than assumed. On a private repository
        under a free plan, both branch protection and rulesets return HTTP 403
        ("Upgrade to GitHub Pro or make this repository public"), so the forge declares
        no policy at all and nothing prevents a direct push to the target branch. That
        is a materially weaker guarantee than a protected branch, and Control Plane has
        to know the difference rather than discover it after a merge.
        """
        branch = cfg["target_branch"]
        ok, payload, status = self.api_optional(
            repo, cfg, f"{self._prefix(cfg)}/branches/{branch}/protection"
        )
        if ok and isinstance(payload, dict) and payload:
            reviews_rule = payload.get("required_pull_request_reviews")
            required_count = 0
            if isinstance(reviews_rule, dict):
                required_count = reviews_rule.get("required_approving_review_count") or 0
            checks_rule = payload.get("required_status_checks")
            contexts = []
            if isinstance(checks_rule, dict):
                contexts = list(checks_rule.get("contexts") or [])
                for check in checks_rule.get("checks") or []:
                    if isinstance(check, dict) and _str(check.get("context")):
                        contexts.append(check["context"])
            return {
                "state": POLICY_ENFORCED,
                "source": "branch_protection",
                "branch": branch,
                "required_approving_reviews": required_count if isinstance(required_count, int) else 0,
                "required_checks": sorted(set(contexts)),
            }
        if status == 403:
            message = (payload or {}).get("message") if isinstance(payload, dict) else None
            return {
                "state": POLICY_UNAVAILABLE_PLAN,
                "source": None,
                "branch": branch,
                "detail": message or "Branch protection is not available for this repository.",
                "remediation": "Make the repository public, or move it to a plan that includes "
                               "branch protection, or keep merge_mode at DEVELOPER_REVIEW.",
            }
        return {
            "state": POLICY_NOT_CONFIGURED,
            "source": None,
            "branch": branch,
            "detail": f"No branch protection is configured for '{branch}'.",
            "remediation": "Configure required reviews and required status checks on the target "
                           "branch, or keep merge_mode at DEVELOPER_REVIEW.",
        }

    def assert_policy_mergeable(self, repo, cfg, task, project, pull):
        """Verify the requirements GitHub itself declares for the target branch.

        GitHub publishes no single mergeable verdict, so the declared requirements are
        read from branch protection and each one is checked against this candidate. When
        no policy is declared the adapter refuses: there is nothing to verify against,
        and reconstructing a policy here would move the decision out of the forge and
        into this file.
        """
        policy = self.policy_state(repo, cfg)
        if policy["state"] in POLICY_DEGRADED:
            raise ForgeError(
                f"Agent merge requires a forge-enforced merge policy. {policy['detail']} "
                f"Without it nothing on GitHub prevents a direct push to "
                f"'{policy['branch']}', so Control Plane would be the only guard and could "
                f"be bypassed out of band. {policy['remediation']}"
            )

        # Configuration first, candidate second. Both of these are answered by data
        # already in hand, while the checks below cost network calls; surfacing a
        # misconfigured project as a missing approval would send the reader to the
        # wrong place.
        required_count = policy["required_approving_reviews"]
        _need(
            required_count >= 1,
            f"Branch '{policy['branch']}' requires zero approving reviews. Set at least one "
            f"before enabling agent merge.",
        )
        required_contexts = policy["required_checks"]
        if cfg.get("ci_required") is True:
            _need(
                required_contexts,
                f"ci_required is true but branch '{policy['branch']}' declares no required "
                f"status checks. Either declare them in branch protection or set "
                f"ci_required to false.",
            )

        head_sha = pull.get("head_sha")
        approvals = self._current_approvals(repo, cfg, task, head_sha)
        _need(
            len(approvals) >= required_count,
            f"Branch protection requires {required_count} approving review(s) for the candidate "
            f"SHA; {len(approvals)} present.",
        )
        if required_contexts:
            self._assert_required_checks(repo, cfg, head_sha, required_contexts)

    def _current_approvals(self, repo, cfg, task, head_sha):
        """Approving reviews that apply to this exact head SHA.

        A review is bound to the commit it was written against. Control Plane already
        pins the candidate SHA, so an approval of an earlier commit is not accepted even
        when the repository's own stale-review dismissal is switched off.
        """
        reviews = self.api(repo, cfg, f'{self._prefix(cfg)}/pulls/{task["mr_iid"]}/reviews')
        latest = {}
        for review in reviews or []:
            if not isinstance(review, dict):
                continue
            login = (review.get("user") or {}).get("login")
            state = review.get("state")
            if not _str(login) or state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                continue
            # Reviews arrive oldest first; the last one per reviewer wins.
            latest[login] = review
        approvals = []
        for login, review in latest.items():
            if review.get("state") != "APPROVED":
                continue
            if review.get("commit_id") != head_sha:
                continue
            approvals.append(login)
        return sorted(approvals)

    def _assert_required_checks(self, repo, cfg, sha, contexts):
        seen = {}
        runs = self.api(repo, cfg, f"{self._prefix(cfg)}/commits/{sha}/check-runs")
        for run in (runs or {}).get("check_runs", []):
            name = run.get("name")
            if not _str(name):
                continue
            seen[name] = run.get("conclusion") if run.get("status") == "completed" else "pending"
        combined = self.api(repo, cfg, f"{self._prefix(cfg)}/commits/{sha}/status")
        for status in (combined or {}).get("statuses", []):
            context = status.get("context")
            if _str(context):
                seen.setdefault(context, status.get("state"))
        ok = {"success", "neutral", "skipped"}
        missing = [c for c in contexts if c not in seen]
        _need(not missing, f"Required status check(s) absent for the candidate SHA: {', '.join(missing)}.")
        failed = [c for c in contexts if seen.get(c) not in ok]
        _need(not failed, f"Required status check(s) not successful for the candidate SHA: {', '.join(failed)}.")


_ADAPTERS = {"gitlab": GitLabForge, "github": GitHubForge}


def adapter(runtime, cfg):
    """Return the adapter for the configured provider."""
    forge = cfg.get("forge")
    _need(isinstance(forge, dict), "Project configuration must define a forge object.")
    provider = forge.get("provider")
    _need(provider in _ADAPTERS, f"Unsupported forge provider: {provider!r}. Use one of {', '.join(PROVIDERS)}.")
    return _ADAPTERS[provider](runtime)
