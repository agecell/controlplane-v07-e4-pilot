#!/usr/bin/env python3
from __future__ import annotations
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))
import runtime as r
import contract

SHA_A = "a" * 40
SHA_B = "b" * 40


def valid_contract():
    return {
        "contract_schema_version": 2,
        "evidence_expectations": [],
        "story_id": "STORY-101",
        "assigned_developer": "dev-a",
        "sprint": "sprint-01",
        "risk_tier": 3,
        "publication": {
            "canonical_backlog": {
                "path": "backlog/features/FEAT-101.md",
                "revision": "REV-03",
                "publication_sha": SHA_A,
                "blob_sha": SHA_B,
            },
            "story_card": {
                "path": "backlog/stories/STORY-101.md",
                "publication_sha": SHA_A,
                "blob_sha": SHA_A,
            },
        },
        "traceability_root": ["REQ-101"],
        "ac_ids": ["AC-FUNC-01", "AC-SEC-01"],
        "scope_ref": "backlog/features/FEAT-101.md#scope",
        "preserve_ref": "backlog/features/FEAT-101.md#scope",
        "dependency_ref": "backlog/features/FEAT-101.md#dependency",
        "engineering_owner": {
            "owner_ref": "team:dev-a",
            "attestation_provider": "gitlab",
            "attestation_identity": "dev-a-user",
        },
        "engineering_impact": {
            "architecture": "MATERIAL",
            "scalability_nfr": "ROUTINE",
            "security_privacy_data": "MATERIAL",
            "operability_recovery": "MATERIAL",
            "human_ownership": "MATERIAL",
            "traceability_audit": "MATERIAL",
        },
        "story_required_assurance": [
            {
                "gate_id": "architecture_review",
                "stage": "PRE_MERGE",
                "mode": "CONFORMANCE",
                "evidence_plan_ref": "backlog/features/FEAT-101.md#assurance",
            },
            {
                "gate_id": "security_review",
                "stage": "PRE_MERGE",
                "mode": "BOUNDED_MATERIAL",
                "evidence_plan_ref": "backlog/features/FEAT-101.md#assurance",
            },
            {
                "gate_id": "human_understanding",
                "stage": "POST_INTEGRATION",
                "mode": "ASYNC_CAPABILITY",
                "evidence_plan_ref": "backlog/features/FEAT-101.md#ownership",
            },
        ],
        "merge_mode_override": "DEVELOPER_REVIEW",
        "build_start_ref": "backlog/features/FEAT-101.md#build-start",
        "final_acceptance_ref": "backlog/features/FEAT-101.md#final-acceptance",
        "capability_id": "FEAT-101",
    }


class ContractValidationTests(unittest.TestCase):
    def test_valid_contract(self):
        self.assertEqual(valid_contract(), contract.validate_contract(valid_contract()))

    def test_digest_is_stable_across_key_order(self):
        a = valid_contract()
        b = json.loads(json.dumps(a))
        b = {k: b[k] for k in reversed(list(b.keys()))}
        self.assertEqual(contract.contract_digest(a), contract.contract_digest(b))
        self.assertRegex(contract.contract_digest(a), r"^[0-9a-f]{64}$")

    def test_digest_changes_when_contract_changes(self):
        a = valid_contract(); b = copy.deepcopy(a)
        b["ac_ids"].append("AC-ERR-01")
        self.assertNotEqual(contract.contract_digest(a), contract.contract_digest(b))

    def test_missing_required_field_rejected(self):
        a = valid_contract(); del a["traceability_root"]
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_unknown_top_level_field_rejected(self):
        a = valid_contract(); a["magic"] = True
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_placeholder_authority_sha_rejected(self):
        a = valid_contract(); a["publication"]["canonical_backlog"]["publication_sha"] = "__FULL_SHA__"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_backlog_and_story_must_share_publication_commit(self):
        a = valid_contract(); a["publication"]["story_card"]["publication_sha"] = SHA_B
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_path_traversal_rejected(self):
        a = valid_contract(); a["publication"]["story_card"]["path"] = "../STORY-101.md"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_wrong_risk_tier_rejected(self):
        a = valid_contract(); a["risk_tier"] = 4
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_unknown_impact_value_rejected(self):
        a = valid_contract(); a["engineering_impact"]["architecture"] = "HIGH"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_missing_impact_lens_rejected(self):
        a = valid_contract(); del a["engineering_impact"]["traceability_audit"]
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_illegal_architecture_mode_rejected(self):
        a = valid_contract(); a["story_required_assurance"][0]["mode"] = "APPROVE_ALL"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_illegal_security_mode_rejected(self):
        a = valid_contract(); a["story_required_assurance"][1]["mode"] = "BASELINE"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_merge_override_cannot_broaden(self):
        a = valid_contract(); a["merge_mode_override"] = "AGENT_MERGE"
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_duplicate_obligation_rejected(self):
        a = valid_contract(); a["story_required_assurance"].append(copy.deepcopy(a["story_required_assurance"][0]))
        with self.assertRaises(r.AEError): contract.validate_contract(a)

    def test_empty_assurance_is_valid_fast_path(self):
        a = valid_contract(); a["risk_tier"] = 1; a["story_required_assurance"] = []
        a["engineering_owner"] = None
        a["engineering_impact"] = {k:"ROUTINE" for k in contract.IMPACT_KEYS}
        self.assertEqual([], contract.validate_contract(a)["story_required_assurance"])

    def test_project_defined_gate_shape_allowed(self):
        a = valid_contract(); a["story_required_assurance"] = [{
            "gate_id":"project_defined:data_retention",
            "stage":"POST_INTEGRATION",
            "mode":"CHECK",
            "evidence_plan_ref":"backlog/features/FEAT-101.md#retention",
        }]
        self.assertEqual("project_defined:data_retention", contract.validate_contract(a)["story_required_assurance"][0]["gate_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
