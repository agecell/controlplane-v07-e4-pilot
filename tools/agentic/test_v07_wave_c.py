#!/usr/bin/env python3
"""Wave C tests: derived required checks (F3/C2) and current authority (F7/A2).

Two v0.6 pilot findings, and both were resolved in the pilot by weakening something:

* **F3.** `required_checks` was one project-wide list demanded of every Story whether or
  not that Story could produce the evidence. STORY-002 could not satisfy `focused_tests`,
  so it was deleted from the project policy -- weakening it for **every** Story rather
  than for the one that could not satisfy it.
* **F7.** A contract bound to FEAT-001 REV-02 kept passing precheck after REV-03
  corrected the very identity error that prompted the revision. Wave A2's mechanical
  floor did not catch it, because the floor is anchored to the target branch and the
  pilot's backlog authority was not on the target branch.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import contract
import handoff


def policy(mandatory=("build",), default=(), mapping=None):
    return {"approved": True, "approval_ref": "setup/merge", "human_review_required": False,
            "human_approvers": [], "mandatory_checks": list(mandatory),
            "default_checks": list(default), "check_evidence_kinds": dict(mapping or {}),
            "remote_lock_enabled": True, "lock_branch": "ae/locks/integration"}


def expectation(ac_id, kind):
    return {"ac_id": ac_id, "kind": kind, "method": "documented", "stage": "BUILD",
            "owner": "story_builder", "initial_status": "NOT_RUN"}


# ---------------------------------------------------------------------------
# F3/C2 -- which checks this Story must actually satisfy
# ---------------------------------------------------------------------------

class MergePolicyShapeTests(unittest.TestCase):
    def test_a_valid_policy_is_accepted(self):
        contract.validate_merge_policy_checks(policy(("build",), ("focused_tests",),
                                                     {"focused_tests": ["automated_test"]}))

    def test_a_check_cannot_be_both_mandatory_and_derived(self):
        with self.assertRaises(r.AEError) as ctx:
            contract.validate_merge_policy_checks(policy(("build",), ("build",)))
        self.assertIn("never both", str(ctx.exception))

    def test_repeated_names_are_refused(self):
        with self.assertRaises(r.AEError):
            contract.validate_merge_policy_checks(policy(("build", "build")))

    def test_a_v06_policy_is_refused(self):
        old = policy()
        old["required_checks"] = old.pop("mandatory_checks")
        with self.assertRaises(r.AEError):
            contract.validate_merge_policy_checks(old)

    def test_an_empty_evidence_kind_list_is_refused(self):
        with self.assertRaises(r.AEError):
            contract.validate_merge_policy_checks(policy(("build",), ("x",), {"x": []}))


class DerivedCheckTests(unittest.TestCase):
    def resolve(self, cfg_policy, expectations):
        return contract.effective_required_checks(
            {"merge_policy": cfg_policy}, {"evidence_expectations": list(expectations)})

    def test_mandatory_checks_apply_without_consulting_the_story(self):
        out = self.resolve(policy(("build", "scope_check")), [])
        self.assertEqual(["build", "scope_check"], out["required"])
        for item in out["resolution"]:
            self.assertEqual("PROJECT_MANDATORY", item["reason"])
            self.assertEqual("project", item["source"])

    def test_a_story_cannot_remove_a_mandatory_check(self):
        # Whatever the Story's evidence says, mandatory stays mandatory.
        out = self.resolve(policy(("focused_tests",)), [expectation("AC-1", "manual_inspection")])
        self.assertIn("focused_tests", out["required"])

    def test_a_story_cannot_add_a_check(self):
        out = self.resolve(policy(("build",)), [expectation("AC-1", "automated_test")])
        self.assertEqual(["build"], out["required"])

    def test_a_derived_check_applies_when_the_evidence_matches(self):
        out = self.resolve(policy(("build",), ("focused_tests",), {"focused_tests": ["automated_test"]}),
                           [expectation("AC-1", "automated_test")])
        self.assertEqual(["build", "focused_tests"], out["required"])
        derived = next(x for x in out["resolution"] if x["check"] == "focused_tests")
        self.assertEqual("EVIDENCE_EXPECTED", derived["reason"])
        self.assertEqual(["AC-1"], derived["ac_ids"])
        self.assertEqual(["automated_test"], derived["evidence_kinds"])

    def test_the_pilot_case_a_derived_check_is_not_applicable(self):
        # STORY-002: no automated-test evidence. v0.6 could only resolve this by deleting
        # focused_tests from the project policy, for every Story.
        out = self.resolve(policy(("build",), ("focused_tests",), {"focused_tests": ["automated_test"]}),
                           [expectation("AC-1", "compile_check"), expectation("AC-2", "export_table")])
        self.assertEqual(["build"], out["required"])
        self.assertEqual(["focused_tests"], out["not_applicable"])
        derived = next(x for x in out["resolution"] if x["check"] == "focused_tests")
        self.assertEqual("NO_MATCHING_EVIDENCE", derived["reason"])
        self.assertIn("automated_test", derived["detail"])

    def test_an_unmapped_default_check_is_a_configuration_gap_not_a_skip(self):
        out = self.resolve(policy(("build",), ("perf_budget",)), [expectation("AC-1", "automated_test")])
        self.assertEqual(["perf_budget"], out["configuration_gaps"])
        self.assertNotIn("perf_budget", out["required"])
        self.assertNotIn("perf_budget", out["not_applicable"])
        gap = next(x for x in out["resolution"] if x["check"] == "perf_budget")
        self.assertEqual("NO_EVIDENCE_KIND_MAPPING", gap["reason"])
        self.assertIn("not skipped", gap["detail"])
        self.assertTrue(out["notice"])

    def test_several_kinds_may_apply_one_check(self):
        out = self.resolve(policy((), ("focused_tests",),
                                  {"focused_tests": ["automated_test", "integration_test"]}),
                           [expectation("AC-1", "integration_test")])
        self.assertEqual(["focused_tests"], out["required"])

    def test_resolution_explains_every_check_exactly_once(self):
        out = self.resolve(policy(("build",), ("focused_tests", "perf_budget"),
                                  {"focused_tests": ["automated_test"]}),
                           [expectation("AC-1", "automated_test")])
        self.assertEqual(["build", "focused_tests", "perf_budget"],
                         sorted(x["check"] for x in out["resolution"]))
        for item in out["resolution"]:
            self.assertIn(item["status"], ("APPLIED", "NOT_APPLICABLE", "CONFIGURATION_GAP"))
            self.assertTrue(item["detail"])


class EvidenceExpectationSchemaTests(unittest.TestCase):
    def test_a_valid_expectation_is_accepted(self):
        handoff.validate_evidence_expectations([expectation("AC-1", "automated_test")], ["AC-1"])

    def test_an_expectation_for_an_unknown_ac_is_refused(self):
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_evidence_expectations([expectation("AC-9", "automated_test")], ["AC-1"])
        self.assertIn("AC-9", str(ctx.exception))

    def test_kind_must_be_a_lowercase_identifier(self):
        for kind in ("Automated Test", "AUTOMATED", "", "1test", None):
            with self.assertRaises(r.AEError, msg=repr(kind)):
                handoff.validate_evidence_expectations([expectation("AC-1", kind)], ["AC-1"])

    def test_stage_owner_and_status_come_from_fixed_vocabularies(self):
        for field, bad in (("stage", "DEPLOY"), ("owner", "somebody"), ("initial_status", "PASS")):
            item = expectation("AC-1", "automated_test")
            item[field] = bad
            with self.assertRaises(r.AEError, msg=field):
                handoff.validate_evidence_expectations([item], ["AC-1"])

    def test_an_unknown_field_is_refused(self):
        item = expectation("AC-1", "automated_test")
        item["extra"] = 1
        with self.assertRaises(r.AEError):
            handoff.validate_evidence_expectations([item], ["AC-1"])


# ---------------------------------------------------------------------------
# F7/A2 -- current authority
# ---------------------------------------------------------------------------

class AuthorityFixture(unittest.TestCase):
    """A repository whose backlog lives on a branch that is not the target branch.

    That is the pilot's real shape, and the reason the mechanical floor alone could not
    answer the question.
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "backlog/features").mkdir(parents=True)
        (self.repo / "backlog/stories").mkdir(parents=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write_feature("REV-02", pointer=None)
        (self.repo / "backlog/stories/STORY-101.md").write_text("# STORY-101\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "REV-02")
        self.rev02 = self.git("rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def write_feature(self, revision, pointer):
        meta = {"artifact_type": "feature", "feature_id": "FEAT-101", "revision": revision}
        if pointer is not None:
            meta["authority_pointer"] = pointer
        (self.repo / "backlog/features/FEAT-101.md").write_text(
            "# FEAT-101\n\n```bp-meta\n" + json.dumps(meta, indent=2) + "\n```\n", encoding="utf-8")

    def publish(self, revision, pointer, message="revise"):
        self.write_feature(revision, pointer)
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def contract_at(self, sha, revision):
        return {
            "publication": {
                "canonical_backlog": {"path": "backlog/features/FEAT-101.md", "revision": revision,
                                      "publication_sha": sha,
                                      "blob_sha": self.git("rev-parse", f"{sha}:backlog/features/FEAT-101.md")},
                "story_card": {"path": "backlog/stories/STORY-101.md", "publication_sha": sha,
                               "blob_sha": self.git("rev-parse", f"{sha}:backlog/stories/STORY-101.md")},
            }
        }

    def cfg(self, **over):
        out = {"remote": "origin", "target_branch": "main", "backlog_authority_ref": None}
        out.update(over)
        return out

    def check(self, data, **over):
        return contract.check_authority_currency(self.repo, self.cfg(**over), data)


class AuthorityPointerTests(AuthorityFixture):
    def test_a_contract_on_the_current_revision_passes(self):
        self.publish("REV-02", {"feature_id": "FEAT-101", "current_revision": "REV-02"}, "add pointer")
        # Re-bind to the commit that carries the pointer, so nothing has moved since.
        sha = self.git("rev-parse", "HEAD")
        out = self.check(self.contract_at(sha, "REV-02"))
        self.assertEqual(contract.AUTHORITY_CURRENT, out["state"])
        self.assertEqual(contract.AUTHORITY_CURRENT, out["pointer"]["state"])
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, out["mechanical"]["state"])

    def test_the_pilot_case_a_superseded_revision_is_refused(self):
        data = self.contract_at(self.rev02, "REV-02")
        self.publish("REV-03", {"feature_id": "FEAT-101", "current_revision": "REV-03",
                                "superseded_revisions": ["REV-02"]})
        out = self.check(data)
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["state"])
        self.assertEqual("REVISION_SUPERSEDED", out["reason"])
        self.assertIn("REV-03", out["detail"])

    def test_a_revision_the_feature_never_heard_of_is_distinguished(self):
        data = self.contract_at(self.rev02, "REV-02")
        self.publish("REV-03", {"feature_id": "FEAT-101", "current_revision": "REV-03"})
        out = self.check(data)
        self.assertEqual("REVISION_UNKNOWN_TO_FEATURE", out["reason"])

    def test_an_edit_without_a_revision_bump_is_refused(self):
        data = self.contract_at(self.rev02, "REV-02")
        self.publish("REV-02", {"feature_id": "FEAT-101", "current_revision": "REV-02"}, "quiet edit")
        out = self.check(data)
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["state"])
        self.assertEqual("AUTHORITY_CHANGED_WITHOUT_REVISION", out["reason"])
        self.assertEqual(contract.AUTHORITY_CURRENT, out["pointer"]["state"])
        self.assertEqual(contract.MECHANICAL_CHANGED, out["mechanical"]["state"])

    def test_no_pointer_is_undetermined_never_current(self):
        out = self.check(self.contract_at(self.rev02, "REV-02"))
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["state"])
        self.assertEqual("NO_AUTHORITY_POINTER", out["reason"])
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, out["mechanical"]["state"])

    def test_no_pointer_plus_a_mechanical_change_still_refuses(self):
        # The floor may raise an alarm even where the pointer cannot be read.
        data = self.contract_at(self.rev02, "REV-02")
        self.publish("REV-03", None)
        out = self.check(data)
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, out["state"])
        self.assertEqual("MECHANICAL_FLOOR", out["reason"])

    def test_the_mechanical_floor_never_says_current(self):
        states = {contract.MECHANICAL_NO_CHANGE, contract.MECHANICAL_CHANGED,
                  contract.MECHANICAL_UNDETERMINED}
        self.assertNotIn(contract.AUTHORITY_CURRENT, states)

    def test_an_absent_feature_card_is_undetermined(self):
        data = self.contract_at(self.rev02, "REV-02")
        self.git("rm", "-q", "backlog/features/FEAT-101.md")
        self.git("commit", "-m", "remove")
        out = self.check(data)
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["pointer"]["state"])
        self.assertEqual("AUTHORITY_SOURCE_ABSENT", out["pointer"]["reason"])

    def test_no_resolvable_reference_is_undetermined(self):
        out = self.check(self.contract_at(self.rev02, "REV-02"), target_branch="does-not-exist")
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, out["state"])
        self.assertEqual("NO_AUTHORITY_REFERENCE", out["reason"])
        self.assertIsNone(out["reference"])


class AuthorityReferenceTests(AuthorityFixture):
    def test_the_declared_reference_is_preferred_over_the_target_branch(self):
        # The pilot's shape: the revision that supersedes lives on a records branch that
        # was never merged into the target, so the target cannot answer the question.
        data = self.contract_at(self.rev02, "REV-02")
        self.git("checkout", "-q", "-b", "ae/records/dev-a/sprint-01")
        self.publish("REV-03", {"feature_id": "FEAT-101", "current_revision": "REV-03",
                                "superseded_revisions": ["REV-02"]})
        self.git("checkout", "-q", "main")

        anchored_to_target = self.check(data)
        self.assertEqual(contract.AUTHORITY_UNDETERMINED, anchored_to_target["state"])
        self.assertEqual(contract.MECHANICAL_NO_CHANGE, anchored_to_target["mechanical"]["state"])

        anchored_to_backlog = self.check(data, backlog_authority_ref="refs/heads/ae/records/dev-a/sprint-01")
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, anchored_to_backlog["state"])
        self.assertEqual("backlog_authority_ref", anchored_to_backlog["reference_source"])

    def test_the_answer_always_names_the_source_it_used(self):
        out = self.check(self.contract_at(self.rev02, "REV-02"))
        self.assertEqual("refs/heads/main", out["reference"])
        self.assertEqual("target_branch", out["reference_source"])


class HistoricalRecordsSurviveTests(AuthorityFixture):
    def test_publication_binding_still_validates_after_the_revision_moves(self):
        # Historical validity and current authority answer different questions. Moving
        # the revision must not make a completed Story's binding stop being true.
        data = self.contract_at(self.rev02, "REV-02")
        blob = data["publication"]["canonical_backlog"]["blob_sha"]
        self.publish("REV-03", {"feature_id": "FEAT-101", "current_revision": "REV-03"})
        self.assertEqual(blob, contract._git_blob_at(self.repo, self.rev02,
                                                     "backlog/features/FEAT-101.md"))
        self.assertEqual(contract.AUTHORITY_SUPERSEDED, self.check(data)["state"])

    def test_the_check_reads_no_traceability_record(self):
        # It gates new execution. It has no business touching finished work.
        source = (HERE / "contract.py").read_text(encoding="utf-8")
        start = source.index("def check_authority_currency(")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        # The docstring names traceability in order to say it is untouched. The code
        # below it must not mention it at all.
        body = body[body.index('"""', body.index('"""') + 3) + 3:]
        self.assertNotIn("traceability", body)


class UpgradeMigrationTests(unittest.TestCase):
    def test_required_checks_all_become_mandatory(self):
        out = {"merge_policy": {"approved": True, "required_checks": ["focused_tests", "build"],
                                "lock_branch": "ae/locks/integration"}}
        contract.split_required_checks(out)
        policy_after = out["merge_policy"]
        self.assertEqual(["focused_tests", "build"], policy_after["mandatory_checks"])
        self.assertEqual([], policy_after["default_checks"])
        self.assertEqual({}, policy_after["check_evidence_kinds"])
        self.assertNotIn("required_checks", policy_after)
        self.assertEqual("ae/locks/integration", policy_after["lock_branch"])

    def test_an_upgrade_never_reduces_enforcement(self):
        out = {"merge_policy": {"required_checks": ["a", "b", "c"]}}
        contract.split_required_checks(out)
        self.assertEqual(["a", "b", "c"], out["merge_policy"]["mandatory_checks"])

    def test_the_upgrade_script_carries_the_same_rule(self):
        # It cannot import contract.py -- it runs before the code it installs -- so the
        # rule is stated twice and must not drift.
        source = (HERE.parents[2] / "upgrade_v06_to_v07.py").read_text(encoding="utf-8")
        self.assertIn('new_policy["mandatory_checks"] = list(required)', source)
        self.assertIn('new_policy["default_checks"] = []', source)
        self.assertIn("evidence_expectations", source)

    def test_the_upgrade_baseline_carries_both_digests(self):
        # Git rewrites line endings on checkout wherever core.autocrlf is on, so a
        # byte-exact-only baseline would refuse a file nobody edited.
        lines = (HERE.parents[2] / "V06_BASELINE.sha256").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        for line in lines:
            raw, normalized, rel = line.split(None, 2)
            self.assertRegex(raw, r"\A[0-9a-f]{64}\Z")
            self.assertRegex(normalized, r"\A[0-9a-f]{64}\Z")
            self.assertTrue(rel.strip())


if __name__ == "__main__":
    unittest.main()
