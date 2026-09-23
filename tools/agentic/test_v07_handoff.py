#!/usr/bin/env python3
"""Wave B tests: the shared handoff interface and deterministic contract generation.

F1 from the v0.6 pilot. Authoring a normalized contract from a Story card took three
human judgements per Story — incompatible reference formats, four required fields with no
source, and disagreeing types — and nothing checked the result until much later.

The fixture card below is the pilot's own STORY-001 bp-meta, reduced but structurally
faithful: schema 1, `"Tier 1"`, a single `traceability_root` string, `§` references, and
no `dependency_ref`, `build_start_ref` or `final_acceptance_ref`.
"""
from __future__ import annotations

import json
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


LEGACY_META = {
    "artifact_type": "story",
    "artifact_schema_version": 1,
    "story_id": "STORY-101",
    "feature_id": "FEAT-101",
    "canonical_backlog_path": "backlog/features/FEAT-101.md",
    "canonical_revision": "REV-03",
    "traceability_root": "REQ-11",
    "requirement_refs": ["REQ-11", "REQ-06"],
    "ac_ids": ["AC-ONE", "AC-TWO"],
    "scope_ref": "STORY-101§Scope, Exclusions & Preserve",
    "exclusions_ref": "STORY-101§Scope, Exclusions & Preserve (Non-Scope)",
    "preserve_ref": "STORY-101§Scope, Exclusions & Preserve (Preserve)",
    "dependencies": [],
    "risk_tier": "Tier 1",
    "engineering_impact": {k: "ROUTINE" for k in contract.IMPACT_KEYS},
    "engineering_owner": None,
    "required_assurance": [],
    "evidence_expectations": [],
    "stop_conditions": [],
    "merge_override": None,
    "capability_id": "FEAT-101",
    "preparation_state": "BUILD READY",
    "implementation_state": "IMPLEMENTATION NOT STARTED",
}

MISSING_IN_SCHEMA_1 = ["build_start_ref", "dependency_ref", "final_acceptance_ref"]

OVERRIDES = {
    "dependency_ref": "backlog/stories/STORY-101.md#dependencies",
    "build_start_ref": "backlog/features/FEAT-101.md#build-start-planning",
    "final_acceptance_ref": "backlog/features/FEAT-101.md#final-capability-acceptance",
}


def modern_meta(**over):
    """The same Story, stated in schema 2: nothing to translate, nothing missing."""
    meta = {
        "artifact_type": "story",
        "artifact_schema_version": handoff.BP_META_SCHEMA_VERSION,
        "handoff_schema_version": handoff.HANDOFF_SCHEMA_VERSION,
        "story_id": "STORY-101",
        "feature_id": "FEAT-101",
        "capability_id": "FEAT-101",
        "canonical_backlog_path": "backlog/features/FEAT-101.md",
        "story_card_path": "backlog/stories/STORY-101.md",
        "canonical_revision": "REV-03",
        "traceability_root": ["REQ-11"],
        "ac_ids": ["AC-ONE", "AC-TWO"],
        "risk_tier": 1,
        "scope_ref": "backlog/stories/STORY-101.md#scope-exclusions-preserve",
        "preserve_ref": "backlog/stories/STORY-101.md#preserve",
        "dependency_ref": "backlog/stories/STORY-101.md#dependencies",
        "build_start_ref": "backlog/features/FEAT-101.md#build-start-planning",
        "final_acceptance_ref": "backlog/features/FEAT-101.md#final-capability-acceptance",
        "engineering_impact": {k: "ROUTINE" for k in contract.IMPACT_KEYS},
        "engineering_owner": None,
        "required_assurance": [],
        "merge_override": None,
        "preparation_state": "BUILD READY",
        "evidence_expectations": [
            {"ac_id": "AC-ONE", "kind": "build_log", "method": "captured build output",
             "stage": "BUILD", "owner": "story_builder", "initial_status": "NOT_RUN"},
        ],
    }
    meta.update(over)
    return meta


def card(meta, body="\n## Outcome\n\nSomething.\n"):
    return "# STORY-101 — fixture\n\n```bp-meta\n" + json.dumps(meta, indent=2) + "\n```\n" + body


class BpMetaReadingTests(unittest.TestCase):
    def test_reads_the_single_block(self):
        self.assertEqual(LEGACY_META, handoff.read_bp_meta(card(LEGACY_META)))

    def test_absent_block_is_refused(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.read_bp_meta("# STORY-101\n\nNo metadata here.\n")

    def test_two_blocks_are_refused(self):
        text = card(LEGACY_META) + "\n```bp-meta\n{}\n```\n"
        with self.assertRaises(handoff.HandoffError) as ctx:
            handoff.read_bp_meta(text)
        self.assertIn("exactly one", str(ctx.exception))

    def test_malformed_json_names_the_problem(self):
        with self.assertRaises(handoff.HandoffError) as ctx:
            handoff.read_bp_meta("```bp-meta\n{not json}\n```")
        self.assertIn("not valid JSON", str(ctx.exception))


class SlugTests(unittest.TestCase):
    def test_the_pilot_heading(self):
        self.assertEqual("scope-exclusions-preserve", handoff.slugify("Scope, Exclusions & Preserve"))

    def test_punctuation_collapses_to_single_hyphens(self):
        self.assertEqual("build-start-planning", handoff.slugify("Build-Start  /  Planning"))

    def test_a_heading_with_no_alphanumerics_is_refused(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.slugify("§ — §")


class SchemaTwoTests(unittest.TestCase):
    def test_a_schema_2_card_needs_no_translation(self):
        handoff.validate_handoff(modern_meta())  # must not raise

    def test_every_reference_field_satisfies_contract_ref_id(self):
        # The exact incompatibility F1 exists to remove.
        meta = modern_meta()
        for field in handoff.REFERENCE_FIELDS:
            self.assertRegex(meta[field], contract.REF_ID)

    def test_a_missing_required_field_is_named(self):
        meta = modern_meta()
        del meta["build_start_ref"]
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_handoff(meta)
        self.assertIn("build_start_ref", str(ctx.exception))

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_handoff(modern_meta(scop_ref="typo"))
        self.assertIn("scop_ref", str(ctx.exception))

    def test_informational_fields_are_accepted(self):
        handoff.validate_handoff(modern_meta(requirement_refs=["REQ-1"], stop_conditions=["x"],
                                             dependencies=[], evidence_expectations=[],
                                             exclusions_ref="a#b", implementation_state="NOT STARTED"))

    def test_a_space_in_a_reference_is_refused(self):
        with self.assertRaises(r.AEError):
            handoff.validate_handoff(modern_meta(scope_ref="STORY-101§Scope, Exclusions & Preserve"))

    def test_tier_must_be_an_integer_in_schema_2(self):
        with self.assertRaises(r.AEError):
            handoff.validate_handoff(modern_meta(risk_tier="Tier 1"))

    def test_traceability_root_must_be_a_list_in_schema_2(self):
        with self.assertRaises(r.AEError):
            handoff.validate_handoff(modern_meta(traceability_root="REQ-11"))

    def test_an_obligation_without_an_evidence_plan_ref_is_refused(self):
        meta = modern_meta(required_assurance=[
            {"gate_id": "architecture_review", "stage": "PRE_MERGE", "mode": "CONFORMANCE"}])
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_handoff(meta)
        self.assertIn("evidence_plan_ref", str(ctx.exception))

    def test_a_schema_1_card_is_refused_by_the_strict_validator(self):
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_handoff(LEGACY_META)
        self.assertIn("schema", str(ctx.exception))

    def test_a_story_card_authority_pointer_is_accepted_but_not_the_authority(self):
        # Wave B reserved the slot on the Story card; Wave C gave it meaning, and put
        # the authoritative one on the *Feature* card. A Story cannot vouch for what is
        # currently in force -- a Story bound to a superseded revision is exactly the
        # condition being detected. One on a Story card is accepted so nothing emitted
        # between the waves breaks, and it is not read.
        handoff.validate_handoff(modern_meta(authority_pointer={
            "feature_id": "FEAT-101", "current_revision": "REV-03"}))
        source = (HERE / "contract.py").read_text(encoding="utf-8")
        self.assertIn("read_authority_pointer_at", source)
        self.assertNotIn('story_card"]["authority_pointer"', source)

    def test_a_non_object_authority_pointer_is_refused(self):
        with self.assertRaises(r.AEError):
            handoff.validate_handoff(modern_meta(authority_pointer="REV-03"))

    def test_the_authority_pointer_shape_is_validated(self):
        for bad in ({}, {"feature_id": "FEAT-101"}, {"current_revision": "REV-03"},
                    {"feature_id": "FEAT-101", "current_revision": "REV-03", "nonsense": 1},
                    {"feature_id": "FEAT-101", "current_revision": "REV-03", "revision_published_at": "abc"},
                    {"feature_id": "FEAT-101", "current_revision": "REV-03", "superseded_revisions": "REV-02"}):
            with self.assertRaises(r.AEError, msg=repr(bad)):
                handoff.validate_authority_pointer(bad)

    def test_a_minimal_authority_pointer_is_enough(self):
        handoff.validate_authority_pointer({"feature_id": "FEAT-101", "current_revision": "REV-03"})
        handoff.validate_authority_pointer({"feature_id": "FEAT-101", "current_revision": "REV-03",
                                            "revision_published_at": "a" * 40,
                                            "superseded_revisions": ["REV-02"]})


class LegacyUpgradeTests(unittest.TestCase):
    def upgrade(self, meta=None, overrides=None):
        return handoff.upgrade_legacy_meta(meta or LEGACY_META,
                                           story_path="backlog/stories/STORY-101.md",
                                           overrides=overrides)

    def test_tier_string_becomes_an_integer(self):
        meta, warnings, _ = self.upgrade()
        self.assertEqual(1, meta["risk_tier"])
        self.assertTrue(any("risk_tier" in w for w in warnings))

    def test_single_traceability_root_becomes_a_list(self):
        meta, warnings, _ = self.upgrade()
        self.assertEqual(["REQ-11"], meta["traceability_root"])
        self.assertTrue(any("traceability_root" in w for w in warnings))

    def test_section_references_are_translated(self):
        meta, warnings, _ = self.upgrade()
        self.assertEqual("backlog/stories/STORY-101.md#scope-exclusions-preserve", meta["scope_ref"])
        self.assertTrue(any("mechanical slug" in w for w in warnings))

    def test_fields_with_no_source_are_reported_never_invented(self):
        _, _, missing = self.upgrade()
        self.assertEqual(MISSING_IN_SCHEMA_1, missing)

    def test_overrides_satisfy_the_missing_fields(self):
        meta, warnings, missing = self.upgrade(overrides=dict(OVERRIDES))
        self.assertEqual([], missing)
        self.assertEqual(OVERRIDES["build_start_ref"], meta["build_start_ref"])
        self.assertTrue(any("override" in w for w in warnings))

    def test_an_obligation_missing_its_evidence_plan_ref_is_reported(self):
        meta = dict(LEGACY_META, required_assurance=[
            {"gate_id": "architecture_review", "stage": "PRE_MERGE", "mode": "CONFORMANCE"}])
        _, _, missing = self.upgrade(meta)
        self.assertIn("required_assurance[0].evidence_plan_ref", missing)

    def test_an_upgraded_card_then_passes_the_strict_validator(self):
        meta, _, missing = self.upgrade(overrides=dict(OVERRIDES))
        self.assertEqual([], missing)
        handoff.validate_handoff(meta)  # must not raise

    def test_an_override_naming_no_field_is_refused(self):
        # Silently dropping it would report the field missing while the operator believes
        # they supplied it, with nothing connecting the two.
        with self.assertRaises(r.AEError) as ctx:
            self.upgrade(overrides={"build_start_re": "a#b"})
        self.assertIn("build_start_re", str(ctx.exception))

    def test_an_obligation_override_index_is_honoured(self):
        meta = dict(LEGACY_META, required_assurance=[
            {"gate_id": "architecture_review", "stage": "PRE_MERGE", "mode": "CONFORMANCE"}])
        overrides = dict(OVERRIDES)
        overrides["required_assurance[0].evidence_plan_ref"] = "backlog/stories/STORY-101.md#required-assurance"
        upgraded, _, missing = self.upgrade(meta, overrides=overrides)
        self.assertEqual([], missing)
        self.assertEqual("backlog/stories/STORY-101.md#required-assurance",
                         upgraded["required_assurance"][0]["evidence_plan_ref"])
        handoff.validate_handoff(upgraded)

    def test_a_tier_that_is_not_a_tier_is_refused(self):
        with self.assertRaises(handoff.HandoffError):
            self.upgrade(dict(LEGACY_META, risk_tier="high"))

    def test_a_reference_in_neither_form_reaches_the_validator_and_is_refused(self):
        # The upgrade translates only what it recognizes; it does not second-guess the
        # rest. One place judges whether a reference is usable, and it is the validator.
        meta, _, missing = self.upgrade(dict(LEGACY_META, scope_ref="has spaces but no marker"),
                                        overrides=dict(OVERRIDES))
        self.assertEqual([], missing)
        self.assertEqual("has spaces but no marker", meta["scope_ref"])
        with self.assertRaises(r.AEError) as ctx:
            handoff.validate_handoff(meta)
        self.assertIn("scope_ref", str(ctx.exception))


class RepoFixture(unittest.TestCase):
    """A real git repository with a Story card, so publication binding is real."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "backlog/features").mkdir(parents=True)
        (self.repo / "backlog/stories").mkdir(parents=True)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "backlog/features/FEAT-101.md").write_text("# FEAT-101\nRevision: REV-03\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def publish(self, meta, message="publish"):
        (self.repo / "backlog/stories/STORY-101.md").write_text(card(meta), encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def generate(self, sha, **kw):
        kw.setdefault("developer", "dev-a")
        kw.setdefault("sprint", "sprint-01")
        return handoff.generate_contract(self.repo, "backlog/stories/STORY-101.md",
                                         publication_sha=sha, **kw)


class GenerationTests(RepoFixture):
    def test_a_schema_2_card_generates_a_valid_contract(self):
        sha = self.publish(modern_meta())
        data = self.generate(sha)
        contract.validate_contract(data)  # must not raise
        self.assertEqual("STORY-101", data["story_id"])
        self.assertEqual(1, data["risk_tier"])
        self.assertEqual(["REQ-11"], data["traceability_root"])
        self.assertEqual("REV-03", data["publication"]["canonical_backlog"]["revision"])

    def test_generation_is_deterministic(self):
        sha = self.publish(modern_meta())
        first, second = self.generate(sha), self.generate(sha)
        self.assertEqual(first, second)
        self.assertEqual(contract.contract_digest(first), contract.contract_digest(second))

    def test_assignment_is_the_only_input_not_from_the_card_or_git(self):
        sha = self.publish(modern_meta())
        a = self.generate(sha)
        b = self.generate(sha, developer="dev-b", sprint="sprint-02")
        self.assertEqual({"assigned_developer", "sprint"},
                         {k for k in a if a[k] != b[k]})

    def test_blob_shas_come_from_the_publication_commit(self):
        sha = self.publish(modern_meta())
        data = self.generate(sha)
        self.assertEqual(self.git("rev-parse", f"{sha}:backlog/stories/STORY-101.md"),
                         data["publication"]["story_card"]["blob_sha"])
        self.assertEqual(self.git("rev-parse", f"{sha}:backlog/features/FEAT-101.md"),
                         data["publication"]["canonical_backlog"]["blob_sha"])

    def test_metadata_is_read_as_published_not_from_the_working_tree(self):
        # The defect this caught against the pilot's own backlog: the working tree
        # carried a later revision than the commit the contract binds to, so a
        # working-tree read produced a contract claiming a revision that commit does not
        # contain.
        sha = self.publish(modern_meta(canonical_revision="REV-02"))
        (self.repo / "backlog/stories/STORY-101.md").write_text(
            card(modern_meta(canonical_revision="REV-99")), encoding="utf-8")
        data = self.generate(sha)
        self.assertEqual("REV-02", data["publication"]["canonical_backlog"]["revision"])

    def test_a_card_absent_at_the_publication_commit_is_refused(self):
        sha = self.publish(modern_meta())
        self.git("rm", "-q", "backlog/stories/STORY-101.md")
        self.git("commit", "-m", "remove")
        later = self.git("rev-parse", "HEAD")
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.generate(later)
        self.assertIn("does not exist at publication commit", str(ctx.exception))

    def test_a_legacy_card_generates_once_the_missing_refs_are_supplied(self):
        sha = self.publish(LEGACY_META)
        data = self.generate(sha, overrides=dict(OVERRIDES))
        contract.validate_contract(data)
        self.assertEqual(OVERRIDES["build_start_ref"], data["build_start_ref"])
        self.assertEqual("backlog/stories/STORY-101.md#scope-exclusions-preserve", data["scope_ref"])

    def test_a_legacy_card_without_overrides_is_refused_with_the_field_names(self):
        sha = self.publish(LEGACY_META)
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.generate(sha)
        message = str(ctx.exception)
        for field in MISSING_IN_SCHEMA_1:
            self.assertIn(field, message)

    def test_round_trip_schema_2_card_to_valid_contract(self):
        sha = self.publish(modern_meta(risk_tier=3, engineering_owner={
            "owner_ref": "team:dev-a", "attestation_provider": "gitlab",
            "attestation_identity": "dev-a-user"}, required_assurance=[
            {"gate_id": "architecture_review", "stage": "PRE_MERGE", "mode": "CONFORMANCE",
             "evidence_plan_ref": "backlog/stories/STORY-101.md#required-assurance"}]))
        data = self.generate(sha)
        contract.validate_contract(data)
        self.assertEqual(3, data["risk_tier"])
        self.assertEqual("dev-a-user", data["engineering_owner"]["attestation_identity"])
        self.assertEqual(1, len(data["story_required_assurance"]))

    def test_the_reserved_pointer_does_not_reach_the_contract(self):
        sha = self.publish(modern_meta(authority_pointer={"feature_revision": "REV-03"}))
        data = self.generate(sha)
        self.assertNotIn("authority_pointer", json.dumps(data))
        contract.validate_contract(data)


class HandoffCheckTests(RepoFixture):
    def check(self, **kw):
        return handoff.handoff_check(self.repo, "backlog/stories/STORY-101.md", **kw)

    def test_a_ready_card_reports_ready(self):
        self.publish(modern_meta())
        out = self.check()
        self.assertEqual("HANDOFF_READY", out["result"])
        self.assertEqual([], out["warnings"])
        self.assertEqual("working_tree", out["source"])

    def test_a_legacy_card_reports_what_is_missing_before_handoff(self):
        self.publish(LEGACY_META)
        out = self.check()
        self.assertEqual("HANDOFF_INCOMPLETE", out["result"])
        self.assertEqual(MISSING_IN_SCHEMA_1, out["missing"])
        self.assertTrue(any("schema 1" in w for w in out["warnings"]))

    def test_a_legacy_card_with_overrides_reports_ready(self):
        self.publish(LEGACY_META)
        out = self.check(overrides=dict(OVERRIDES))
        self.assertEqual("HANDOFF_READY", out["result"])
        self.assertEqual([], out["missing"])

    def test_an_invalid_card_reports_rather_than_raising(self):
        self.publish(modern_meta(engineering_impact={"architecture": "ROUTINE"}))
        out = self.check()
        self.assertEqual("HANDOFF_INVALID", out["result"])
        self.assertIn("engineering_impact", out["error"])

    def test_checking_at_a_commit_reads_the_published_card(self):
        sha = self.publish(modern_meta(canonical_revision="REV-02"))
        (self.repo / "backlog/stories/STORY-101.md").write_text(
            card(modern_meta(canonical_revision="REV-99")), encoding="utf-8")
        self.assertEqual("REV-99", self.check()["canonical_revision"])
        self.assertEqual("REV-02", self.check(publication_sha=sha)["canonical_revision"])


class RegistrationTests(RepoFixture):
    def register(self, sha, **kw):
        kw.setdefault("developer", "dev-a")
        kw.setdefault("sprint", "sprint-01")
        return contract.handoff_generate_file(self.repo, "backlog/stories/STORY-101.md",
                                              publication_sha=sha, **kw)

    def test_preview_writes_nothing(self):
        sha = self.publish(modern_meta())
        out = self.register(sha)
        self.assertEqual("CONTRACT_PREVIEW", out["result"])
        self.assertFalse((self.repo / out["contract_ref"]).exists())

    def test_apply_registers_at_the_expected_path(self):
        sha = self.publish(modern_meta())
        out = self.register(sha, apply=True)
        self.assertEqual("CONTRACT_REGISTERED", out["result"])
        path = contract.registered_contract_path(self.repo, "dev-a", "STORY-101")
        self.assertTrue(path.exists())
        contract.validate_contract(r.read_json(path))

    def test_regenerating_the_same_contract_reports_current(self):
        sha = self.publish(modern_meta())
        self.register(sha, apply=True)
        out = self.register(sha, apply=True)
        self.assertEqual("CONTRACT_CURRENT", out["result"])

    def test_a_different_contract_is_refused_and_names_the_fields(self):
        sha = self.publish(modern_meta())
        self.register(sha, apply=True)
        later = self.publish(modern_meta(canonical_revision="REV-04"), message="revise")
        out = self.register(later, apply=True)
        self.assertEqual("CONTRACT_DIFFERS", out["result"])
        self.assertEqual("REFUSED", out["mode"])
        self.assertIn("publication", out["differing_fields"])
        self.assertEqual("REV-03", out["differences"]["publication"]["registered"]["canonical_backlog"]["revision"])

    def test_a_registered_contract_passes_the_kit_loader(self):
        sha = self.publish(modern_meta())
        self.register(sha, apply=True)
        path, data = contract.load_registered_contract(self.repo, "dev-a", "STORY-101")
        self.assertEqual("STORY-101", data["story_id"])
        self.assertTrue(path.exists())


class InterfaceVersionTests(unittest.TestCase):
    def test_the_interface_version_is_independent_of_both_packages(self):
        self.assertEqual(1, handoff.HANDOFF_SCHEMA_VERSION)
        self.assertEqual(2, handoff.BP_META_SCHEMA_VERSION)
        self.assertEqual(2, contract.CONTRACT_SCHEMA_VERSION)

    def test_a_card_declaring_another_interface_version_is_refused(self):
        with self.assertRaises(r.AEError):
            handoff.validate_handoff(modern_meta(handoff_schema_version=2))

    def test_the_interface_is_documented_where_both_sides_can_read_it(self):
        spec = HERE.parents[1] / "docs/agentic/HANDOFF.md"
        self.assertTrue(spec.is_file())
        text = spec.read_text(encoding="utf-8")
        for field in handoff.REFERENCE_FIELDS:
            self.assertIn(field, text)
        self.assertIn("authority_pointer", text)


if __name__ == "__main__":
    unittest.main()
