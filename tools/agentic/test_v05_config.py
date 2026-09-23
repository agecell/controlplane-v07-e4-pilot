#!/usr/bin/env python3
from __future__ import annotations
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import runtime as r
import contract


def v04_config() -> dict:
    return {
        "schema_version": 2,
        "kit_version": "0.4",
        "project_name": "Fixture",
        "remote": "origin",
        "target_branch": "main",
        "gitlab_repo": "group/product",
        "merge_mode": "DEVELOPER_REVIEW",
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
        "gitlab_host": "gitlab.example.invalid",
        "cleanup": {"mode":"SAFE_MERGED","approved":True,"approval_ref":"setup/cleanup","protected_branches":["main"],"triggers":["after_merge_verified"],"scope":"registered_owner_story_only"},
        "ci_required": True,
        "pool": {"model":"REUSABLE_LANE_POOL","size":7,"root":".claude/worktrees","reusable_ignored_directories":[]},
        "merge_policy": {"approved":True,"approval_ref":"setup/merge","human_review_required":False,"human_approvers":[],"mandatory_checks":["focused_tests","build","scope_check"],"default_checks":[],"check_evidence_kinds":{},"remote_lock_enabled":True,"lock_branch":"ae/locks/integration"},
    }


def v06_config(approved: bool = True) -> dict:
    cfg = contract.migrate_project_config_v04(v04_config())
    cfg["assurance_policy"]["approved"] = approved
    cfg["assurance_policy"]["approval_ref"] = "setup/assurance" if approved else "__CONFIGURE__"
    return cfg


class ProjectConfigMigrationTests(unittest.TestCase):
    def test_migration_changes_only_version_plus_assurance_and_forge(self):
        old = v04_config(); new = contract.migrate_project_config_v04(old)
        self.assertEqual(2, old["schema_version"])
        self.assertEqual(5, new["schema_version"])
        self.assertEqual("0.7", new["kit_version"])
        self.assertEqual("DEVELOPER_REVIEW", new["merge_mode"])
        self.assertTrue(new["merge_policy"]["approved"])
        self.assertTrue(new["remote_actions_ready"])
        self.assertFalse(new["assurance_policy"]["approved"])
        self.assertEqual([], new["assurance_policy"]["project_required_gates"])

    def test_migration_folds_gitlab_fields_into_forge_block(self):
        new = contract.migrate_project_config_v04(v04_config())
        # v0.4 could only speak GitLab, so the provider is derived, not guessed, and the
        # host/repo must still identify the same remote repository afterwards.
        self.assertEqual({"provider": "gitlab", "host": "gitlab.example.invalid", "repo": "group/product"}, new["forge"])
        self.assertNotIn("gitlab_host", new)
        self.assertNotIn("gitlab_repo", new)
        r.forge_ready(new)

    def test_migration_rejects_source_without_gitlab_fields(self):
        cfg = v04_config(); cfg.pop("gitlab_host")
        with self.assertRaises(r.AEError): contract.migrate_project_config_v04(cfg)

    def test_migration_does_not_mutate_source(self):
        old = v04_config(); snapshot = copy.deepcopy(old)
        contract.migrate_project_config_v04(old)
        self.assertEqual(snapshot, old)

    def test_migration_rejects_non_v04_source(self):
        cfg = v04_config(); cfg["schema_version"] = 3
        with self.assertRaises(r.AEError): contract.migrate_project_config_v04(cfg)

    def test_default_assurance_policy_is_safe(self):
        p = contract.default_assurance_policy()
        self.assertFalse(p["approved"])
        self.assertFalse(p["waiver_policy"]["default_waivable"])
        self.assertEqual({"1":[],"2":[],"3":[]}, p["tier_required_gates"])


class AssurancePolicyValidationTests(unittest.TestCase):
    def test_minimal_policy_valid(self):
        contract.validate_assurance_policy(v06_config()["assurance_policy"], require_approved=True)

    def test_unapproved_policy_blocks_ready(self):
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(v06_config(False)["assurance_policy"], require_approved=True)

    def test_project_required_gate_valid(self):
        p = v06_config()["assurance_policy"]
        p["project_required_gates"] = [{"gate_id":"traceability_completion","stage":"POST_INTEGRATION","mode":None}]
        contract.validate_assurance_policy(p, require_approved=True)

    def test_duplicate_project_gate_rejected(self):
        p = v06_config()["assurance_policy"]
        item = {"gate_id":"traceability_completion","stage":"POST_INTEGRATION","mode":None}
        p["project_required_gates"] = [item, copy.deepcopy(item)]
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_bad_tier_keys_rejected(self):
        p = v06_config()["assurance_policy"]
        p["tier_required_gates"].pop("3")
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_tier_security_mode_valid(self):
        p = v06_config()["assurance_policy"]
        p["tier_required_gates"]["3"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BOUNDED_MATERIAL"}]
        contract.validate_assurance_policy(p)

    def test_invalid_builtin_mode_rejected(self):
        p = v06_config()["assurance_policy"]
        p["tier_required_gates"]["3"] = [{"gate_id":"security_review","stage":"PRE_MERGE","mode":"BASELINE"}]
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_human_attestor_nested_mode_valid(self):
        p = v06_config()["assurance_policy"]
        p["human_attestors"] = {"security_review":{"BOUNDED_MATERIAL":["security-champion-a"],"CRITICAL_OR_MAJOR":["security-lead-a"]}}
        contract.validate_assurance_policy(p)

    def test_unknown_human_attestor_gate_rejected(self):
        p = v06_config()["assurance_policy"]
        p["human_attestors"] = {"unknown_gate":["human-a"]}
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_default_waivable_true_rejected(self):
        p = v06_config()["assurance_policy"]
        p["waiver_policy"]["default_waivable"] = True
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_waivable_gate_requires_risk_owner(self):
        p = v06_config()["assurance_policy"]
        p["waiver_policy"]["gates"] = {"nfr_performance":{"waivable":True,"risk_owners":[]}}
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_waivable_gate_with_owner_valid(self):
        p = v06_config()["assurance_policy"]
        p["waiver_policy"]["gates"] = {"nfr_performance":{"waivable":True,"risk_owners":["eng-manager-a"]}}
        contract.validate_assurance_policy(p)

    def test_custom_gate_requires_policy_ref(self):
        p = v06_config()["assurance_policy"]
        p["custom_gates"] = {"project_defined:data_retention":{}}
        with self.assertRaises(r.AEError): contract.validate_assurance_policy(p)

    def test_custom_gate_can_be_required(self):
        p = v06_config()["assurance_policy"]
        p["custom_gates"] = {"project_defined:data_retention":{"policy_ref":"docs/policies/data.md#retention"}}
        p["project_required_gates"] = [{"gate_id":"project_defined:data_retention","stage":"POST_INTEGRATION","mode":"CHECK"}]
        contract.validate_assurance_policy(p)


class RuntimeProjectConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.repo = Path(self.tmp.name)/"repo"; self.repo.mkdir()
        subprocess.run(["git","-C",str(self.repo),"init","-b","main"],check=True,capture_output=True,text=True)
        subprocess.run(["git","-C",str(self.repo),"config","user.name","Fixture"],check=True)
        subprocess.run(["git","-C",str(self.repo),"config","user.email","fixture@example.invalid"],check=True)
        (self.repo/".agentic").mkdir()
    def tearDown(self): self.tmp.cleanup()
    def write(self,cfg): (self.repo/".agentic/project.json").write_text(json.dumps(cfg))

    def test_runtime_config_accepts_schema5(self):
        cfg = v06_config(); self.write(cfg)
        self.assertEqual(5, r.config(self.repo)["schema_version"])
        r.config_ready(cfg)

    def test_runtime_config_rejects_schema3_no_implicit_migration(self):
        cfg = v06_config(); cfg["schema_version"] = 3; cfg["kit_version"] = "0.5"
        cfg.pop("forge"); cfg["gitlab_host"] = "gitlab.example.invalid"; cfg["gitlab_repo"] = "group/product"
        self.write(cfg)
        with self.assertRaises(r.AEError): r.config(self.repo)

    def test_runtime_config_rejects_schema2_no_implicit_migration(self):
        self.write(v04_config())
        with self.assertRaises(r.AEError): r.config(self.repo)

    def test_runtime_ready_requires_assurance_approval(self):
        cfg = v06_config(False); self.write(cfg)
        loaded = r.config(self.repo)
        with self.assertRaises(r.AEError): r.config_ready(loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
