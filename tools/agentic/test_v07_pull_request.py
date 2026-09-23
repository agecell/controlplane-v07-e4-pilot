#!/usr/bin/env python3
"""Wave A1 tests: pull request creation behind the forge seam, and forge identity checks.

Two v0.6 findings from the live GitLab pilot:

* **F2** -- `forge.py` had no creation operation. The step was delegated to `glab mr
  create` / `gh pr create`, which cannot reach a self-managed host that is not the
  CLI's own default, so both pilot merge requests were opened by hand in the web UI,
  outside every check this kit performs.
* **F6** -- a configured attestor identity was never checked against the forge until
  the attestation step itself, so a Story nobody could satisfy consumed a full build
  and review cycle before failing.

The adapter tests reuse the fake transport from test_v06_forge, which substitutes
`runtime.run`. What they prove is the request shape and the decision logic, not that
either provider's REST still looks like this today; live verification is recorded
separately.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import forge
import contract
import guard
from test_v06_forge import FakeTransport, cfg_for, gl, gh, gitlab_routes, github_routes, make
from test_v05_precheck import RepoFixture
from test_v04 import RepoCase


TASK = {"developer": "dev-a", "task_id": "STORY-101", "branch": "work/dev-a/STORY-101",
        "candidate_sha": "a" * 40}

GL_CREATE = "projects/group%2Fproduct/merge_requests"
GL_LIST = "projects/group%2Fproduct/merge_requests?state=opened&per_page=100&page=1"
GH_CREATE = "repos/owner/product/pulls"
GH_LIST = "repos/owner/product/pulls?state=open&per_page=100&page=1"


def fields_of(argv):
    """Rebuild the -f/-F fields a call sent, keeping which form carried each one."""
    out = {}
    for i, token in enumerate(argv):
        if token in ("-f", "-F") and i + 1 < len(argv):
            key, _, value = argv[i + 1].partition("=")
            out[key] = (token, value)
    return out


# ---------------------------------------------------------------------------
# F2 -- creation behind the seam
# ---------------------------------------------------------------------------

class CreatePullRequestTests(unittest.TestCase):
    def test_gitlab_creates_and_returns_the_re_read_request(self):
        adapter, transport = make("gitlab", gitlab_routes(**{
            GL_LIST: [],
            GL_CREATE: gl(iid=7),
        }))
        pull = adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "STORY-101", "body")
        self.assertTrue(pull["created"])
        self.assertEqual(7, pull["number"])
        self.assertEqual("work/dev-a/STORY-101", pull["source_branch"])
        self.assertEqual("main", pull["target_branch"])
        self.assertIn(("POST", GL_CREATE), [(m, p) for m, p, _ in transport.calls])
        # The result is re-read from the forge, not assembled from the POST response.
        self.assertIn("projects/group%2Fproduct/merge_requests/7", transport.paths("GET"))

    def test_github_creates_and_returns_the_re_read_request(self):
        adapter, transport = make("github", github_routes(**{
            GH_LIST: [],
            GH_CREATE: gh(number=7),
        }))
        pull = adapter.create_pull_request(None, cfg_for("github"), TASK, "STORY-101", "body")
        self.assertTrue(pull["created"])
        self.assertEqual(7, pull["number"])
        self.assertIn(("POST", GH_CREATE), [(m, p) for m, p, _ in transport.calls])
        self.assertIn("repos/owner/product/pulls/7", transport.paths("GET"))

    def test_gitlab_never_asks_the_forge_to_act_on_its_own(self):
        adapter, transport = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "STORY-101", "body")
        argv = next(a for m, p, a in transport.calls if m == "POST" and p == GL_CREATE)
        sent = fields_of(argv)
        # Controlled cleanup deletes the branch only after the integration proof and the
        # traceability record exist. Set explicitly rather than left to the project
        # default, which is finding F5.
        self.assertEqual(("-F", "false"), sent["remove_source_branch"])
        self.assertEqual(("-F", "false"), sent["squash"])
        self.assertNotIn("merge_when_pipeline_succeeds", sent)
        self.assertNotIn("auto_merge", sent)

    def test_github_creation_sends_no_draft_and_no_auto_merge(self):
        adapter, transport = make("github", github_routes(**{GH_LIST: [], GH_CREATE: gh(number=7)}))
        adapter.create_pull_request(None, cfg_for("github"), TASK, "STORY-101", "body")
        sent = fields_of(next(a for m, p, a in transport.calls if m == "POST" and p == GH_CREATE))
        self.assertEqual(("-F", "false"), sent["draft"])
        self.assertNotIn("auto_merge", sent)

    def test_free_text_is_sent_as_a_raw_string_field(self):
        # Both CLIs read a typed (-F) value starting with '@' as a filename and coerce a
        # bare number. A title must never be able to do either.
        adapter, transport = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "@/etc/passwd", "1234")
        sent = fields_of(next(a for m, p, a in transport.calls if m == "POST" and p == GL_CREATE))
        self.assertEqual(("-f", "@/etc/passwd"), sent["title"])
        self.assertEqual(("-f", "1234"), sent["description"])

    def test_source_branch_outside_the_workflow_is_refused(self):
        for branch in ("feature/x", "main", "refs/heads/work/dev-a/S1", ""):
            adapter, transport = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
            with self.assertRaises(forge.ForgeError, msg=branch):
                adapter.create_pull_request(None, cfg_for("gitlab"), dict(TASK, branch=branch), "t", "")
            self.assertEqual([], transport.paths("POST"), branch)

    def test_records_branch_source_is_accepted(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            GL_LIST: [],
            GL_CREATE: gl(iid=7, source_branch="ae/records/dev-a/pilot"),
            "projects/group%2Fproduct/merge_requests/7": gl(iid=7, source_branch="ae/records/dev-a/pilot"),
        }))
        pull = adapter.create_pull_request(
            None, cfg_for("gitlab"), dict(TASK, branch="ae/records/dev-a/pilot"), "t", "")
        self.assertEqual("ae/records/dev-a/pilot", pull["source_branch"])

    def test_unconfigured_target_is_refused(self):
        adapter, transport = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        cfg = cfg_for("gitlab", target_branch="")
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg, TASK, "t", "")
        self.assertEqual([], transport.paths("POST"))

    def test_title_is_required_and_never_derived(self):
        adapter, transport = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        for title in ("", "   ", None):
            with self.assertRaises(forge.ForgeError, msg=repr(title)):
                adapter.create_pull_request(None, cfg_for("gitlab"), TASK, title, "")
        self.assertEqual([], transport.paths("POST"))

    def test_description_must_be_a_string(self):
        adapter, _ = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", None)

    def test_existing_request_is_returned_never_duplicated(self):
        for provider, listing, create, listed in (
            ("gitlab", GL_LIST, GL_CREATE,
             [{"iid": 7, "source_branch": "work/dev-a/STORY-101", "target_branch": "main"}]),
            ("github", GH_LIST, GH_CREATE,
             [{"number": 7, "head": {"ref": "work/dev-a/STORY-101"}, "base": {"ref": "main"}}]),
        ):
            routes = (gitlab_routes if provider == "gitlab" else github_routes)(**{listing: listed})
            adapter, transport = make(provider, routes)
            pull = adapter.create_pull_request(None, cfg_for(provider), TASK, "t", "")
            self.assertFalse(pull["created"], provider)
            self.assertEqual(7, pull["number"], provider)
            self.assertEqual([], transport.paths("POST"), provider)
            self.assertNotIn(create, transport.paths(), provider)

    def test_existing_request_to_a_different_target_is_refused(self):
        adapter, transport = make("gitlab", gitlab_routes(**{
            GL_LIST: [{"iid": 7, "source_branch": "work/dev-a/STORY-101", "target_branch": "release"}],
            "projects/group%2Fproduct/merge_requests/7": gl(iid=7, target_branch="release"),
        }))
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")
        self.assertEqual([], transport.paths("POST"))

    def test_creation_response_without_a_number_is_refused(self):
        adapter, _ = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: {"web_url": "x"}}))
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")

    def test_listing_that_never_ends_refuses_rather_than_duplicating(self):
        full_page = [{"iid": i, "source_branch": f"work/dev-a/S{i}", "target_branch": "main"}
                     for i in range(100)]
        routes = gitlab_routes()
        for page in range(1, 102):
            routes[f"projects/group%2Fproduct/merge_requests?state=opened&per_page=100&page={page}"] = full_page
        adapter, transport = make("gitlab", routes)
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")
        self.assertEqual([], transport.paths("POST"))

    def test_listing_is_paged_before_concluding_none_is_open(self):
        first = [{"iid": i, "source_branch": f"work/dev-a/S{i}", "target_branch": "main"}
                 for i in range(100)]
        adapter, transport = make("gitlab", gitlab_routes(**{
            GL_LIST: first,
            "projects/group%2Fproduct/merge_requests?state=opened&per_page=100&page=2":
                [{"iid": 7, "source_branch": "work/dev-a/STORY-101", "target_branch": "main"}],
        }))
        pull = adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")
        self.assertFalse(pull["created"])
        self.assertEqual([], transport.paths("POST"))

    def test_listed_request_without_a_number_is_refused(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            GL_LIST: [{"source_branch": "work/dev-a/STORY-101", "target_branch": "main"}]}))
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")

    def test_created_request_that_does_not_match_the_request_is_refused(self):
        # What was asked for and what the forge recorded are different facts.
        adapter, _ = make("gitlab", gitlab_routes(**{
            GL_LIST: [],
            GL_CREATE: gl(iid=7),
            "projects/group%2Fproduct/merge_requests/7": gl(iid=7, target_branch="release"),
        }))
        with self.assertRaises(forge.ForgeError):
            adapter.create_pull_request(None, cfg_for("gitlab"), TASK, "t", "")

    def test_both_providers_implement_creation(self):
        for cls in (forge.GitLabForge, forge.GitHubForge):
            self.assertIsNot(cls._open_pull_request, forge.Forge._open_pull_request, cls.__name__)


# ---------------------------------------------------------------------------
# F6 -- identity existence, and only existence
# ---------------------------------------------------------------------------

class UserExistsTests(unittest.TestCase):
    def test_gitlab_reports_presence_and_absence(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"users?username=agungwib": [{"username": "agungwib"}]}))
        self.assertTrue(adapter.user_exists(None, cfg_for("gitlab"), "agungwib"))
        adapter, _ = make("gitlab", gitlab_routes(**{"users?username=ghost": []}))
        self.assertFalse(adapter.user_exists(None, cfg_for("gitlab"), "ghost"))

    def test_gitlab_partial_match_is_not_an_account(self):
        # GitLab's lookup is a filtered list; a near match must not be read as the name.
        adapter, _ = make("gitlab", gitlab_routes(**{"users?username=agung": [{"username": "agungwib"}]}))
        self.assertFalse(adapter.user_exists(None, cfg_for("gitlab"), "agung"))

    def test_gitlab_non_list_answer_is_refused(self):
        adapter, _ = make("gitlab", gitlab_routes(**{"users?username=x": {"message": "403 Forbidden"}}))
        with self.assertRaises(forge.ForgeError):
            adapter.user_exists(None, cfg_for("gitlab"), "x")

    def test_github_reports_presence_and_absence(self):
        adapter, _ = make("github", github_routes(**{"users/octocat": {"login": "octocat"}}))
        self.assertTrue(adapter.user_exists(None, cfg_for("github"), "octocat"))
        adapter, _ = make("github", github_routes(**{
            "users/ghost": (1, {"message": "Not Found", "status": "404"})}))
        self.assertFalse(adapter.user_exists(None, cfg_for("github"), "ghost"))

    def test_github_case_variant_is_not_the_configured_identity(self):
        # GitHub resolves users/OCTOCAT to octocat, but attestation matching is exact.
        adapter, _ = make("github", github_routes(**{"users/OCTOCAT": {"login": "octocat"}}))
        self.assertFalse(adapter.user_exists(None, cfg_for("github"), "OCTOCAT"))

    def test_github_unanswerable_lookup_raises_rather_than_denying(self):
        # 403 is not "absent". Treating it as absent would block a Story on a rate limit.
        adapter, _ = make("github", github_routes(**{
            "users/octocat": (1, {"message": "rate limit", "status": "403"})}))
        with self.assertRaises(forge.ForgeError):
            adapter.user_exists(None, cfg_for("github"), "octocat")

    def test_both_providers_implement_the_lookup(self):
        for cls in (forge.GitLabForge, forge.GitHubForge):
            self.assertIsNot(cls.user_exists, forge.Forge.user_exists, cls.__name__)


class ForgeIdentityCheckTests(unittest.TestCase):
    def test_empty_request_is_verified_trivially(self):
        out = r.forge_identity_check(None, cfg_for("gitlab"), [])
        self.assertTrue(out["verified"])
        self.assertEqual([], out["unknown"])

    def test_missing_forge_block_degrades_without_claiming_absence(self):
        out = r.forge_identity_check(None, {"target_branch": "main"}, ["agungwib"])
        self.assertFalse(out["verified"])
        self.assertEqual([], out["unknown"])
        self.assertTrue(out["reason"])

    def test_unanswerable_lookup_degrades_without_claiming_absence(self):
        def unreachable(_adapter, repo, cfg, name):
            raise forge.ForgeError("host unreachable")
        with patch.object(forge.GitLabForge, "user_exists", unreachable):
            out = r.forge_identity_check(None, cfg_for("gitlab"), ["agungwib"])
        self.assertFalse(out["verified"])
        self.assertEqual([], out["unknown"])
        self.assertIn("host unreachable", out["reason"])

    def test_missing_cli_degrades_rather_than_raising(self):
        cfg = cfg_for("gitlab")
        cfg["forge"]["host"] = "gitlab.example.invalid"
        with patch.object(r, "run", side_effect=FileNotFoundError("glab")):
            out = r.forge_identity_check(None, cfg, ["agungwib"])
        self.assertFalse(out["verified"])

    def test_answers_are_split_into_known_and_unknown(self):
        def lookup(_adapter, repo, cfg, name):
            return name == "agungwib"
        with patch.object(forge.GitLabForge, "user_exists", lookup):
            out = r.forge_identity_check(None, cfg_for("gitlab"), ["agungwib", "ghost", "agungwib"])
        self.assertTrue(out["verified"])
        self.assertEqual(["agungwib"], out["known"])
        self.assertEqual(["ghost"], out["unknown"])


class PrecheckIdentityTests(RepoFixture):
    def precheck(self, contract_data, answers):
        """Run precheck with the forge answering exactly `answers` (name -> bool)."""
        def lookup(_adapter, repo, cfg, name):
            return bool(answers.get(name))
        with patch.object(forge.GitLabForge, "user_exists", lookup):
            return contract.assurance_precheck(self.repo, contract_data)

    def architecture_story(self):
        c = self.make_contract()
        c["story_required_assurance"] = [{
            "gate_id": "architecture_review", "stage": "PRE_MERGE", "mode": "CONFORMANCE",
            "evidence_plan_ref": "backlog/features/FEAT-101.md#architecture"}]
        return c

    def test_known_owner_is_ready_and_marked_verified(self):
        out = self.precheck(self.architecture_story(), {"dev-a-user": True})
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        self.assertEqual([], out["blocking"])
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        self.assertTrue(gate["attestor_readiness"]["forge_verified"])
        self.assertEqual([], gate["attestor_readiness"]["unknown_identities"])
        self.assertTrue(out["forge_identity_check"]["verified"])

    def test_unknown_sole_attestor_blocks_before_any_build_work(self):
        out = self.precheck(self.architecture_story(), {"dev-a-user": False})
        self.assertEqual("CONTRACT_PRECHECK_BLOCKED", out["result"])
        self.assertEqual("BLOCKED_UNSATISFIABLE_ASSURANCE", out["execution_verdict"])
        self.assertEqual(["architecture_review"], [x["gate_id"] for x in out["blocking"]])
        self.assertEqual("NO_RESOLVABLE_ATTESTOR", out["blocking"][0]["reason"])
        self.assertIn("dev-a-user", out["blocking"][0]["candidates"])

    def test_one_resolvable_attestor_among_several_does_not_block(self):
        self.cfg["assurance_policy"]["human_attestors"] = {
            "architecture_review": {"CONFORMANCE": ["arch-a"]}}
        self.write_config()
        out = self.precheck(self.architecture_story(), {"dev-a-user": False, "arch-a": True})
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        # Still reported: the misconfigured identity is visible without stopping work.
        self.assertEqual(["dev-a-user"], gate["attestor_readiness"]["unknown_identities"])

    def test_unreachable_forge_reports_unverified_and_does_not_block(self):
        def unreachable(_adapter, repo, cfg, name):
            raise forge.ForgeError("host unreachable")
        with patch.object(forge.GitLabForge, "user_exists", unreachable):
            out = contract.assurance_precheck(self.repo, self.architecture_story())
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        self.assertEqual([], out["blocking"])
        self.assertFalse(out["forge_identity_check"]["verified"])
        self.assertTrue(out["forge_identity_check"]["notice"])
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        self.assertIs(False, gate["attestor_readiness"]["forge_verified"])
        self.assertEqual([], gate["attestor_readiness"]["unknown_identities"])

    def test_gates_with_no_human_attestor_report_not_applicable(self):
        c = self.make_contract()
        c["story_required_assurance"] = [{
            "gate_id": "traceability_completion", "stage": "POST_INTEGRATION", "mode": "MECHANICAL",
            "evidence_plan_ref": "backlog/features/FEAT-101.md#trace"}]
        out = self.precheck(c, {})
        gate = out["effective_assurance"]["POST_INTEGRATION"][0]
        self.assertIsNone(gate["attestor_readiness"]["forge_verified"])
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])

    def test_existence_never_adds_an_attestor(self):
        # A real forge account that the project never authorized stays out of the
        # candidate list. Existence is not authorization.
        out = self.precheck(self.architecture_story(), {"dev-a-user": True, "someone-else": True})
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        self.assertEqual(["dev-a-user"], gate["attestor_readiness"]["candidates"])
        self.assertNotIn("someone-else", out["forge_identity_check"]["known_identities"])

    def test_blocked_precheck_is_not_ready_for_task_binding(self):
        # Task creation and legacy binding both gate on CONTRACT_PRECHECK_READY, so a
        # blocked result stops the Story before a lane is leased.
        out = self.precheck(self.architecture_story(), {"dev-a-user": False})
        self.assertNotEqual("CONTRACT_PRECHECK_READY", out["result"])
        source = (HERE / "contract.py").read_text(encoding="utf-8")
        self.assertIn("'CONTRACT_PRECHECK_READY'", source)


# ---------------------------------------------------------------------------
# F2 -- the runtime wrapper and the CLI surface
# ---------------------------------------------------------------------------

class CreatePullPublicationTests(RepoFixture):
    def task(self, **over):
        t = dict(TASK)
        t.update(over)
        return t

    def test_unpublished_branch_is_refused_before_any_forge_call(self):
        with patch.object(r, "verify_remote", lambda repo, cfg: None), \
             patch.object(r, "remote_head", lambda repo, remote, branch: None), \
             patch.object(r, "forge_adapter", side_effect=AssertionError("no forge call expected")):
            with self.assertRaises(r.AEError) as ctx:
                r.create_pull(self.repo, self.cfg, self.task(), "t", "")
        self.assertIn("Publish", str(ctx.exception))

    def test_moved_branch_head_is_refused(self):
        with patch.object(r, "verify_remote", lambda repo, cfg: None), \
             patch.object(r, "remote_head", lambda repo, remote, branch: "b" * 40), \
             patch.object(r, "forge_adapter", side_effect=AssertionError("no forge call expected")):
            with self.assertRaises(r.AEError):
                r.create_pull(self.repo, self.cfg, self.task(), "t", "")

    def test_short_candidate_is_refused(self):
        with self.assertRaises(r.AEError):
            r.create_pull(self.repo, self.cfg, self.task(candidate_sha="abc"), "t", "")

    def test_created_request_head_must_equal_the_candidate(self):
        adapter, _ = make("gitlab", gitlab_routes(**{
            GL_LIST: [], GL_CREATE: gl(iid=7),
            "projects/group%2Fproduct/merge_requests/7": gl(iid=7, diff_refs={"head_sha": "c" * 40, "base_sha": "b" * 40}),
        }))
        with patch.object(r, "verify_remote", lambda repo, cfg: None), \
             patch.object(r, "remote_head", lambda repo, remote, branch: "a" * 40), \
             patch.object(r, "forge_adapter", lambda cfg: adapter):
            with self.assertRaises(r.AEError):
                r.create_pull(self.repo, self.cfg, self.task(), "t", "")

    def test_happy_path_returns_the_normalized_request(self):
        adapter, _ = make("gitlab", gitlab_routes(**{GL_LIST: [], GL_CREATE: gl(iid=7)}))
        with patch.object(r, "verify_remote", lambda repo, cfg: None), \
             patch.object(r, "remote_head", lambda repo, remote, branch: "a" * 40), \
             patch.object(r, "forge_adapter", lambda cfg: adapter):
            pull = r.create_pull(self.repo, self.cfg, self.task(), "STORY-101", "body")
        self.assertEqual(7, pull["number"])
        self.assertTrue(pull["created"])


class PullRequestHelperTests(unittest.TestCase):
    def test_helper_is_installed_and_importable(self):
        import pull_request
        self.assertTrue(hasattr(pull_request, "execute"))
        self.assertTrue(hasattr(pull_request, "main"))

    def test_helper_declares_title_and_apply(self):
        source = (HERE / "pull_request.py").read_text(encoding="utf-8")
        for flag in ("--title", "--description-file", "--apply", "--task", "--repo"):
            self.assertIn(flag, source)

    def test_description_file_outside_the_repo_is_refused(self):
        import pull_request
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "body.md"
            outside.write_text("x", encoding="utf-8")
            inside = Path(tmp) / "repo"
            inside.mkdir()
            with self.assertRaises(r.AEError):
                pull_request.description_text(inside, outside)

    def test_absent_description_is_empty_not_derived(self):
        import pull_request
        self.assertEqual("", pull_request.description_text(Path.cwd(), None))


class PrecheckAgainstTheFakeForgeTests(RepoCase):
    """The same check over the full repository fixture and its fake GitLab transport."""

    def owned_contract(self):
        path = contract.registered_contract_path(self.repo, "dev-a", "S1")
        self.ensure_contract("S1")
        data = r.read_json(path)
        data["engineering_impact"]["human_ownership"] = "MATERIAL"
        data["engineering_owner"] = {"owner_ref": "team:dev-a", "attestation_provider": "gitlab",
                                     "attestation_identity": "dev-a-user"}
        data["story_required_assurance"] = [{
            "gate_id": "human_understanding", "stage": "POST_INTEGRATION", "mode": "ASYNC_CAPABILITY",
            "evidence_plan_ref": "backlog/features/FIXTURE.md#ownership"}]
        r.atomic_json(path, data)
        return data

    def test_owner_known_to_the_forge_is_ready(self):
        self.forge_users = {"dev-a-user"}
        out = contract.assurance_precheck(self.repo, self.owned_contract())
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        self.assertTrue(out["forge_identity_check"]["verified"])

    def test_owner_absent_from_the_forge_blocks(self):
        self.forge_users = set()
        out = contract.assurance_precheck(self.repo, self.owned_contract())
        self.assertEqual("CONTRACT_PRECHECK_BLOCKED", out["result"])
        self.assertEqual(["human_understanding"], [x["gate_id"] for x in out["blocking"]])

    def test_identity_lookup_does_not_collide_with_the_merge_request_listing(self):
        # GitLab spells both as a query string. Reading one as the other is exactly how
        # the v0.7 change first broke this fixture.
        self.forge_users = {"dev-a-user"}
        contract.assurance_precheck(self.repo, self.owned_contract())
        looked_up = [e for _, e, _ in self.calls if e.startswith("users?username=")]
        self.assertEqual(["users?username=dev-a-user"], looked_up)


class PullRequestExecuteTests(RepoCase):
    """The helper end to end: real task manifest, real branch, fake GitLab."""

    def published_task(self, story="S1"):
        assigned = self.assign(story)
        workspace = Path(assigned["workspace"])
        (workspace / "src/a.txt").write_text(story + "\n")
        self.g("add", "src/a.txt", repo=workspace)
        self.g("commit", "-m", story, repo=workspace)
        head = self.g("rev-parse", "HEAD", repo=workspace)
        self.g("push", "origin", head + ":refs/heads/work/dev-a/" + story)
        self.release(assigned, head)
        return r.read_json(r.task_path(self.repo, "dev-a", story)), head

    def test_preview_creates_nothing(self):
        import pull_request
        task, head = self.published_task()
        out = pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body")
        self.assertEqual("PULL_REQUEST_PREVIEW", out["result"])
        self.assertEqual(head, out["candidate_sha"])
        self.assertEqual({}, self.mrs)
        self.assertEqual([], [e for m, e, _ in self.calls if m == "POST"])

    def test_apply_creates_one_and_reports_it(self):
        import pull_request
        task, head = self.published_task()
        out = pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body", apply=True)
        self.assertEqual("PULL_REQUEST_OPEN", out["result"])
        self.assertEqual("APPLIED", out["mode"])
        self.assertEqual(1, out["mr_iid"])
        self.assertEqual(head, out["candidate_sha"])
        self.assertEqual("work/dev-a/S1", out["source_branch"])
        self.assertEqual("main", out["target_branch"])
        self.assertIs(False, out["deletes_source_on_merge"])
        self.assertIs(False, out["auto_merge_queued"])
        sent = next(f for m, e, f in self.calls if m == "POST")
        self.assertIs(False, sent["remove_source_branch"])
        self.assertIs(False, sent["squash"])

    def test_second_call_returns_the_same_request(self):
        import pull_request
        task, _ = self.published_task()
        first = pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body", apply=True)
        self.calls.clear()
        second = pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body", apply=True)
        self.assertEqual("ALREADY_OPEN", second["mode"])
        self.assertEqual(first["mr_iid"], second["mr_iid"])
        self.assertEqual([], [e for m, e, _ in self.calls if m == "POST"])
        self.assertEqual(1, len(self.mrs))

    def test_unpublished_branch_refuses(self):
        import pull_request
        task, _ = self.published_task()
        self.g("push", "origin", ":refs/heads/work/dev-a/S1")
        with self.assertRaises(r.AEError):
            pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body", apply=True)
        self.assertEqual({}, self.mrs)

    def test_unapproved_remote_effects_refuse(self):
        import pull_request
        task, _ = self.published_task()
        self.cfg["remote_actions_ready"] = False
        with self.assertRaises(r.AEError):
            pull_request.execute(self.repo, task, self.cfg, "STORY S1", "body", apply=True)


class GuardCreationTests(unittest.TestCase):
    def cfg(self):
        return {
            "schema_version": guard.SCHEMA_VERSION, "kit_version": guard.KIT_VERSION,
            "configuration_approved": True, "remote_actions_ready": True,
            "remote_actions_review_ref": "setup/remote", "target_branch": "main",
            "merge_mode": "AGENT_MERGE",
            "assurance_policy": {"approved": True, "approval_ref": "setup/assurance"},
            "merge_policy": {"approved": True, "approval_ref": "setup/merge"},
        }

    def check(self, command, cfg=None):
        return guard.inspect({"tool_name": "Bash", "cwd": "/repo", "tool_input": {"command": command}},
                             cfg or self.cfg())

    def test_cli_creation_is_denied_and_names_the_helper(self):
        for command in ("glab mr create --source-branch work/dev-a/S1 --target-branch main",
                        "glab mr new --source-branch work/dev-a/S1 --target-branch main",
                        "gh pr create --head work/dev-a/S1 --base main --title t --body b"):
            decision, reason = self.check(command)
            self.assertEqual("deny", decision, command)
            self.assertIn("pull_request.py", reason, command)

    def test_helper_is_recognized_from_the_coordinator_root(self):
        decision, _ = self.check("python tools/agentic/pull_request.py --task t.json --title x --apply")
        self.assertIsNone(decision)

    def test_helper_apply_requires_approved_remote_effects(self):
        cfg = self.cfg(); cfg["remote_actions_ready"] = False
        decision, _ = self.check("python tools/agentic/pull_request.py --task t.json --title x --apply", cfg)
        self.assertEqual("deny", decision)

    def test_helper_preview_does_not_require_remote_approval(self):
        cfg = self.cfg(); cfg["remote_actions_ready"] = False
        decision, _ = self.check("python tools/agentic/pull_request.py --task t.json --title x", cfg)
        self.assertIsNone(decision)

    def test_helper_outside_the_coordinator_root_is_questioned(self):
        decision, _ = self.check("python /elsewhere/pull_request.py --task t.json --title x")
        self.assertEqual("ask", decision)

    def test_reads_stay_allowed(self):
        self.assertIsNone(self.check("glab mr list --state opened")[0])
        self.assertIsNone(self.check("gh pr view 7")[0])


if __name__ == "__main__":
    unittest.main()
