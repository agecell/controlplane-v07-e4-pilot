#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import contract
import ae


def base_project_config() -> dict:
    cfg = {
        "schema_version": 5,
        "kit_version": "0.7",
        "project_name": "Fixture",
        "remote": "origin",
        "target_branch": "main",
        "merge_mode": "AGENT_MERGE",
        "merge_method": "merge_commit",
        "max_local_builders": 1,
        "max_repo_builders": 2,
        "max_shared_high_risk_writers": 1,
        "remote_actions_ready": True,
        "remote_actions_review_ref": "setup/remote",
        "human_review_policy": "fixture",
        "team_coordination_ref": "team/board",
        "ci_policy": "source SHA",
        "commands": {},
        "configuration_approved": True,
        "language": "id",
        "forge": {"provider": "gitlab", "host": "gitlab.example.invalid", "repo": "group/product"},
        "cleanup": {"mode":"SAFE_MERGED","approved":True,"approval_ref":"setup/cleanup","protected_branches":["main"],"triggers":["after_merge_verified"],"scope":"registered_owner_story_only"},
        "ci_required": True,
        "pool": {"model":"REUSABLE_LANE_POOL","size":7,"root":".claude/worktrees","reusable_ignored_directories":[]},
        "merge_policy": {"approved":True,"approval_ref":"setup/merge","human_review_required":False,"human_approvers":[],"mandatory_checks":["focused_tests","build","scope_check"],"default_checks":[],"check_evidence_kinds":{},"remote_lock_enabled":True,"lock_branch":"ae/locks/integration"},
        "assurance_policy": contract.default_assurance_policy(),
    }
    cfg["assurance_policy"]["approved"] = True
    cfg["assurance_policy"]["approval_ref"] = "setup/assurance"
    return cfg


class RepoFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-b", "main"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "fixture@example.invalid"], check=True)
        (self.repo / ".agentic").mkdir()
        (self.repo / "backlog/features").mkdir(parents=True)
        (self.repo / "backlog/stories").mkdir(parents=True)
        (self.repo / "backlog/features/FEAT-101.md").write_text("# FEAT-101\nRevision: REV-03\n", encoding="utf-8")
        (self.repo / "backlog/stories/STORY-101.md").write_text("# STORY-101\n", encoding="utf-8")
        self.cfg = base_project_config()
        self.write_config()
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "fixture authority"], check=True, capture_output=True, text=True)
        self.pub = self.git("rev-parse", "HEAD")
        self.backlog_blob = self.git("rev-parse", f"{self.pub}:backlog/features/FEAT-101.md")
        self.story_blob = self.git("rev-parse", f"{self.pub}:backlog/stories/STORY-101.md")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def write_config(self):
        (self.repo / ".agentic/project.json").write_text(json.dumps(self.cfg), encoding="utf-8")

    def make_contract(self, *, material: bool = True) -> dict:
        impact = {k: "ROUTINE" for k in contract.IMPACT_KEYS}
        owner = None
        if material:
            impact["security_privacy_data"] = "MATERIAL"
            impact["human_ownership"] = "MATERIAL"
            owner = {"owner_ref":"team:dev-a","attestation_provider":"gitlab","attestation_identity":"dev-a-user"}
        return {
            "contract_schema_version": 2,
        "evidence_expectations": [],
            "story_id": "STORY-101",
            "assigned_developer": "dev-a",
            "sprint": "sprint-01",
            "risk_tier": 3 if material else 1,
            "publication": {
                "canonical_backlog": {"path":"backlog/features/FEAT-101.md","revision":"REV-03","publication_sha":self.pub,"blob_sha":self.backlog_blob},
                "story_card": {"path":"backlog/stories/STORY-101.md","publication_sha":self.pub,"blob_sha":self.story_blob},
            },
            "traceability_root": ["REQ-101"],
            "ac_ids": ["AC-FUNC-01"],
            "scope_ref": "backlog/features/FEAT-101.md#scope",
            "preserve_ref": "backlog/features/FEAT-101.md#preserve",
            "dependency_ref": "backlog/features/FEAT-101.md#dependency",
            "engineering_owner": owner,
            "engineering_impact": impact,
            "story_required_assurance": [],
            "merge_mode_override": None,
            "build_start_ref": "backlog/features/FEAT-101.md#build-start",
            "final_acceptance_ref": "backlog/features/FEAT-101.md#final-acceptance",
            "capability_id": "FEAT-101",
        }


class EffectiveAssuranceTests(unittest.TestCase):
    def policy(self):
        p = contract.default_assurance_policy(); p["approved"] = True; p["approval_ref"] = "setup/assurance"; return p

    def test_union_project_tier_story(self):
        p = self.policy()
        p["project_required_gates"] = [{"gate_id":"traceability_completion","stage":"POST_INTEGRATION","mode":None}]
        p["tier_required_gates"]["3"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL"}]
        story = [{"gate_id":"human_understanding","stage":"POST_INTEGRATION","mode":"ASYNC_CAPABILITY","evidence_plan_ref":"backlog/features/F.md#assurance"}]
        out = contract.resolve_effective_assurance(p, 3, story)
        self.assertEqual({"traceability_completion","security_review","human_understanding"}, {x["gate_id"] for x in out})

    def test_story_cannot_weaken_project_security_mode(self):
        p = self.policy()
        p["project_required_gates"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"CRITICAL_OR_MAJOR"}]
        story = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL","evidence_plan_ref":"backlog/features/F.md#assurance"}]
        out = contract.resolve_effective_assurance(p, 1, story)
        self.assertEqual("CRITICAL_OR_MAJOR", out[0]["mode"])
        self.assertEqual(["project","story"], out[0]["required_sources"])

    def test_story_can_strengthen_project_security_mode(self):
        p = self.policy()
        p["project_required_gates"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL"}]
        story = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"CRITICAL_OR_MAJOR","evidence_plan_ref":"backlog/features/F.md#assurance"}]
        out = contract.resolve_effective_assurance(p, 1, story)
        self.assertEqual("CRITICAL_OR_MAJOR", out[0]["mode"])

    def test_same_gate_different_stage_is_preserved(self):
        p = self.policy()
        p["project_required_gates"] = [{"gate_id":"nfr_performance","stage":"PRE_MERGE","mode":"OBJECTIVE_TOOL"}]
        story = [{"gate_id":"nfr_performance","stage":"POST_INTEGRATION","mode":"COMPOSITE","evidence_plan_ref":"backlog/features/F.md#nfr"}]
        out = contract.resolve_effective_assurance(p, 1, story)
        self.assertEqual(2, len(out))
        self.assertEqual(["PRE_MERGE","POST_INTEGRATION"], [x["stage"] for x in out])

    def test_unknown_story_custom_gate_rejected(self):
        p = self.policy()
        story = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"CHECK","evidence_plan_ref":"backlog/features/F.md#data"}]
        with self.assertRaises(r.AEError): contract.resolve_effective_assurance(p, 1, story)

    def test_configured_custom_gate_is_retained(self):
        p = self.policy()
        p["custom_gates"] = {"project_defined:data_retention":{"policy_ref":"docs/policies/data.md#retention"}}
        story = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"CHECK","evidence_plan_ref":"backlog/features/F.md#data"}]
        out = contract.resolve_effective_assurance(p, 1, story)
        self.assertEqual("project_defined:data_retention", out[0]["gate_id"])

    def test_conflicting_custom_modes_rejected(self):
        p = self.policy()
        p["custom_gates"] = {"project_defined:data_retention":{"policy_ref":"docs/policies/data.md#retention"}}
        p["project_required_gates"] = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"A"}]
        story = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"B","evidence_plan_ref":"backlog/features/F.md#data"}]
        with self.assertRaises(r.AEError): contract.resolve_effective_assurance(p, 1, story)


class AssurancePrecheckTests(RepoFixture):
    def test_fast_path_ready_has_empty_registry(self):
        c = self.make_contract(material=False)
        out = contract.assurance_precheck(self.repo, c, developer="dev-a", sprint="sprint-01")
        self.assertEqual("CONTRACT_PRECHECK_READY", out["result"])
        self.assertEqual([], out["effective_assurance"]["PRE_MERGE"])
        self.assertEqual([], out["effective_assurance"]["POST_INTEGRATION"])
        self.assertEqual("PENDING_EXISTING_RUNTIME_PREFLIGHT", out["execution_verdict"])

    def test_publication_blob_mismatch_rejected(self):
        c = self.make_contract(); c["publication"]["story_card"]["blob_sha"] = "b" * 40
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_publication_commit_missing_rejected(self):
        c = self.make_contract(); c["publication"]["canonical_backlog"]["publication_sha"] = "c" * 40; c["publication"]["story_card"]["publication_sha"] = "c" * 40
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_developer_mismatch_rejected(self):
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, self.make_contract(), developer="dev-b")

    def test_sprint_mismatch_rejected(self):
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, self.make_contract(), sprint="sprint-02")

    def test_unapproved_assurance_policy_blocks(self):
        self.cfg["assurance_policy"]["approved"] = False
        self.cfg["assurance_policy"]["approval_ref"] = "__CONFIGURE__"
        self.write_config()
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, self.make_contract())

    def test_material_impact_requires_engineering_owner(self):
        c = self.make_contract(); c["engineering_owner"] = None
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_project_gate_is_inherited(self):
        self.cfg["assurance_policy"]["project_required_gates"] = [{"gate_id":"traceability_completion","stage":"POST_INTEGRATION","mode":None}]
        self.write_config()
        out = contract.assurance_precheck(self.repo, self.make_contract())
        self.assertEqual("traceability_completion", out["effective_assurance"]["POST_INTEGRATION"][0]["gate_id"])

    def test_tier_gate_is_inherited(self):
        self.cfg["assurance_policy"]["tier_required_gates"]["3"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL"}]
        self.cfg["assurance_policy"]["human_attestors"] = {"security_review":{"BOUNDED_MATERIAL":["security-champion-a"]}}
        self.write_config()
        out = contract.assurance_precheck(self.repo, self.make_contract())
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        self.assertEqual("security_review", gate["gate_id"])
        self.assertEqual(["security-champion-a"], gate["attestor_readiness"]["candidates"])

    def test_bounded_security_without_attestor_blocks(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL","evidence_plan_ref":"backlog/features/FEAT-101.md#assurance"}]
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_critical_security_requires_critical_allowlist(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"CRITICAL_OR_MAJOR","evidence_plan_ref":"backlog/features/FEAT-101.md#assurance"}]
        self.cfg["assurance_policy"]["human_attestors"] = {"security_review":{"BOUNDED_MATERIAL":["security-champion-a"]}}
        self.write_config()
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_architecture_conformance_can_use_engineering_owner(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"architecture_review","stage":"PRE_MERGE","mode":"CONFORMANCE","evidence_plan_ref":"backlog/features/FEAT-101.md#architecture"}]
        out = contract.assurance_precheck(self.repo, c)
        gate = out["effective_assurance"]["PRE_MERGE"][0]
        self.assertIn("dev-a-user", gate["attestor_readiness"]["candidates"])

    def test_architecture_decision_requires_authorized_attestor(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"architecture_review","stage":"PRE_MERGE","mode":"DECISION","evidence_plan_ref":"backlog/features/FEAT-101.md#architecture"}]
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_human_understanding_uses_engineering_owner(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"human_understanding","stage":"POST_INTEGRATION","mode":"ASYNC_CAPABILITY","evidence_plan_ref":"backlog/features/FEAT-101.md#ownership"}]
        out = contract.assurance_precheck(self.repo, c)
        gate = out["effective_assurance"]["POST_INTEGRATION"][0]
        self.assertEqual("ENGINEERING_OWNER", gate["attestor_readiness"]["kind"])

    def test_operability_can_use_engineering_owner(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"operability_recovery","stage":"POST_INTEGRATION","mode":"CAPABILITY","evidence_plan_ref":"backlog/features/FEAT-101.md#ops"}]
        out = contract.assurance_precheck(self.repo, c)
        self.assertEqual("ENGINEERING_OWNER_OR_AUTHORIZED_HUMAN", out["effective_assurance"]["POST_INTEGRATION"][0]["attestor_readiness"]["kind"])

    def test_nfr_composite_requires_human_attestor(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"nfr_performance","stage":"PRE_MERGE","mode":"COMPOSITE","evidence_plan_ref":"backlog/features/FEAT-101.md#nfr"}]
        with self.assertRaises(r.AEError): contract.assurance_precheck(self.repo, c)

    def test_story_override_narrows_agent_merge(self):
        c = self.make_contract(); c["merge_mode_override"] = "DEVELOPER_REVIEW"
        out = contract.assurance_precheck(self.repo, c)
        self.assertEqual("DEVELOPER_REVIEW", out["effective_merge_mode"])

    def test_project_developer_review_cannot_be_broadened(self):
        self.cfg["merge_mode"] = "DEVELOPER_REVIEW"; self.write_config()
        out = contract.assurance_precheck(self.repo, self.make_contract())
        self.assertEqual("DEVELOPER_REVIEW", out["effective_merge_mode"])

    def test_release_pointer_kept_downstream(self):
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"traceability_completion","stage":"RELEASE_POINTER","mode":"MECHANICAL","evidence_plan_ref":"backlog/features/FEAT-101.md#release"}]
        out = contract.assurance_precheck(self.repo, c)
        self.assertEqual("traceability_completion", out["effective_assurance"]["RELEASE_POINTER"][0]["gate_id"])

    def test_custom_gate_reports_policy_pointer(self):
        self.cfg["assurance_policy"]["custom_gates"] = {"project_defined:data_retention":{
            "policy_ref":"docs/policies/data.md#retention",
            "allowed_stages":["POST_INTEGRATION"],
            "allowed_modes":["CHECK"],
            "attestor_class":"TOOL",
            "binding_class":"INTEGRATED_CAPABILITY",
            "minimum_evidence":1,
            "freshness_rule":"IMMUTABLE_BINDING",
            "failure_behavior":"BLOCK_STAGE"
        }}
        self.write_config()
        c = self.make_contract(); c["story_required_assurance"] = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"CHECK","evidence_plan_ref":"backlog/features/FEAT-101.md#data"}]
        out = contract.assurance_precheck(self.repo, c)
        item = out["effective_assurance"]["POST_INTEGRATION"][0]
        readiness = item["attestor_readiness"]
        self.assertEqual("docs/policies/data.md#retention", readiness["policy_ref"])
        self.assertEqual("TOOL", item["runtime_definition"]["attestor_class"])


class DoctorSchemaTests(unittest.TestCase):
    def test_current_schema_not_reported_as_legacy_gap(self):
        cfg = base_project_config()
        gaps = ae.config_gaps(cfg)
        self.assertNotIn("schema_version", gaps)
        self.assertIn("commands", gaps)

    def test_superseded_schema_reported_as_gap(self):
        cfg = base_project_config()
        cfg["schema_version"] = 3; cfg["kit_version"] = "0.5"
        self.assertIn("schema_version", ae.config_gaps(cfg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
