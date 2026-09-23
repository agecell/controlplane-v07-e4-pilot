#!/usr/bin/env python3
"""Two-provider tests for the v0.6 forge adapter.

These run against a fake transport rather than a live forge, so what they prove is
shape and decision logic, not that the recorded REST payloads still match either
provider today. The payloads below were copied from responses observed against
github.com/agecell/ProxyReference and from the GitLab fields v0.5 consumed; live
verification is tracked in HANDOVER.md, and the GitLab path has none yet.

The fake substitutes `runtime.run`, which is the seam `Forge.__init__` exists for. That
keeps `_argv`, `api` and `api_optional` under test — including the 403-versus-404
distinction that degraded mode depends on — instead of stubbing them out.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import forge
import assurance
import merge
from unittest.mock import patch


def cfg_for(provider: str, **over) -> dict:
    cfg = {
        "schema_version": 5,
        "kit_version": "0.7",
        "remote": "origin",
        "target_branch": "main",
        "ci_required": False,
        "forge": {
            "provider": provider,
            "host": "gitlab.example.invalid" if provider == "gitlab" else "github.com",
            "repo": "group/product" if provider == "gitlab" else "owner/product",
        },
    }
    cfg.update(over)
    return cfg


TASK = {"mr_iid": 7, "branch": "work/dev-a/STORY-101", "candidate_sha": "a" * 40}


class FakeTransport:
    """Stands in for runtime, recording argv and replaying routed responses.

    A route value is either a payload (replayed as a successful body) or an
    (exit_code, payload) pair, which is how an HTTP error is expressed: both CLIs exit
    non-zero and print the error body on stdout.
    """

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    # Mirrors runtime.run's signature and its allowed-exit-code contract.
    def run(self, argv, cwd=None, allowed=(0,), stdin=None):
        # _argv puts the path directly after --method, then appends -F fields.
        at = argv.index("--method")
        method, path = argv[at + 1], argv[at + 2]
        self.calls.append((method, path, list(argv)))
        if path not in self.routes:
            raise AssertionError(f"unrouted {method} {path}")
        entry = self.routes[path]
        code, payload = entry if isinstance(entry, tuple) else (0, entry)
        if code not in allowed:
            raise r.AEError(f"gh command failed (exit {code}); inspect privately.")
        return code, json.dumps(payload)

    def paths(self, method=None):
        return [p for m, p, _ in self.calls if method is None or m == method]


def gl(**over):
    """A GitLab merge-request payload in the shape v0.5 consumed."""
    mr = {
        "iid": 7,
        "source_project_id": 11,
        "target_project_id": 11,
        "source_branch": "work/dev-a/STORY-101",
        "target_branch": "main",
        "diff_refs": {"head_sha": "a" * 40, "base_sha": "b" * 40},
        "state": "opened",
        "draft": False,
        "has_conflicts": False,
        "detailed_merge_status": "mergeable",
        "head_pipeline": {"sha": "a" * 40, "status": "success"},
    }
    mr.update(over)
    return mr


def gh(**over):
    pr = {
        "number": 7,
        "state": "open",
        "merged": False,
        "draft": False,
        "mergeable": True,
        "auto_merge": None,
        "head": {"ref": "work/dev-a/STORY-101", "sha": "a" * 40, "repo": {"id": 22}},
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"id": 22}},
    }
    pr.update(over)
    return pr


GL_PROJECT = {"id": 11, "path_with_namespace": "group/product", "merge_method": "merge"}
GH_PROJECT = {
    "id": 22,
    "full_name": "owner/product",
    "allow_merge_commit": True,
    "allow_squash_merge": True,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": False,
    "default_branch": "main",
    "private": True,
}


def gitlab_routes(**over):
    routes = {
        "projects/group%2Fproduct": GL_PROJECT,
        "projects/group%2Fproduct/merge_requests/7": gl(),
    }
    routes.update(over)
    return routes


def github_routes(**over):
    routes = {
        "repos/owner/product": GH_PROJECT,
        "repos/owner/product/pulls/7": gh(),
        f'repos/owner/product/commits/{"a" * 40}/check-runs': {"check_runs": []},
        f'repos/owner/product/commits/{"a" * 40}/status': {"statuses": []},
    }
    routes.update(over)
    return routes


def make(provider, routes):
    transport = FakeTransport(routes)
    return forge.adapter(transport, cfg_for(provider)), transport


class AdapterSelectionTests(unittest.TestCase):
    def test_provider_selects_implementation(self):
        self.assertIsInstance(forge.adapter(FakeTransport({}), cfg_for("gitlab")), forge.GitLabForge)
        self.assertIsInstance(forge.adapter(FakeTransport({}), cfg_for("github")), forge.GitHubForge)

    def test_unsupported_provider_refused(self):
        cfg = cfg_for("gitlab"); cfg["forge"]["provider"] = "bitbucket"
        with self.assertRaises(forge.ForgeError): forge.adapter(FakeTransport({}), cfg)

    def test_missing_forge_block_refused(self):
        with self.assertRaises(forge.ForgeError): forge.adapter(FakeTransport({}), {"target_branch": "main"})

    def test_runtime_translates_adapter_error_to_aeerror(self):
        # Callers above the seam only ever handle AEError.
        cfg = cfg_for("gitlab"); cfg["forge"]["provider"] = "bitbucket"
        with self.assertRaises(r.AEError): r.forge_adapter(cfg)


class TransportTests(unittest.TestCase):
    def test_each_provider_calls_its_own_cli_and_host(self):
        for provider, cli, host in (("gitlab", "glab", "gitlab.example.invalid"), ("github", "gh", "github.com")):
            adapter, transport = make(provider, gitlab_routes() if provider == "gitlab" else github_routes())
            adapter.project(None, cfg_for(provider))
            argv = transport.calls[0][2]
            self.assertEqual(cli, argv[0])
            self.assertEqual(host, argv[argv.index("--hostname") + 1])

    def test_fields_serialize_booleans_as_forge_literals(self):
        adapter, transport = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/merge": {}}))
        adapter.merge(None, cfg_for("gitlab"), TASK, "squash")
        argv = transport.calls[-1][2]
        self.assertIn("--method", argv); self.assertEqual("PUT", argv[argv.index("--method") + 1])
        self.assertIn("squash=true", argv)
        self.assertIn("auto_merge=false", argv)
        self.assertIn(f'sha={"a" * 40}', argv)

    def test_unreadable_body_is_a_forge_error(self):
        class Garbage(FakeTransport):
            def run(self, argv, cwd=None, allowed=(0,), stdin=None): return 0, "<html>not json</html>"
        adapter = forge.adapter(Garbage({}), cfg_for("github"))
        with self.assertRaises(forge.ForgeError): adapter.project(None, cfg_for("github"))


class NormalizedProjectTests(unittest.TestCase):
    def test_both_providers_return_the_same_keys(self):
        gl_project, _ = make("gitlab", gitlab_routes())
        gh_project, _ = make("github", github_routes())
        a = gl_project.project(None, cfg_for("gitlab"))
        b = gh_project.project(None, cfg_for("github"))
        self.assertEqual(set(a), set(b))
        self.assertEqual({"id", "full_name", "merge_methods", "deletes_source_branch", "_raw"}, set(a))
        self.assertEqual("group/product", a["full_name"])
        self.assertEqual("owner/product", b["full_name"])

    def test_gitlab_merge_method_maps_to_one_permitted_method(self):
        for native, expected in (("merge", {"merge_commit"}), ("ff", {"fast_forward"}), ("rebase_merge", {"fast_forward"})):
            adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct": dict(GL_PROJECT, merge_method=native)}))
            self.assertEqual(expected, adapter.project(None, cfg_for("gitlab"))["merge_methods"])

    def test_github_allow_flags_map_to_permitted_methods(self):
        adapter, _ = make("github", github_routes())
        self.assertEqual({"merge_commit", "squash"}, adapter.project(None, cfg_for("github"))["merge_methods"])

    def test_identity_mismatch_refused_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct": dict(GL_PROJECT, path_with_namespace="group/other")}))
        with self.assertRaises(forge.ForgeError): adapter.project(None, cfg_for("gitlab"))
        adapter, _ = make("github", github_routes(**{"repos/owner/product": dict(GH_PROJECT, full_name="owner/other")}))
        with self.assertRaises(forge.ForgeError): adapter.project(None, cfg_for("github"))


class NormalizedPullRequestTests(unittest.TestCase):
    def both(self):
        gl_adapter, _ = make("gitlab", gitlab_routes())
        gh_adapter, _ = make("github", github_routes())
        return (gl_adapter.pull_request(None, cfg_for("gitlab"), TASK),
                gh_adapter.pull_request(None, cfg_for("github"), TASK))

    def test_the_two_providers_normalize_to_one_shape(self):
        a, b = self.both()
        self.assertEqual(set(a), set(b))
        for pull in (a, b):
            self.assertEqual(7, pull["number"])
            self.assertEqual("open", pull["state"])
            self.assertFalse(pull["draft"])
            self.assertEqual("work/dev-a/STORY-101", pull["source_branch"])
            self.assertEqual("main", pull["target_branch"])
            self.assertEqual("a" * 40, pull["head_sha"])
            self.assertTrue(pull["same_project"])
            self.assertFalse(pull["has_conflicts"])
            self.assertFalse(pull["auto_merge_queued"])

    def test_project_pull_accepts_both_providers_through_runtime(self):
        # The identity checks runtime performs must hold against either normalized pull.
        for provider, routes in (("gitlab", gitlab_routes()), ("github", github_routes())):
            cfg = cfg_for(provider)
            adapter, _ = make(provider, routes)
            pull = adapter.pull_request(None, cfg, TASK)
            self.assertEqual(TASK["branch"], pull["source_branch"])
            self.assertEqual(cfg["target_branch"], pull["target_branch"])
            self.assertEqual(TASK["candidate_sha"], pull["head_sha"])

    def test_github_null_mergeable_is_treated_as_conflict(self):
        # GitHub computes this asynchronously; an unknown answer must fail closed.
        for value in (None, False, "unknown"):
            adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": gh(mergeable=value)}))
            self.assertTrue(adapter.pull_request(None, cfg_for("github"), TASK)["has_conflicts"])

    def test_gitlab_unknown_conflict_flag_is_treated_as_conflict(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": gl(has_conflicts=None)}))
        self.assertTrue(adapter.pull_request(None, cfg_for("gitlab"), TASK)["has_conflicts"])

    def test_fork_or_cross_project_is_reported_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": gl(source_project_id=99)}))
        self.assertFalse(adapter.pull_request(None, cfg_for("gitlab"), TASK)["same_project"])
        pr = gh(); pr["head"] = {"ref": "work/dev-a/STORY-101", "sha": "a" * 40, "repo": {"id": 99}}
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": pr}))
        self.assertFalse(adapter.pull_request(None, cfg_for("github"), TASK)["same_project"])

    def test_wrong_pull_request_number_refused_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": gl(iid=8)}))
        with self.assertRaises(forge.ForgeError): adapter.pull_request(None, cfg_for("gitlab"), TASK)
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": gh(number=8)}))
        with self.assertRaises(forge.ForgeError): adapter.pull_request(None, cfg_for("github"), TASK)

    def test_merged_and_closed_states_normalize(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": gl(state="merged")}))
        self.assertEqual("merged", adapter.pull_request(None, cfg_for("gitlab"), TASK)["state"])
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": gh(merged=True, state="closed")}))
        self.assertEqual("merged", adapter.pull_request(None, cfg_for("github"), TASK)["state"])
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": gl(state="closed")}))
        self.assertEqual("closed", adapter.pull_request(None, cfg_for("gitlab"), TASK)["state"])
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": gh(state="closed")}))
        self.assertEqual("closed", adapter.pull_request(None, cfg_for("github"), TASK)["state"])

    def test_github_collapses_check_runs_and_statuses_into_one_verdict(self):
        sha = "a" * 40
        adapter, _ = make("github", github_routes(**{
            f"repos/owner/product/commits/{sha}/check-runs": {"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]},
            f"repos/owner/product/commits/{sha}/status": {"statuses": [{"context": "legacy", "state": "success"}]},
        }))
        self.assertEqual({"status": "success", "sha": sha}, adapter.pull_request(None, cfg_for("github"), TASK)["ci"])
        adapter, _ = make("github", github_routes(**{
            f"repos/owner/product/commits/{sha}/check-runs": {"check_runs": [{"name": "build", "status": "in_progress"}]},
        }))
        self.assertEqual("failed", adapter.pull_request(None, cfg_for("github"), TASK)["ci"]["status"])


class MergeResultNormalizationTests(unittest.TestCase):
    """The post-merge proof reads these, so both providers must supply them."""

    FIELDS = ("merged_at", "base_sha", "merge_commit_sha", "squash_commit_sha")

    def test_both_providers_report_the_merge_result_fields(self):
        merged_gl = gl(state="merged", merged_at="2026-09-21T11:00:00Z", merge_commit_sha="d" * 40)
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": merged_gl}))
        a = adapter.pull_request(None, cfg_for("gitlab"), TASK)
        merged_gh = gh(merged=True, state="closed", merged_at="2026-09-21T11:00:00Z", merge_commit_sha="d" * 40)
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": merged_gh}))
        b = adapter.pull_request(None, cfg_for("github"), TASK)
        for pull in (a, b):
            for field in self.FIELDS:
                self.assertIn(field, pull)
            self.assertEqual("2026-09-21T11:00:00Z", pull["merged_at"])
            self.assertEqual("d" * 40, pull["merge_commit_sha"])
            self.assertEqual("b" * 40, pull["base_sha"])

    def test_gitlab_reports_a_squash_result_and_github_cannot(self):
        merged = gl(state="merged", merged_at="2026-09-21T11:00:00Z",
                    merge_commit_sha="d" * 40, squash_commit_sha="e" * 40)
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7": merged}))
        self.assertEqual("e" * 40, adapter.pull_request(None, cfg_for("gitlab"), TASK)["squash_commit_sha"])
        # GitHub publishes one merge_commit_sha whatever the method, so there is nothing
        # that identifies a squash result; the proof must not guess from silence.
        merged = gh(merged=True, merged_at="2026-09-21T11:00:00Z", merge_commit_sha="e" * 40)
        adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7": merged}))
        self.assertIsNone(adapter.pull_request(None, cfg_for("github"), TASK)["squash_commit_sha"])

    def test_integration_proof_reads_only_normalized_names(self):
        # Guards against the proof path drifting back onto one provider's field names:
        # a pull carrying no provider-specific keys at all must still be refused for
        # the right reason, not with a KeyError.
        with self.assertRaises(r.AEError) as caught:
            r.integration_proof(None, dict(TASK), {"state": "open", "head_sha": "a" * 40}, "f" * 40)
        self.assertIn("not verified merged", str(caught.exception))
        with self.assertRaises(r.AEError) as caught:
            r.integration_proof(None, dict(TASK),
                                {"state": "merged", "merged_at": "2026-09-21T11:00:00Z", "head_sha": "c" * 40},
                                "f" * 40)
        self.assertIn("Wrong candidate", str(caught.exception))


class IntegrationProofOverRealGitTests(unittest.TestCase):
    """The post-merge proof, against a real repository rather than a fake transport.

    Small but real: the whole point of these two methods is what Git can actually
    demonstrate, which a stub cannot stand in for.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.g("init", "-b", "main")
        self.g("config", "user.name", "Fixture")
        self.g("config", "user.email", "fixture@example.invalid")
        (self.repo / "a.txt").write_text("base\n")
        self.g("add", "."); self.g("commit", "-m", "base")
        self.base = self.g("rev-parse", "HEAD")
        self.g("switch", "-c", "work/dev-a/S1")
        (self.repo / "a.txt").write_text("changed\n")
        self.g("add", "."); self.g("commit", "-m", "candidate")
        self.candidate = self.g("rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def g(self, *args):
        return r.git(self.repo, *args)[1]

    def task(self):
        return {"mr_iid": 7, "branch": "work/dev-a/S1", "candidate_sha": self.candidate}

    def pull(self, **over):
        base = {"state": "merged", "merged_at": "2026-09-21T11:00:00Z", "head_sha": self.candidate,
                "base_sha": self.base, "merge_commit_sha": None, "squash_commit_sha": None}
        base.update(over)
        return base

    def test_merge_commit_is_proven_by_candidate_ancestry(self):
        self.g("switch", "main")
        self.g("merge", "--no-ff", "work/dev-a/S1", "-m", "merge")
        merge_sha = self.g("rev-parse", "HEAD")
        proof = r.integration_proof(self.repo, self.task(), self.pull(merge_commit_sha=merge_sha), merge_sha)
        self.assertEqual("candidate_ancestry", proof["method"])
        self.assertEqual(merge_sha, proof["result_sha"])

    def test_squash_is_proven_by_exact_delta(self):
        self.g("switch", "main")
        self.g("merge", "--squash", "work/dev-a/S1")
        self.g("commit", "-m", "squash")
        squash_sha = self.g("rev-parse", "HEAD")
        proof = r.integration_proof(self.repo, self.task(),
                                    self.pull(merge_commit_sha=squash_sha, squash_commit_sha=squash_sha),
                                    squash_sha)
        self.assertEqual("squash_exact_delta", proof["method"])
        self.assertEqual(squash_sha, proof["result_sha"])

    def test_squash_with_a_different_delta_is_refused(self):
        self.g("switch", "main")
        (self.repo / "a.txt").write_text("something else\n")
        self.g("add", "."); self.g("commit", "-m", "not the candidate delta")
        other = self.g("rev-parse", "HEAD")
        with self.assertRaises(r.AEError) as caught:
            r.integration_proof(self.repo, self.task(),
                                self.pull(squash_commit_sha=other), other)
        self.assertIn("exact blob/mode match", str(caught.exception))

    def test_rewritten_history_without_a_squash_field_is_refused(self):
        # What GitHub's squash and rebase merges look like from here: a merge result
        # exists, it is not a descendant of the candidate, and nothing reports it as a
        # squash whose delta could be compared instead. Assuming it corresponds to the
        # candidate is the one thing this function exists to rule out.
        self.g("switch", "main")
        self.g("merge", "--squash", "work/dev-a/S1")
        self.g("commit", "-m", "rewritten")
        rewritten = self.g("rev-parse", "HEAD")
        with self.assertRaises(r.AEError) as caught:
            r.integration_proof(self.repo, self.task(),
                                self.pull(merge_commit_sha=rewritten), rewritten)
        self.assertIn("rewrote history", str(caught.exception))

    def test_candidate_absent_from_target_is_refused(self):
        with self.assertRaises(r.AEError) as caught:
            r.integration_proof(self.repo, self.task(), self.pull(), self.base)
        self.assertIn("Candidate not in target", str(caught.exception))


class ProvableMergeMethodTests(unittest.TestCase):
    def test_gitlab_can_prove_every_named_method(self):
        adapter, _ = make("gitlab", gitlab_routes())
        self.assertEqual(set(forge.MERGE_METHODS), set(adapter.provable_merge_methods))

    def test_github_can_only_prove_a_merge_commit(self):
        adapter, _ = make("github", github_routes())
        self.assertEqual(("merge_commit",), adapter.provable_merge_methods)

    def test_every_provable_method_is_a_named_method(self):
        for provider in forge.PROVIDERS:
            adapter = forge.adapter(FakeTransport({}), cfg_for(provider))
            for method in adapter.provable_merge_methods:
                self.assertIn(method, forge.MERGE_METHODS)

    def preflight(self, provider, merge_method):
        """Drive merge.preflight far enough to reach the provable-method assertion.

        Everything before it is stubbed: the point is that an unprovable combination is
        refused while the merge has not happened, not to re-test the earlier gates.
        """
        cfg = cfg_for(provider, merge_mode="AGENT_MERGE", merge_method=merge_method,
                      merge_policy={"approved": True, "approval_ref": "setup/merge",
                                    "human_review_required": False, "human_approvers": [],
                                    "mandatory_checks": ["focused_tests"],
                                    "default_checks": [], "check_evidence_kinds": {},
                                    "remote_lock_enabled": True,
                                    "lock_branch": "ae/locks/integration"})
        candidate = TASK["candidate_sha"]
        pull = {"number": 7, "state": "open", "draft": False, "source_branch": TASK["branch"],
                "target_branch": "main", "head_sha": candidate, "same_project": True,
                "has_conflicts": False, "auto_merge_queued": False,
                "deletes_source_on_merge": False, "ci": {"status": "success", "sha": candidate},
                "_raw": {"detailed_merge_status": "mergeable"}}
        project = {"id": 1, "full_name": cfg["forge"]["repo"],
                   "merge_methods": set(forge.MERGE_METHODS)}
        task = dict(TASK, schema_version=None, developer="dev-a", task_id="STORY-101",
                    merge_mode_override=None)
        transport = FakeTransport(github_routes(**{PROTECTION: protection(reviews=1, contexts=())},
                                                **{REVIEWS: [review("human-a", "APPROVED", candidate)]})
                                  if provider == "github" else gitlab_routes())
        with patch.object(r, "config_ready", lambda *a, **k: None),              patch.object(merge, "quality", lambda *a, **k: {"target_sha": "b" * 40}),              patch.object(r, "project_pull", lambda *a, **k: (project, pull)),              patch.object(r, "remote_head", lambda *a, **k: candidate),              patch.object(r, "fetch_target", lambda *a, **k: "b" * 40),              patch.object(r, "forge_adapter", lambda c: forge.adapter(transport, c)):
            return merge.preflight(None, cfg, task)

    def test_preflight_refuses_an_unprovable_method_before_merging(self):
        for method in ("squash", "fast_forward"):
            with self.assertRaises(r.AEError) as caught:
                self.preflight("github", method)
            message = str(caught.exception)
            self.assertIn("cannot prove", message)
            self.assertIn("merge_commit", message)
            self.assertIn("DEVELOPER_REVIEW", message)

    def test_preflight_accepts_a_provable_method(self):
        out = self.preflight("github", "merge_commit")
        self.assertEqual("MERGE_READY", out["result"])
        for method in forge.MERGE_METHODS:
            out = self.preflight("gitlab", method)
            self.assertEqual("MERGE_READY", out["result"], method)


class OpenPullRequestAndBranchTests(unittest.TestCase):
    def test_open_listing_normalizes_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            "projects/group%2Fproduct/merge_requests?state=opened&per_page=100&page=1": [
                {"iid": 7, "source_branch": "work/dev-a/S1", "target_branch": "main"}]}))
        self.assertEqual([{"number": 7, "source_branch": "work/dev-a/S1", "target_branch": "main"}],
                         adapter.open_pull_requests(None, cfg_for("gitlab"), 1))
        adapter, _ = make("github", github_routes(**{
            "repos/owner/product/pulls?state=open&per_page=100&page=1": [
                {"number": 7, "head": {"ref": "work/dev-a/S1"}, "base": {"ref": "main"}}]}))
        self.assertEqual([{"number": 7, "source_branch": "work/dev-a/S1", "target_branch": "main"}],
                         adapter.open_pull_requests(None, cfg_for("github"), 1))

    def test_branch_normalizes_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            "projects/group%2Fproduct/repository/branches/main": {
                "name": "main", "commit": {"id": "b" * 40}, "protected": True, "default": True}}))
        self.assertEqual({"name": "main", "sha": "b" * 40, "protected": True, "is_default": True},
                         adapter.branch(None, cfg_for("gitlab"), "main"))
        adapter, _ = make("github", github_routes(**{
            "repos/owner/product/branches/main": {"name": "main", "commit": {"sha": "b" * 40}, "protected": True}}))
        # GitHub has no per-branch default flag; it is derived from the repository.
        self.assertEqual({"name": "main", "sha": "b" * 40, "protected": True, "is_default": True},
                         adapter.branch(None, cfg_for("github"), "main"))


class CommentTests(unittest.TestCase):
    GL_NOTE = {"id": 55, "body": "APPROVE " + "a" * 40, "author": {"username": "human-a"},
               "system": False, "created_at": "2026-09-21T10:00:00Z"}
    GH_NOTE = {"id": 55, "body": "APPROVE " + "a" * 40, "user": {"login": "human-a", "type": "User"},
               "created_at": "2026-09-21T10:00:00Z", "issue_url": "https://api.github.com/repos/owner/product/issues/7"}

    def test_comment_normalizes_to_one_shape_on_both(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/notes/55": self.GL_NOTE}))
        a = adapter.comment(None, cfg_for("gitlab"), TASK, 55)
        adapter, _ = make("github", github_routes(**{"repos/owner/product/issues/comments/55": self.GH_NOTE}))
        b = adapter.comment(None, cfg_for("github"), TASK, 55)
        self.assertEqual(set(a), set(b))
        for note in (a, b):
            self.assertEqual("human-a", note["author"])
            self.assertFalse(note["system"])
            self.assertEqual("APPROVE " + "a" * 40, note["body"])

    def test_attestor_ref_keeps_each_provider_spelling(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/notes/55": self.GL_NOTE}))
        gl_ref = adapter.comment(None, cfg_for("gitlab"), TASK, 55)["ref"]
        adapter, _ = make("github", github_routes(**{"repos/owner/product/issues/comments/55": self.GH_NOTE}))
        gh_ref = adapter.comment(None, cfg_for("github"), TASK, 55)["ref"]
        # The GitLab spelling is byte-for-byte what v0.5 wrote, so pre-upgrade records
        # still parse and compare after the rewire.
        self.assertEqual("gitlab:mr:7:note:55:user:human-a", gl_ref)
        self.assertEqual("github:pr:7:comment:55:user:human-a", gh_ref)

    def test_assurance_parses_both_attestor_ref_spellings(self):
        for ref in ("gitlab:mr:7:note:55:user:human-a", "github:pr:7:comment:55:user:human-a"):
            self.assertEqual((55, "human-a"), assurance._parse_attestor_ref(ref, {"mr_iid": 7}))

    def test_attestor_ref_from_another_pull_request_refused(self):
        with self.assertRaises(r.AEError):
            assurance._parse_attestor_ref("github:pr:9:comment:55:user:human-a", {"mr_iid": 7})

    def test_gitlab_system_note_is_machine_generated(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            "projects/group%2Fproduct/merge_requests/7/notes/55": dict(self.GL_NOTE, system=True)}))
        self.assertTrue(adapter.comment(None, cfg_for("gitlab"), TASK, 55)["system"])

    def test_github_bot_comment_is_machine_generated(self):
        # GitHub publishes no system flag, so the author account type carries it.
        note = dict(self.GH_NOTE, user={"login": "renovate[bot]", "type": "Bot"})
        adapter, _ = make("github", github_routes(**{"repos/owner/product/issues/comments/55": note}))
        self.assertTrue(adapter.comment(None, cfg_for("github"), TASK, 55)["system"])

    def test_github_comment_from_another_pull_request_refused(self):
        note = dict(self.GH_NOTE, issue_url="https://api.github.com/repos/owner/product/issues/9")
        adapter, _ = make("github", github_routes(**{"repos/owner/product/issues/comments/55": note}))
        with self.assertRaises(forge.ForgeError): adapter.comment(None, cfg_for("github"), TASK, 55)


class BotCommentRefusedTests(unittest.TestCase):
    """A machine-written comment must not satisfy human authority on either provider."""

    def verify(self, provider, routes):
        cfg = cfg_for(provider)
        transport = FakeTransport(routes)
        original = r.forge_adapter
        try:
            r.forge_adapter = lambda c: forge.adapter(transport, c)
            return r.verify_forge_comment(None, cfg, dict(TASK), 55, "APPROVE " + "a" * 40,
                                          ["human-a", "renovate[bot]"], verify_mr=False)
        finally:
            r.forge_adapter = original

    def test_human_comment_accepted_on_both(self):
        out = self.verify("gitlab", gitlab_routes(**{
            "projects/group%2Fproduct/merge_requests/7/notes/55": CommentTests.GL_NOTE}))
        self.assertEqual("human-a", out["username"])
        self.assertEqual("gitlab:mr:7:note:55:user:human-a", out["attestor_ref"])
        out = self.verify("github", github_routes(**{
            "repos/owner/product/issues/comments/55": CommentTests.GH_NOTE}))
        self.assertEqual("human-a", out["username"])
        self.assertEqual("github:pr:7:comment:55:user:human-a", out["attestor_ref"])

    def test_evidence_ref_keeps_each_provider_spelling(self):
        # v0.5 wrote `human-attestation:gitlab:mr:{iid}:note:{id}`. Re-lettering that to
        # a generic pr/comment form would have made every pre-upgrade record fail the
        # prefix check in assurance, so both refs are derived from the adapter's own.
        out = self.verify("gitlab", gitlab_routes(**{
            "projects/group%2Fproduct/merge_requests/7/notes/55": CommentTests.GL_NOTE}))
        self.assertEqual("human-attestation:gitlab:mr:7:note:55", out["evidence_ref"])
        out = self.verify("github", github_routes(**{
            "repos/owner/product/issues/comments/55": CommentTests.GH_NOTE}))
        self.assertEqual("human-attestation:github:pr:7:comment:55", out["evidence_ref"])

    def test_both_evidence_ref_spellings_are_accepted_by_assurance(self):
        for provider, routes, note_key, note in (
            ("gitlab", gitlab_routes, "projects/group%2Fproduct/merge_requests/7/notes/55", CommentTests.GL_NOTE),
            ("github", github_routes, "repos/owner/product/issues/comments/55", CommentTests.GH_NOTE),
        ):
            out = self.verify(provider, routes(**{note_key: note}))
            self.assertTrue(assurance._is_attestation_evidence_ref(out["evidence_ref"]), provider)
            self.assertTrue(assurance._is_attestor_ref(out["attestor_ref"]), provider)

    def test_bot_comment_refused_on_gitlab(self):
        with self.assertRaises(r.AEError):
            self.verify("gitlab", gitlab_routes(**{
                "projects/group%2Fproduct/merge_requests/7/notes/55": dict(CommentTests.GL_NOTE, system=True)}))

    def test_bot_comment_refused_on_github(self):
        note = dict(CommentTests.GH_NOTE, user={"login": "renovate[bot]", "type": "Bot"})
        with self.assertRaises(r.AEError):
            self.verify("github", github_routes(**{"repos/owner/product/issues/comments/55": note}))

    def test_unauthorized_author_refused_on_both(self):
        note = dict(CommentTests.GL_NOTE, author={"username": "stranger"})
        with self.assertRaises(r.AEError):
            self.verify("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/notes/55": note}))
        note = dict(CommentTests.GH_NOTE, user={"login": "stranger", "type": "User"})
        with self.assertRaises(r.AEError):
            self.verify("github", github_routes(**{"repos/owner/product/issues/comments/55": note}))


PROTECTION = "repos/owner/product/branches/main/protection"
FORBIDDEN = (1, {"message": "Upgrade to GitHub Pro or make this repository public to use this feature.", "status": "403"})
NOT_FOUND = (1, {"message": "Branch not protected", "status": "404"})


def protection(reviews=2, contexts=("build",)):
    return {
        "required_pull_request_reviews": {"required_approving_review_count": reviews, "dismiss_stale_reviews": False},
        "required_status_checks": {"strict": False, "contexts": list(contexts)},
    }


class PolicyStateTests(unittest.TestCase):
    """The four answers policy_state can give, one test each."""

    def test_gitlab_delegates_its_own_verdict(self):
        adapter, transport = make("gitlab", gitlab_routes())
        state = adapter.policy_state(None, cfg_for("gitlab"))
        self.assertEqual(forge.POLICY_FORGE_VERDICT, state["state"])
        self.assertEqual("detailed_merge_status", state["source"])
        # Delegation costs no network call: the verdict arrives with the merge request.
        self.assertEqual([], transport.paths())

    def test_github_enforced_reads_the_declared_requirements(self):
        adapter, _ = make("github", github_routes(**{PROTECTION: protection(reviews=2, contexts=("build", "tests"))}))
        state = adapter.policy_state(None, cfg_for("github"))
        self.assertEqual(forge.POLICY_ENFORCED, state["state"])
        self.assertEqual("branch_protection", state["source"])
        self.assertEqual(2, state["required_approving_reviews"])
        self.assertEqual(["build", "tests"], state["required_checks"])

    def test_github_enforced_reads_the_newer_checks_array(self):
        rule = protection(contexts=())
        rule["required_status_checks"]["checks"] = [{"context": "build", "app_id": 1}]
        adapter, _ = make("github", github_routes(**{PROTECTION: rule}))
        self.assertEqual(["build"], adapter.policy_state(None, cfg_for("github"))["required_checks"])

    def test_github_403_is_unavailable_plan_not_missing_configuration(self):
        # Observed on a private repository under a free plan; the distinction from 404
        # is the whole reason api_optional reports the status code.
        adapter, _ = make("github", github_routes(**{PROTECTION: FORBIDDEN}))
        state = adapter.policy_state(None, cfg_for("github"))
        self.assertEqual(forge.POLICY_UNAVAILABLE_PLAN, state["state"])
        self.assertIsNone(state["source"])
        self.assertIn("Upgrade to GitHub Pro", state["detail"])
        self.assertTrue(state["remediation"])

    def test_github_404_is_not_configured(self):
        adapter, _ = make("github", github_routes(**{PROTECTION: NOT_FOUND}))
        state = adapter.policy_state(None, cfg_for("github"))
        self.assertEqual(forge.POLICY_NOT_CONFIGURED, state["state"])
        self.assertIsNone(state["source"])
        self.assertIn("main", state["detail"])

    def test_both_degraded_states_are_listed_as_degraded(self):
        self.assertEqual({forge.POLICY_NOT_CONFIGURED, forge.POLICY_UNAVAILABLE_PLAN}, set(forge.POLICY_DEGRADED))
        self.assertNotIn(forge.POLICY_ENFORCED, forge.POLICY_DEGRADED)
        self.assertNotIn(forge.POLICY_FORGE_VERDICT, forge.POLICY_DEGRADED)


def review(login, state, commit):
    return {"user": {"login": login}, "state": state, "commit_id": commit}


REVIEWS = "repos/owner/product/pulls/7/reviews"


class AgentMergeRefusedWhenDegradedTests(unittest.TestCase):
    def assert_refused(self, protection_entry, expect_in_message):
        cfg = cfg_for("github")
        adapter, transport = make("github", github_routes(**{PROTECTION: protection_entry}))
        pull = adapter.pull_request(None, cfg, TASK)
        with self.assertRaises(forge.ForgeError) as caught:
            adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)
        message = str(caught.exception)
        for fragment in expect_in_message:
            self.assertIn(fragment, message)
        # Nothing about the candidate was inspected: there is no policy to check against.
        self.assertNotIn(REVIEWS, transport.paths())

    def test_unavailable_plan_refuses_agent_merge(self):
        self.assert_refused(FORBIDDEN, ["forge-enforced merge policy", "Upgrade to GitHub Pro", "main"])

    def test_not_configured_refuses_agent_merge(self):
        self.assert_refused(NOT_FOUND, ["forge-enforced merge policy", "No branch protection", "main"])

    def test_zero_required_reviews_refuses_agent_merge(self):
        # Protection exists but declares nothing, so there is still no approval to verify.
        cfg = cfg_for("github")
        adapter, transport = make("github", github_routes(**{PROTECTION: protection(reviews=0)}))
        pull = adapter.pull_request(None, cfg, TASK)
        with self.assertRaises(forge.ForgeError) as caught:
            adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)
        self.assertIn("zero approving reviews", str(caught.exception))
        self.assertNotIn(REVIEWS, transport.paths())

    def test_ci_required_without_declared_checks_is_reported_before_candidate_checks(self):
        # The live ENFORCED run hit exactly this combination. Reporting it as a missing
        # approval after several network calls sent the reader to the wrong place.
        cfg = cfg_for("github", ci_required=True)
        transport = FakeTransport(github_routes(**{PROTECTION: protection(contexts=())}))
        adapter = forge.adapter(transport, cfg)
        pull = adapter.pull_request(None, cfg, TASK)
        with self.assertRaises(forge.ForgeError) as caught:
            adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)
        self.assertIn("ci_required is true", str(caught.exception))
        self.assertIn("no required", str(caught.exception))
        self.assertNotIn(REVIEWS, transport.paths())

    def test_gitlab_is_not_degraded_by_construction(self):
        # GitLab's verdict is delegated, so the degraded path does not apply to it.
        cfg = cfg_for("gitlab")
        adapter, _ = make("gitlab", gitlab_routes())
        pull = adapter.pull_request(None, cfg, TASK)
        adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)

    def test_gitlab_refuses_when_its_own_verdict_is_not_mergeable(self):
        cfg = cfg_for("gitlab")
        for status in ("blocked_status", "not_approved", "ci_still_running", None):
            adapter, _ = make("gitlab", gitlab_routes(**{
                "projects/group%2Fproduct/merge_requests/7": gl(detailed_merge_status=status)}))
            pull = adapter.pull_request(None, cfg, TASK)
            with self.assertRaises(forge.ForgeError):
                adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)


class ApprovalBoundToCandidateShaTests(unittest.TestCase):
    """An approval of an earlier commit must never count, whatever the repository sets."""

    def routes(self, reviews, protection_rule=None, checks=None):
        sha = "a" * 40
        extra = {PROTECTION: protection_rule or protection(reviews=1, contexts=()), REVIEWS: reviews}
        if checks is not None:
            extra[f"repos/owner/product/commits/{sha}/check-runs"] = checks
        return github_routes(**extra)

    def mergeable(self, reviews, cfg=None, protection_rule=None, checks=None):
        cfg = cfg or cfg_for("github")
        adapter, _ = make("github", self.routes(reviews, protection_rule, checks))
        pull = adapter.pull_request(None, cfg, TASK)
        adapter.assert_policy_mergeable(None, cfg, TASK, pull["_project"], pull)

    def test_approval_of_the_candidate_counts(self):
        self.mergeable([review("human-a", "APPROVED", "a" * 40)])

    def test_approval_of_an_earlier_commit_does_not_count(self):
        # dismiss_stale_reviews is false in this fixture, exactly as the live ENFORCED
        # repository had it, so the binding here is the only thing rejecting it.
        with self.assertRaises(forge.ForgeError) as caught:
            self.mergeable([review("human-a", "APPROVED", "c" * 40)])
        self.assertIn("1 approving review(s)", str(caught.exception))
        self.assertIn("0 present", str(caught.exception))

    def test_latest_review_per_reviewer_wins(self):
        # An approval superseded by a later change request no longer counts.
        with self.assertRaises(forge.ForgeError):
            self.mergeable([review("human-a", "APPROVED", "a" * 40),
                            review("human-a", "CHANGES_REQUESTED", "a" * 40)])
        # And the reverse: a change request the reviewer then withdrew by approving.
        self.mergeable([review("human-a", "CHANGES_REQUESTED", "a" * 40),
                        review("human-a", "APPROVED", "a" * 40)])

    def test_comments_and_dismissed_reviews_do_not_approve(self):
        with self.assertRaises(forge.ForgeError):
            self.mergeable([review("human-a", "COMMENTED", "a" * 40), review("human-b", "DISMISSED", "a" * 40)])

    def test_two_required_reviews_need_two_distinct_reviewers(self):
        rule = protection(reviews=2, contexts=())
        with self.assertRaises(forge.ForgeError):
            self.mergeable([review("human-a", "APPROVED", "a" * 40)], protection_rule=rule)
        self.mergeable([review("human-a", "APPROVED", "a" * 40), review("human-b", "APPROVED", "a" * 40)],
                       protection_rule=rule)

    def test_required_check_must_be_present_and_successful_for_the_candidate(self):
        rule = protection(reviews=1, contexts=("build",))
        approved = [review("human-a", "APPROVED", "a" * 40)]
        with self.assertRaises(forge.ForgeError) as caught:
            self.mergeable(approved, protection_rule=rule, checks={"check_runs": []})
        self.assertIn("absent", str(caught.exception))
        with self.assertRaises(forge.ForgeError) as caught:
            self.mergeable(approved, protection_rule=rule,
                           checks={"check_runs": [{"name": "build", "status": "completed", "conclusion": "failure"}]})
        self.assertIn("not successful", str(caught.exception))
        with self.assertRaises(forge.ForgeError) as caught:
            self.mergeable(approved, protection_rule=rule,
                           checks={"check_runs": [{"name": "build", "status": "in_progress"}]})
        self.assertIn("not successful", str(caught.exception))
        self.mergeable(approved, protection_rule=rule,
                       checks={"check_runs": [{"name": "build", "status": "completed", "conclusion": "success"}]})


class MergeMethodTests(unittest.TestCase):
    def test_github_maps_control_plane_method_names_to_its_own(self):
        for method, native in (("merge_commit", "merge"), ("squash", "squash"), ("fast_forward", "rebase")):
            adapter, transport = make("github", github_routes(**{"repos/owner/product/pulls/7/merge": {}}))
            adapter.merge(None, cfg_for("github"), TASK, method)
            argv = transport.calls[-1][2]
            self.assertIn(f"merge_method={native}", argv)
            self.assertIn(f'sha={"a" * 40}', argv)

    def test_gitlab_expresses_squash_as_a_separate_flag(self):
        for method, squash in (("merge_commit", "false"), ("squash", "true")):
            adapter, transport = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/merge": {}}))
            adapter.merge(None, cfg_for("gitlab"), TASK, method)
            self.assertIn(f"squash={squash}", transport.calls[-1][2])

    def test_every_named_method_is_mapped_on_both(self):
        # A method Control Plane names but an adapter cannot express would surface as a
        # KeyError at merge time, which is the worst possible moment to find it.
        for method in forge.MERGE_METHODS:
            adapter, _ = make("github", github_routes(**{"repos/owner/product/pulls/7/merge": {}}))
            adapter.merge(None, cfg_for("github"), TASK, method)
            adapter, _ = make("gitlab", gitlab_routes(**{"projects/group%2Fproduct/merge_requests/7/merge": {}}))
            adapter.merge(None, cfg_for("gitlab"), TASK, method)


class ForgeReadyTests(unittest.TestCase):
    def test_both_providers_validate(self):
        for provider in forge.PROVIDERS:
            r.forge_ready(cfg_for(provider))

    def test_forge_is_not_required_for_local_work(self):
        # F12: pool init, lane lease and build must run before a forge is chosen.
        cfg = cfg_for("gitlab", project_name="Fixture", team_coordination_ref="team/board",
                      merge_mode="DEVELOPER_REVIEW", configuration_approved=True, max_local_builders=1,
                      merge_policy={"approved": False, "approval_ref": "__CONFIGURE__",
                                    "human_review_required": False, "human_approvers": [],
                                    "mandatory_checks": ["build"], "default_checks": [],
                                    "check_evidence_kinds": {}},
                      assurance_policy={"approved": True, "approval_ref": "setup/assurance",
                                        "project_required_gates": [], "tier_required_gates": {"1": [], "2": [], "3": []},
                                        "human_attestors": {}, "waiver_policy": {"default_waivable": False, "gates": {}},
                                        "custom_gates": {}})
        cfg.pop("forge")
        r.config_ready(cfg)  # local path: no forge needed
        with self.assertRaises(r.AEError):
            r.config_ready(cfg, remote=True)  # remote path: forge required

    def test_unresolved_placeholders_refused(self):
        for field, value in (("provider", "__CONFIGURE__"), ("host", "__CONFIGURE__"), ("repo", "__CONFIGURE__")):
            cfg = cfg_for("github"); cfg["forge"][field] = value
            with self.assertRaises(r.AEError): r.forge_ready(cfg)

    def test_extra_or_missing_forge_keys_refused(self):
        cfg = cfg_for("github"); cfg["forge"]["token"] = "secret"
        with self.assertRaises(r.AEError): r.forge_ready(cfg)
        cfg = cfg_for("github"); cfg["forge"].pop("host")
        with self.assertRaises(r.AEError): r.forge_ready(cfg)

    def test_repo_must_be_owner_slash_name_without_url_parts(self):
        for value in ("product", "https://github.com/owner/product", "owner/../product", "owner/product?x=1"):
            cfg = cfg_for("github"); cfg["forge"]["repo"] = value
            with self.assertRaises(r.AEError): r.forge_ready(cfg)

    def test_host_may_carry_a_port_but_not_a_scheme(self):
        cfg = cfg_for("gitlab"); cfg["forge"]["host"] = "gitlab.example.invalid:8443"
        r.forge_ready(cfg)
        cfg["forge"]["host"] = "https://gitlab.example.invalid"
        with self.assertRaises(r.AEError): r.forge_ready(cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
