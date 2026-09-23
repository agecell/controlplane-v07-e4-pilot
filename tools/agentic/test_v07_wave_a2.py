#!/usr/bin/env python3
"""Wave A2 tests: stale authority, forge setup validation, controlled abort, lane output.

Four v0.6 pilot findings:

* **F7/A3** -- publication binding proves a contract refers to a state that really was
  published, and Git history is immutable, so that check passes forever. The pilot
  raised FEAT-001 REV-02 -> REV-03 specifically to fix a wrong attestation identity, and
  the contract still bound to REV-02 passed precheck unchanged.
* **F5** -- GitLab turns on "delete source branch when merged" for every new project,
  which merge preflight correctly refuses; nothing said so until the first merge.
* **F8** -- a BUILD lane leased against the wrong base could not be abandoned. Releasing
  recorded the base as the candidate, re-leasing was refused as a duplicate, and
  recovery meant hand-editing kit state.
* **F4** -- a Story that introduces a build directory blocks its own lane release, with a
  message that names neither the paths nor where to declare them.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import forge
import contract
import pool
import ae
import merge
from test_v06_forge import cfg_for, gitlab_routes, github_routes, make, GL_PROJECT, GH_PROJECT
from test_v05_precheck import RepoFixture
from test_v04 import RepoCase


# ---------------------------------------------------------------------------
# F7/A3 -- current authority, not just historical validity
# ---------------------------------------------------------------------------

class MechanicalAuthorityFloorTests(RepoFixture):
    """A3 alone. These fixtures publish no authority pointer, so A2 cannot answer.

    Wave C made that distinction load-bearing: with no pointer the combined verdict is
    UNDETERMINED, never CURRENT, and the floor's own finding is read from `mechanical`.
    A3 may still raise an alarm on its own -- a change it detects yields SUPERSEDED
    through `MECHANICAL_FLOOR` -- but it may never clear one.
    """

    def revise(self, message="revise authority", path="backlog/features/FEAT-101.md",
               text="# FEAT-101\nRevision: REV-04\n"):
        (self.repo / path).write_text(text, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def check(self, data=None):
        return contract.check_authority_currency(self.repo, self.cfg, data or self.make_contract())

    def test_nothing_moved_means_the_floor_detects_nothing(self):
        out = self.check()
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, out["mechanical"]["state"])
        self.assertEqual([], out["mechanical"]["changes"])
        # And that is not proof: without a published pointer nobody has said what is in
        # force, so the combined verdict stays unknown.
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["state"])

    def test_a_detected_change_refuses_even_without_a_pointer(self):
        later = self.revise()
        out = self.check()
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["state"])
        self.assertEqual("MECHANICAL_FLOOR", out["reason"])
        self.assertEqual(contract.MECHANICAL_CHANGED, out["mechanical"]["state"])
        self.assertEqual([later], [c["commit"] for c in out["mechanical"]["changes"]])

    def test_the_refusal_names_the_later_commit_and_the_path(self):
        later = self.revise()
        out = self.check()
        self.assertIn(later[:12], out["detail"])
        self.assertIn("backlog/features/FEAT-101.md", out["detail"])

    def test_a_story_card_edited_without_a_revision_bump_is_detected(self):
        # The point of A3: it asks Git, not the document, so an out-of-band edit that
        # left the revision string alone is still caught.
        later = self.revise(path="backlog/stories/STORY-101.md", text="# STORY-101\nquietly changed\n")
        out = self.check()
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["state"])
        self.assertEqual([later], [c["commit"] for c in out["mechanical"]["changes"]])

    def test_a_commit_touching_nothing_relevant_does_not_trip_the_floor(self):
        (self.repo / "unrelated.txt").write_text("x", encoding="utf-8")
        self.git("add", "unrelated.txt")
        self.git("commit", "-m", "unrelated")
        out = self.check()
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, out["mechanical"]["state"])
        self.assertNotEqual(contract.AUTHORITY_SUPERSEDED, out["state"])

    def test_publication_not_reachable_from_the_reference_is_undetermined(self):
        # The v0.6 pilot's real shape: the revision was committed on the records branch
        # and never merged to the target, so the target cannot answer the question.
        self.git("checkout", "-q", "-b", "side")
        later = self.revise()
        data = self.make_contract()
        data["publication"]["canonical_backlog"]["publication_sha"] = later
        data["publication"]["canonical_backlog"]["blob_sha"] = self.git(
            "rev-parse", f"{later}:backlog/features/FEAT-101.md")
        out = self.check(data)
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["state"])
        self.assertEqual("PUBLICATION_NOT_ON_REFERENCE", out["mechanical"]["reason"])
        self.assertIn("main", out["detail"])

    def test_missing_reference_is_undetermined_not_current(self):
        cfg = dict(self.cfg, target_branch="does-not-exist")
        out = contract.check_authority_currency(self.repo, cfg, self.make_contract())
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["state"])
        self.assertEqual("NO_AUTHORITY_REFERENCE", out["reason"])

    def test_the_floor_has_no_vocabulary_for_current(self):
        # It can raise an alarm and never clear one, so it must not be able to say the
        # word at all. Only the published pointer may.
        for state in (contract.MECHANICAL_NO_CHANGE, contract.MECHANICAL_CHANGED,
                      contract.MECHANICAL_UNDETERMINED):
            self.assertNotEqual(contract.AUTHORITY_CURRENT, state)

    def test_remote_tracking_reference_is_preferred_over_the_local_branch(self):
        head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", head)
        later = self.revise()
        out = self.check()
        # The local branch moved, the remote-tracking ref did not; the answer must come
        # from what the team sees, and must say which ref it used.
        self.assertEqual("refs/remotes/origin/main", out["reference"])
        self.assertEqual(head, out["reference_sha"])
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, out["mechanical"]["state"])
        self.assertNotIn(later, [c["commit"] for c in out["mechanical"]["changes"]])


class PrecheckAuthorityTests(RepoFixture):
    def test_superseded_authority_blocks_new_execution(self):
        (self.repo / "backlog/features/FEAT-101.md").write_text("# FEAT-101\nRevision: REV-04\n", encoding="utf-8")
        self.git("add", "backlog/features/FEAT-101.md")
        self.git("commit", "-m", "REV-04")
        def lookup(_adapter, repo, cfg, name):
            return True
        with patch.object(forge.GitLabForge, "user_exists", lookup):
            out = contract.assurance_precheck(self.repo, self.make_contract())
        self.assertEqual("CONTRACT_PRECHECK_BLOCKED", out["result"])
        self.assertEqual("BLOCKED_UNSATISFIABLE_ASSURANCE", out["execution_verdict"])
        self.assertIn("AUTHORITY_SUPERSEDED", [b["reason"] for b in out["blocking"]])
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["authority_currency"]["state"])

    def test_unanswerable_authority_reports_and_does_not_block(self):
        def lookup(_adapter, repo, cfg, name):
            return True
        with patch.object(forge.GitLabForge, "user_exists", lookup):
            out = contract.assurance_precheck(self.repo, self.make_contract())
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        # No Feature card pointer in this fixture, so the answer is unknown -- reported,
        # and deliberately not blocking.
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["authority_currency"]["state"])
        self.assertEqual([], out["blocking"])

    def test_identity_and_authority_blocks_are_both_reported(self):
        (self.repo / "backlog/stories/STORY-101.md").write_text("# STORY-101\nchanged\n", encoding="utf-8")
        self.git("add", "backlog/stories/STORY-101.md")
        self.git("commit", "-m", "edit")
        c = self.make_contract()
        c["story_required_assurance"] = [{
            "gate_id": "human_understanding", "stage": "POST_INTEGRATION", "mode": "ASYNC_CAPABILITY",
            "evidence_plan_ref": "backlog/features/FEAT-101.md#ownership"}]
        def lookup(_adapter, repo, cfg, name):
            return False
        with patch.object(forge.GitLabForge, "user_exists", lookup):
            out = contract.assurance_precheck(self.repo, c)
        reasons = {b["reason"] for b in out["blocking"]}
        self.assertEqual({"NO_RESOLVABLE_ATTESTOR", "AUTHORITY_SUPERSEDED"}, reasons)


class HistoricalRecordsAreNotReJudgedTests(RepoCase):
    def test_traceability_written_under_an_earlier_revision_still_validates(self):
        # F7's non-negotiable: current-authority checking gates new execution only.
        task = self.build()
        result = merge.execute(self.repo, task, self.cfg, True)
        self.assertEqual("INTEGRATED", result["result"])
        record_ref = result["traceability"]["record_ref"]
        (self.repo / "backlog/features/FIXTURE.md").write_text("# FIXTURE\nREV-99\n", encoding="utf-8")
        self.g("add", "backlog/features/FIXTURE.md")
        self.g("commit", "-m", "revise authority after integration")
        record = r.read_json(self.repo / record_ref)
        r.validate_traceability_index(record)  # must not raise
        self.assertEqual("FIXTURE-1", record["authority"]["canonical_backlog"]["revision"])


# ---------------------------------------------------------------------------
# F5 -- the forge's own source-branch-deletion default, read only
# ---------------------------------------------------------------------------

class SourceBranchPolicyTests(unittest.TestCase):
    def test_both_providers_surface_the_flag(self):
        for provider, routes, key in (
            ("gitlab", gitlab_routes(**{"projects/group%2Fproduct": dict(GL_PROJECT, remove_source_branch_after_merge=True)}), "gitlab"),
            ("github", github_routes(**{"repos/owner/product": dict(GH_PROJECT, delete_branch_on_merge=True)}), "github"),
        ):
            adapter, _ = make(provider, routes)
            self.assertIs(True, adapter.project(None, cfg_for(provider))["deletes_source_branch"], provider)

    def test_absent_flag_reads_as_false_not_unknown(self):
        # Neither provider omits the field on a real project; a missing one must not
        # silently become "true" and block, nor become "unknown" and hide.
        for provider, routes in (("gitlab", gitlab_routes()), ("github", github_routes())):
            adapter, _ = make(provider, routes)
            self.assertIs(False, adapter.project(None, cfg_for(provider))["deletes_source_branch"], provider)

    def test_the_setting_is_named_for_each_provider(self):
        self.assertIn("remove_source_branch_after_merge", ae.SOURCE_BRANCH_SETTING["gitlab"])
        self.assertIn("delete_branch_on_merge", ae.SOURCE_BRANCH_SETTING["github"])


class DoctorSourceBranchTests(RepoCase):
    def project_with(self, flag):
        def api(_adapter, repo, cfg, endpoint, method="GET", fields=None):
            return {"id": 1, "path_with_namespace": cfg["forge"]["repo"], "merge_method": "merge",
                    "remove_source_branch_after_merge": flag}
        return patch.object(forge.GitLabForge, "api", api)

    def test_doctor_reports_the_flag_and_lists_a_gap_when_true(self):
        with self.project_with(True):
            out = ae.doctor(self.repo)
        self.assertIs(True, out["forge_deletes_source_branch"])
        self.assertIn("forge_deletes_source_branch", out["configuration_gaps"])
        self.assertIn("remove_source_branch_after_merge", out["forge_source_branch_check"]["setting"])

    def test_doctor_reports_false_without_a_gap(self):
        with self.project_with(False):
            out = ae.doctor(self.repo)
        self.assertIs(False, out["forge_deletes_source_branch"])
        self.assertNotIn("forge_deletes_source_branch", out["configuration_gaps"])

    def test_doctor_reports_unknown_and_still_succeeds_without_a_forge(self):
        def unreachable(_adapter, repo, cfg, endpoint, method="GET", fields=None):
            raise forge.ForgeError("host unreachable")
        with patch.object(forge.GitLabForge, "api", unreachable):
            out = ae.doctor(self.repo)
        self.assertIsNone(out["forge_deletes_source_branch"])
        self.assertFalse(out["forge_source_branch_check"]["checked"])
        self.assertIn("unreachable", out["forge_source_branch_check"]["reason"])
        self.assertNotIn("forge_deletes_source_branch", out["configuration_gaps"])

    def test_merge_refusal_names_the_setting_and_both_steps(self):
        task = self.build()
        self.mrs[task["mr_iid"]]["force_remove_source_branch"] = True
        with self.assertRaises(r.AEError) as ctx:
            merge.execute(self.repo, task, self.cfg, True)
        message = str(ctx.exception)
        self.assertIn("remove_source_branch_after_merge", message)
        self.assertIn("already open", message)
        self.assertEqual(0, self.put_count)


# ---------------------------------------------------------------------------
# F8 -- an invalid start can be discarded, and only an invalid one
# ---------------------------------------------------------------------------

class EmptyReleaseTests(RepoCase):
    def test_release_without_commits_records_no_candidate(self):
        assigned = self.assign("S1")
        out = self.release(assigned, self.base)
        self.assertEqual("NOT_RECORDED", out["candidate"])
        task = r.read_json(r.task_path(self.repo, "dev-a", "S1"))
        self.assertIsNone(task["candidate_sha"])
        self.assertEqual("BUILD_PREPARED", task["state"])
        self.assertIn("abort", out["notice"])

    def test_fix_without_commits_keeps_the_candidate_it_started_from(self):
        # "Nothing was committed" means two different things depending on whether a
        # candidate already exists. Reporting NOT_RECORDED for a FIX would be a lie.
        assigned = self.assign("S1")
        path = Path(assigned["workspace"])
        (path / "src/a.txt").write_text("work\n")
        self.g("add", "src/a.txt", repo=path)
        self.g("commit", "-m", "S1", repo=path)
        head = self.g("rev-parse", "HEAD", repo=path)
        self.release(assigned, head)
        fix = self.assign("S1", lane="lane-02", role="FIX", sha=head, invocation="fix-S1")
        out = self.release(fix, head)
        self.assertEqual("UNCHANGED", out["candidate"])
        task = r.read_json(r.task_path(self.repo, "dev-a", "S1"))
        self.assertEqual(head, task["candidate_sha"])
        self.assertEqual("CANDIDATE_SAVED", task["state"])

    def test_release_with_commits_still_records_the_candidate(self):
        assigned = self.assign("S1")
        path = Path(assigned["workspace"])
        (path / "src/a.txt").write_text("work\n")
        self.g("add", "src/a.txt", repo=path)
        self.g("commit", "-m", "S1", repo=path)
        head = self.g("rev-parse", "HEAD", repo=path)
        self.release(assigned, head)
        task = r.read_json(r.task_path(self.repo, "dev-a", "S1"))
        self.assertEqual(head, task["candidate_sha"])
        self.assertEqual("CANDIDATE_SAVED", task["state"])


class AbortTests(RepoCase):
    def started(self, story="S1"):
        assigned = self.assign(story)
        return assigned, assigned["lease"]["token"]

    def abort(self, story="S1", lane="lane-01", token=None, apply=True, reason="records/abort-S1"):
        return pool.abort(self.repo, "dev-a", story, lane, token, reason, apply)

    def test_abort_succeeds_on_a_clean_invalid_start(self):
        _, token = self.started()
        out = self.abort(token=token)
        self.assertEqual("STORY_START_ABORTED", out["result"])
        self.assertEqual("DELETED", out["branch"])
        self.assertFalse(r.task_path(self.repo, "dev-a", "S1").exists())
        self.assertIsNone(r.local_ref(self.repo, "refs/heads/work/dev-a/S1"))
        self.assertEqual("PRESERVED", out["folder"])

    def test_preview_changes_nothing(self):
        _, token = self.started()
        out = self.abort(token=token, apply=False)
        self.assertEqual("ABORT_READY", out["result"])
        self.assertTrue(r.task_path(self.repo, "dev-a", "S1").exists())
        self.assertEqual(self.base, r.local_ref(self.repo, "refs/heads/work/dev-a/S1"))

    def test_abort_is_recorded_with_its_reason(self):
        _, token = self.started()
        out = self.abort(token=token, reason="records/dev-a/S1/abort.md")
        record = r.read_json(self.repo / out["record_ref"])
        self.assertEqual("STORY_START_ABORTED", record["result"])
        self.assertEqual("records/dev-a/S1/abort.md", record["reason_ref"])
        self.assertEqual(self.base, record["base_sha"])

    def test_abort_requires_a_reason(self):
        _, token = self.started()
        with self.assertRaises(r.AEError):
            self.abort(token=token, reason="")

    def test_re_lease_after_abort_succeeds(self):
        _, token = self.started()
        self.abort(token=token)
        again = self.assign("S1", lane="lane-02")
        self.assertEqual("LANE_PREPARED", again["result"])
        self.assertEqual(self.base, r.local_ref(self.repo, "refs/heads/work/dev-a/S1"))

    def test_abort_refused_when_a_commit_exists(self):
        assigned, token = self.started()
        path = Path(assigned["workspace"])
        (path / "src/a.txt").write_text("real work\n")
        self.g("add", "src/a.txt", repo=path)
        self.g("commit", "-m", "real", repo=path)
        with self.assertRaises(r.AEError) as ctx:
            self.abort(token=token)
        self.assertIn("real work", str(ctx.exception))
        self.assertTrue(r.task_path(self.repo, "dev-a", "S1").exists())

    def test_abort_refused_when_the_branch_was_published(self):
        _, token = self.started()
        self.g("push", "origin", self.base + ":refs/heads/work/dev-a/S1")
        with self.assertRaises(r.AEError) as ctx:
            self.abort(token=token)
        self.assertIn("forge", str(ctx.exception))
        self.assertTrue(r.task_path(self.repo, "dev-a", "S1").exists())

    def test_abort_refused_when_a_pull_request_is_recorded(self):
        _, token = self.started()
        path = r.task_path(self.repo, "dev-a", "S1")
        task = r.read_json(path)
        task["mr_iid"] = 7
        r.atomic_json(path, task)
        with self.assertRaises(r.AEError) as ctx:
            self.abort(token=token)
        self.assertIn("pull request", str(ctx.exception))

    def test_abort_refused_when_assurance_was_closed(self):
        _, token = self.started()
        path = r.task_path(self.repo, "dev-a", "S1")
        task = r.read_json(path)
        task["assurance"]["pre_merge"] = {"architecture_review": {"status": "SATISFIED"}}
        r.atomic_json(path, task)
        with self.assertRaises(r.AEError) as ctx:
            self.abort(token=token)
        self.assertIn("architecture_review", str(ctx.exception))
        self.assertTrue(r.task_path(self.repo, "dev-a", "S1").exists())

    def test_abort_refused_when_a_traceability_record_exists(self):
        _, token = self.started()
        path = r.task_path(self.repo, "dev-a", "S1")
        task = r.read_json(path)
        task["traceability"]["record_ref"] = "docs/agentic/records/x.json"
        r.atomic_json(path, task)
        with self.assertRaises(r.AEError):
            self.abort(token=token)

    def test_abort_refused_with_a_mismatched_token(self):
        self.started()
        with self.assertRaises(r.AEError):
            self.abort(token="0" * 32)

    def test_abort_refused_for_a_reader_lane(self):
        assigned = self.assign("S1")
        self.release(assigned, self.base)
        reader = self.assign("S1", lane="lane-02", role="QA", invocation="qa-S1")
        with self.assertRaises(r.AEError) as ctx:
            self.abort(lane="lane-02", token=reader["lease"]["token"])
        self.assertIn("writing lane", str(ctx.exception))

    def test_abort_survives_an_unreachable_forge(self):
        # Condition 2 already proves the branch was never published, so a request cannot
        # exist; an unreachable forge must not make recovery impossible.
        _, token = self.started()
        def unreachable(_adapter, repo, cfg, endpoint, method="GET", fields=None):
            raise forge.ForgeError("host unreachable")
        with patch.object(forge.GitLabForge, "api", unreachable):
            out = self.abort(token=token)
        self.assertEqual("STORY_START_ABORTED", out["result"])
        self.assertFalse(out["checks"]["forge_confirmed_no_open_request"])

    def test_abort_refused_when_an_open_request_exists_on_the_forge(self):
        assigned, token = self.started()
        self.mrs[1] = {"iid": 1, "state": "opened", "source_branch": "work/dev-a/S1",
                       "target_branch": "main"}
        with self.assertRaises(r.AEError) as ctx:
            self.abort(token=token)
        self.assertIn("open pull request", str(ctx.exception))


# ---------------------------------------------------------------------------
# F4 -- a lane's output, and whose problem it is
# ---------------------------------------------------------------------------

class LaneOutputTests(RepoCase):
    def lane_with(self, entries, patterns=None, tracked=()):
        """Create ignored entries in a lane. `patterns` are the .gitignore lines.

        Both other arguments matter, because `git ls-files --others --ignored --directory`
        reports the shallowest *untracked* directory. Ignoring `tools` reports `tools/`
        rather than the `__pycache__` beneath it; and even ignoring `__pycache__/` reports
        `tools/` when nothing under `tools/` is tracked. In a real repository the kit's
        own files are tracked, which is why `tracked` seeds them here.
        """
        assigned = self.assign("S1")
        path = Path(assigned["workspace"])
        if patterns is None:
            patterns = sorted({e.split("/")[0] for e in entries})
        (path / ".gitignore").write_text("\n".join(patterns) + "\n")
        self.g("add", ".gitignore", repo=path)
        for keep in tracked:
            target = path / keep
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("tracked\n")
            self.g("add", keep, repo=path)
        self.g("commit", "-m", "ignore", repo=path)
        for entry in entries:
            target = path / entry
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        return assigned, path

    def test_undeclared_directory_still_blocks_and_is_named(self):
        _, path = self.lane_with(["build/out.o"])
        with self.assertRaises(r.AEError) as ctx:
            pool.clean_for_switch(path, self.cfg)
        message = str(ctx.exception)
        self.assertIn("build/", message)
        self.assertIn("pool.reusable_ignored_directories", message)
        self.assertIn("Nothing was deleted", message)

    def test_declared_directory_releases_cleanly(self):
        _, path = self.lane_with(["build/out.o"])
        self.cfg["pool"]["reusable_ignored_directories"] = ["build/"]
        pool.clean_for_switch(path, self.cfg)  # must not raise

    def test_kit_pycache_alone_does_not_block(self):
        # The pilot's exact case: running a helper from inside a lane leaves
        # tools/agentic/__pycache__/ beside the kit's own tracked files.
        _, path = self.lane_with(["tools/agentic/__pycache__/runtime.pyc"],
                                 patterns=["__pycache__/"], tracked=["tools/agentic/runtime.py"])
        pool.clean_for_switch(path, self.cfg)  # must not raise

    def test_a_wholly_ignored_parent_directory_still_blocks(self):
        # Git reports the shallowest ignored directory. `tools/` is not a kit artifact
        # even when the only thing under it happens to be one, and is not waved through.
        _, path = self.lane_with(["tools/agentic/__pycache__/runtime.pyc"], patterns=["tools"])
        with self.assertRaises(r.AEError) as ctx:
            pool.clean_for_switch(path, self.cfg)
        self.assertIn("tools/", str(ctx.exception))

    def test_kit_pycache_does_not_excuse_a_user_directory(self):
        _, path = self.lane_with(["tools/agentic/__pycache__/runtime.pyc", "build/out.o"],
                                 patterns=["__pycache__/", "build/"],
                                 tracked=["tools/agentic/runtime.py"])
        with self.assertRaises(r.AEError) as ctx:
            pool.clean_for_switch(path, self.cfg)
        message = str(ctx.exception)
        self.assertIn("build/", message)
        self.assertNotIn("__pycache__", message)

    def test_the_built_in_set_is_fixed_and_not_configurable(self):
        # Anything a project could extend would be the weakening the refusal prevents.
        self.assertIsInstance(pool.KIT_GENERATED_DIRECTORIES, tuple)
        self.assertEqual(("__pycache__",), pool.KIT_GENERATED_DIRECTORIES)
        source = (HERE / "pool.py").read_text(encoding="utf-8")
        self.assertNotIn("cfg.get('kit_generated", source)
        self.assertNotIn("KIT_GENERATED_DIRECTORIES +", source)

    def test_kit_generated_matches_at_any_depth_only_by_directory_name(self):
        self.assertTrue(pool.kit_generated("__pycache__/"))
        self.assertTrue(pool.kit_generated("tools/agentic/__pycache__/x.pyc"))
        self.assertFalse(pool.kit_generated("my__pycache__cache/x"))
        self.assertFalse(pool.kit_generated("build/"))


if __name__ == "__main__":
    unittest.main()
