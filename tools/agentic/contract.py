#!/usr/bin/env python3
"""Control Plane v0.7 contract, precheck, and explicit v0.4 migration/binding helper.

This module remains mutation-free for source/Git history. It validates normalized Story
contracts and schema-4 project assurance policy, verifies authority publication blobs,
resolves project + Tier + Story assurance obligations, and emits a bounded readiness
summary for the Control Plane. It does not create task manifests, lease lanes, run BUILD,
or claim final AUTHORIZED_TO_START by itself; existing runtime preflight still applies.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import runtime as r
except ModuleNotFoundError:  # pragma: no cover - package-style import in some harnesses
    from . import runtime as r

CONTRACT_SCHEMA_VERSION = 2
PROJECT_SCHEMA_VERSION = 5
KIT_VERSION = "0.7"
IMPACT_VALUES = {"N/A", "ROUTINE", "MATERIAL"}
IMPACT_KEYS = {
    "architecture",
    "scalability_nfr",
    "security_privacy_data",
    "operability_recovery",
    "human_ownership",
    "traceability_audit",
}
STAGES = {"PRE_MERGE", "POST_INTEGRATION", "RELEASE_POINTER"}
BUILTIN_MODES: dict[str, set[str | None]] = {
    "architecture_review": {"CONFORMANCE", "DECISION"},
    "security_review": {"BOUNDED_MATERIAL", "CRITICAL_OR_MAJOR"},
    "human_understanding": {"ASYNC_CAPABILITY", "STORY_EXPLAIN_BACK"},
    "nfr_performance": {None, "OBJECTIVE_TOOL", "COMPOSITE"},
    "operability_recovery": {None, "CAPABILITY", "STORY"},
    "traceability_completion": {None, "MECHANICAL"},
    # technical_review is virtual/derived from v0.4 review and is not expected in
    # story_required_assurance, but accepting it keeps the contract generic.
    "technical_review": {None},
}
GATE_ID = re.compile(r"(?:[a-z][a-z0-9_]{1,63}|project_defined:[a-z][a-z0-9_.-]{1,63})\Z")
REF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/#@+-]{0,255}\Z")


BUILTIN_GATES = set(BUILTIN_MODES)
ATTESTOR_CLASSES = {"TOOL", "INDEPENDENT_REVIEWER", "ENGINEERING_OWNER", "AUTHORIZED_HUMAN", "COMPOSITE", "CONTROL_PLANE"}
BINDING_CLASSES = {"CONTRACT", "CANDIDATE", "INTEGRATED_CAPABILITY"}
CUSTOM_GATE_RUNTIME_FIELDS = {"allowed_stages", "allowed_modes", "attestor_class", "binding_class", "minimum_evidence", "freshness_rule", "failure_behavior"}
CUSTOM_FRESHNESS_RULES = {"IMMUTABLE_BINDING", "POLICY_WINDOW", "MANUAL"}
CUSTOM_FAILURE_BEHAVIORS = {"BLOCK_STAGE"}

# When the same built-in gate/stage is required by more than one authority, v0.5
# keeps the stricter known mode. This is a bounded deterministic rule, not a generic
# policy-expression engine. Unknown/custom mode conflicts are rejected for human review.
MODE_STRENGTH: dict[str, dict[Any, int]] = {
    "architecture_review": {"CONFORMANCE": 1, "DECISION": 2},
    "security_review": {"BOUNDED_MATERIAL": 1, "CRITICAL_OR_MAJOR": 2},
    "human_understanding": {"ASYNC_CAPABILITY": 1, "STORY_EXPLAIN_BACK": 2},
    "nfr_performance": {None: 0, "OBJECTIVE_TOOL": 1, "COMPOSITE": 2},
    "operability_recovery": {None: 0, "CAPABILITY": 1, "STORY": 2},
    "traceability_completion": {None: 0, "MECHANICAL": 1},
    "technical_review": {None: 0},
}



def default_assurance_policy() -> dict[str, Any]:
    """Safe distribution default: no blanket gates and no authority activated."""
    return {
        "approved": False,
        "approval_ref": "__CONFIGURE__",
        "project_required_gates": [],
        "tier_required_gates": {"1": [], "2": [], "3": []},
        "human_attestors": {},
        "waiver_policy": {"default_waivable": False, "gates": {}},
        "custom_gates": {},
    }



def _validate_custom_gate_runtime_body(gate_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate generic runtime semantics for a project-defined assurance gate.

    Phase 5 permits a migration-era custom gate to contain only policy_ref, but any
    custom gate that becomes effective at runtime must have the full semantics below.
    This avoids a magic boolean while keeping project-specific reasoning outside code.
    """
    _need(isinstance(body, dict) and "policy_ref" in body, f"custom_gates[{gate_id}] requires policy_ref.")
    _ref(body.get("policy_ref"), f"custom_gates[{gate_id}].policy_ref")
    _need(set(body)=={"policy_ref", *CUSTOM_GATE_RUNTIME_FIELDS}, f"custom_gates[{gate_id}] runtime semantics are incomplete.")
    stages=body.get("allowed_stages")
    _need(isinstance(stages,list) and stages and len(stages)==len(set(stages)) and set(stages).issubset(STAGES), f"custom_gates[{gate_id}].allowed_stages invalid.")
    modes=body.get("allowed_modes")
    _need(isinstance(modes,list) and modes and all(x is None or (isinstance(x,str) and x.strip()) for x in modes), f"custom_gates[{gate_id}].allowed_modes invalid.")
    _need(len({json.dumps(x,sort_keys=True) for x in modes})==len(modes), f"custom_gates[{gate_id}].allowed_modes contains duplicates.")
    _need(body.get("attestor_class") in ATTESTOR_CLASSES, f"custom_gates[{gate_id}].attestor_class invalid.")
    _need(body.get("binding_class") in BINDING_CLASSES, f"custom_gates[{gate_id}].binding_class invalid.")
    _need(type(body.get("minimum_evidence")) is int and 0 <= body["minimum_evidence"] <= 32, f"custom_gates[{gate_id}].minimum_evidence must be 0..32.")
    _need(body.get("freshness_rule") in CUSTOM_FRESHNESS_RULES, f"custom_gates[{gate_id}].freshness_rule invalid.")
    _need(body.get("failure_behavior") in CUSTOM_FAILURE_BEHAVIORS, f"custom_gates[{gate_id}].failure_behavior invalid.")
    return body


def custom_gate_runtime_definition(policy: dict[str, Any], gate_id: str, *, stage: str | None = None, mode: Any = None) -> dict[str, Any]:
    body=policy.get("custom_gates",{}).get(gate_id)
    _need(isinstance(body,dict), f"Custom gate {gate_id} is not configured.")
    _validate_custom_gate_runtime_body(gate_id, body)
    if stage is not None:
        _need(stage in body["allowed_stages"], f"Custom gate {gate_id} does not allow stage {stage}.")
    _need(mode in body["allowed_modes"], f"Custom gate {gate_id} does not allow mode {mode!r}.")
    return copy.deepcopy(body)

def _gate_key(item: dict[str, Any]) -> tuple[str, str, Any]:
    return item["gate_id"], item["stage"], item.get("mode")


def _validate_project_gate_item(item: Any, label: str, custom_gate_ids: set[str]) -> dict[str, Any]:
    _need(isinstance(item, dict), f"{label} must be an object.")
    _need(set(item) == {"gate_id", "stage", "mode"}, f"{label} must contain gate_id, stage, and mode only.")
    gate_id = _nonblank(item.get("gate_id"), f"{label}.gate_id")
    _need(bool(GATE_ID.fullmatch(gate_id)), f"Invalid {label}.gate_id.")
    _need(gate_id in BUILTIN_GATES or gate_id in custom_gate_ids, f"{label}.gate_id is not a built-in or configured custom gate.")
    stage = item.get("stage")
    _need(stage in STAGES, f"{label}.stage must be PRE_MERGE, POST_INTEGRATION, or RELEASE_POINTER.")
    mode = item.get("mode")
    if gate_id in BUILTIN_MODES:
        _need(mode in BUILTIN_MODES[gate_id], f"Unsupported mode for {gate_id}.")
    else:
        _need(mode is None or isinstance(mode, str), f"{label}.mode must be null or a project-defined string.")
    return item


def _validate_gate_list(value: Any, label: str, custom_gate_ids: set[str]) -> list[dict[str, Any]]:
    _need(isinstance(value, list), f"{label} must be a list.")
    seen: set[tuple[str, str, Any]] = set()
    for i, item in enumerate(value):
        _validate_project_gate_item(item, f"{label}[{i}]", custom_gate_ids)
        key = _gate_key(item)
        _need(key not in seen, f"{label} contains a duplicate gate obligation.")
        seen.add(key)
    return value


def _validate_attestor_users(value: Any, label: str) -> list[str]:
    users = _unique_strings(value, label)
    for i, user in enumerate(users):
        _need("__" not in user and bool(re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", user)), f"Invalid {label}[{i}] identity.")
    return users


def validate_assurance_policy(policy: Any, *, require_approved: bool = False) -> dict[str, Any]:
    """Validate the schema-3 assurance_policy block.

    Phase 2 validates load-bearing project authority fields. Custom-gate semantic bodies
    remain intentionally bounded: each custom gate must at least point to a reviewed
    policy_ref; full runtime semantics are enforced when the assurance registry lands.
    """
    _need(isinstance(policy, dict), "assurance_policy must be an object.")
    required = {"approved", "approval_ref", "project_required_gates", "tier_required_gates", "human_attestors", "waiver_policy", "custom_gates"}
    _need(set(policy) == required, f"assurance_policy fields are invalid: {sorted(set(policy) ^ required)}")
    _need(type(policy.get("approved")) is bool, "assurance_policy.approved must be explicitly true or false.")
    if policy["approved"] or require_approved:
        _need(policy["approved"] is True, "Assurance policy requires maintainer review before v0.5 execution.")
        _nonblank(policy.get("approval_ref"), "assurance_policy.approval_ref")
    else:
        _need(isinstance(policy.get("approval_ref"), str), "assurance_policy.approval_ref must be a string.")

    custom = policy.get("custom_gates")
    _need(isinstance(custom, dict), "assurance_policy.custom_gates must be an object.")
    custom_ids: set[str] = set()
    for gate_id, body in custom.items():
        _need(isinstance(gate_id, str) and gate_id.startswith("project_defined:") and bool(GATE_ID.fullmatch(gate_id)), "Custom gate IDs must use project_defined:<id>.")
        _need(isinstance(body, dict), f"custom_gates[{gate_id}] must be an object.")
        _need("policy_ref" in body, f"custom_gates[{gate_id}] requires policy_ref.")
        _ref(body.get("policy_ref"), f"custom_gates[{gate_id}].policy_ref")
        extra=set(body)-{"policy_ref"}
        _need(extra.issubset(CUSTOM_GATE_RUNTIME_FIELDS), f"custom_gates[{gate_id}] contains unsupported runtime fields.")
        if extra:
            _need(extra==CUSTOM_GATE_RUNTIME_FIELDS, f"custom_gates[{gate_id}] runtime semantics must define all required fields or only policy_ref.")
            _validate_custom_gate_runtime_body(gate_id, body)
        custom_ids.add(gate_id)

    _validate_gate_list(policy.get("project_required_gates"), "assurance_policy.project_required_gates", custom_ids)
    tiers = policy.get("tier_required_gates")
    _need(isinstance(tiers, dict) and set(tiers) == {"1", "2", "3"}, "assurance_policy.tier_required_gates must contain keys 1, 2, and 3.")
    for tier in ("1", "2", "3"):
        _validate_gate_list(tiers[tier], f"assurance_policy.tier_required_gates[{tier}]", custom_ids)

    attestors = policy.get("human_attestors")
    _need(isinstance(attestors, dict), "assurance_policy.human_attestors must be an object.")
    for gate_id, mapping in attestors.items():
        _need(gate_id in BUILTIN_GATES or gate_id in custom_ids, f"human_attestors references unknown gate {gate_id}.")
        if isinstance(mapping, list):
            _validate_attestor_users(mapping, f"human_attestors[{gate_id}]")
        else:
            _need(isinstance(mapping, dict) and mapping, f"human_attestors[{gate_id}] must be a non-empty list or mode map.")
            for mode, users in mapping.items():
                _need(isinstance(mode, str) and bool(mode.strip()), f"human_attestors[{gate_id}] has invalid mode key.")
                if gate_id in BUILTIN_MODES:
                    _need(mode in {m for m in BUILTIN_MODES[gate_id] if isinstance(m, str)}, f"human_attestors[{gate_id}] has unsupported mode {mode}.")
                _validate_attestor_users(users, f"human_attestors[{gate_id}][{mode}]")

    waiver = policy.get("waiver_policy")
    _need(isinstance(waiver, dict) and set(waiver) == {"default_waivable", "gates"}, "assurance_policy.waiver_policy fields are invalid.")
    _need(waiver.get("default_waivable") is False, "v0.5 requires default_waivable=false; open exceptions per gate explicitly.")
    _need(isinstance(waiver.get("gates"), dict), "waiver_policy.gates must be an object.")
    for gate_id, rule in waiver["gates"].items():
        _need(gate_id in BUILTIN_GATES or gate_id in custom_ids, f"waiver_policy references unknown gate {gate_id}.")
        _need(isinstance(rule, dict) and set(rule) == {"waivable", "risk_owners"}, f"waiver_policy.gates[{gate_id}] fields are invalid.")
        _need(type(rule.get("waivable")) is bool, f"waiver_policy.gates[{gate_id}].waivable must be bool.")
        owners = _validate_attestor_users(rule.get("risk_owners"), f"waiver_policy.gates[{gate_id}].risk_owners")
        if rule["waivable"]:
            _need(bool(owners), f"waiver_policy.gates[{gate_id}] requires at least one risk owner when waivable.")
    return policy


def fold_gitlab_into_forge(out: dict[str, Any]) -> dict[str, Any]:
    """Replace the gitlab_host/gitlab_repo pair with the schema-4 forge block, in place.

    `provider` is read off a fact, not guessed: v0.4 and v0.5 could only speak GitLab,
    so a configuration carrying these two fields described a GitLab project by
    construction. Host and repo transfer byte for byte, so a migrated configuration
    still identifies exactly the same remote repository and `verify_remote` keeps
    matching the same fetch/push URLs it matched before.

    Shared by the v0.4 and v0.5 migration paths so the two cannot drift.
    """
    host = out.pop("gitlab_host", None)
    repo = out.pop("gitlab_repo", None)
    _need(isinstance(host, str) and isinstance(repo, str), "Source configuration must carry gitlab_host and gitlab_repo to migrate into the forge block.")
    _need("forge" not in out, "Source configuration unexpectedly contains a forge block; review manually.")
    out["forge"] = {"provider": "gitlab", "host": host, "repo": repo}
    return out


def migrate_project_config_v04(data: Any) -> dict[str, Any]:
    """Return a migration preview. Never mutates the supplied object or approves assurance.

    v0.4 (schema 2) migrates straight to the current schema in one step, so the preview
    both adds the unapproved assurance policy v0.5 introduced and folds the GitLab host
    and repository into the forge block v0.6 introduced. Placeholder values are carried
    through unchanged: a v0.4 configuration still reading `__CONFIGURE__` migrates to a
    forge block that `forge_ready` will keep rejecting, which is the intended outcome —
    migration reshapes a configuration, it never completes one.
    """
    _need(isinstance(data, dict), "Project configuration must be a JSON object.")
    _need(data.get("schema_version") == 2 and data.get("kit_version") == "0.4", "Migration preview accepts only reviewed v0.4 schema-2 configuration.")
    out = copy.deepcopy(data)
    out["schema_version"] = PROJECT_SCHEMA_VERSION
    out["kit_version"] = KIT_VERSION
    _need("assurance_policy" not in out, "v0.4 source unexpectedly contains assurance_policy; review manually.")
    out["assurance_policy"] = default_assurance_policy()
    fold_gitlab_into_forge(out)
    split_required_checks(out)
    out.setdefault("backlog_authority_ref", None)
    return out


def split_required_checks(out: dict[str, Any]) -> dict[str, Any]:
    """Move v0.6's single required_checks list into schema 5's two, preserving order.

    **Everything previously required becomes mandatory.** A migration may not reduce
    enforcement that was already in force, and nothing here knows which Story could
    satisfy which check — that is derived per Story from acceptance evidence, once a team
    moves a check into default_checks and maps its evidence kinds deliberately.

    Shared by the v0.4 path and mirrored by upgrade_v06_to_v07.py, which cannot import
    this module because it runs before the code it installs.
    """
    policy = out.get("merge_policy")
    if not isinstance(policy, dict) or "required_checks" not in policy:
        return out
    required = policy.pop("required_checks")
    rebuilt: dict[str, Any] = {}
    for key, value in policy.items():
        rebuilt[key] = value
    rebuilt["mandatory_checks"] = list(required) if isinstance(required, list) else required
    rebuilt["default_checks"] = []
    rebuilt["check_evidence_kinds"] = {}
    out["merge_policy"] = rebuilt
    return out


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise r.AEError(message)


def _nonblank(value: Any, field: str) -> str:
    _need(isinstance(value, str) and bool(value.strip()) and "__" not in value, f"{field} must be a resolved non-placeholder string.")
    return value


def _ident(value: Any, field: str) -> str:
    _need(isinstance(value, str) and r.IDENT.fullmatch(value), f"Invalid {field} identifier.")
    return value


def _sha(value: Any, field: str) -> str:
    _need(isinstance(value, str) and r.SHA.fullmatch(value), f"{field} requires a full lowercase SHA.")
    return value


def _relpath(value: Any, field: str) -> str:
    value = _nonblank(value, field)
    _need("\\" not in value and ":" not in value and not value.startswith("/"), f"{field} must be a relative POSIX path.")
    p = PurePosixPath(value)
    _need(".." not in p.parts and "." not in p.parts, f"{field} must not traverse directories.")
    _need(len(p.parts) >= 2, f"{field} must name a repository-relative file.")
    return value


def _ref(value: Any, field: str) -> str:
    value = _nonblank(value, field)
    _need(bool(REF_ID.fullmatch(value)) or ("#" in value and not any(c.isspace() for c in value)), f"Invalid {field} reference.")
    return value


def _unique_strings(value: Any, field: str, minimum: int = 0) -> list[str]:
    _need(isinstance(value, list) and len(value) >= minimum, f"{field} must be a list.")
    _need(all(isinstance(x, str) and x.strip() for x in value), f"{field} must contain non-empty strings.")
    _need(len(set(value)) == len(value), f"{field} contains duplicates.")
    return value


def validate_gate_obligation(item: Any, *, index: int | None = None) -> dict[str, Any]:
    label = f"story_required_assurance[{index}]" if index is not None else "assurance obligation"
    _need(isinstance(item, dict), f"{label} must be an object.")
    allowed = {"gate_id", "stage", "mode", "evidence_plan_ref"}
    _need(set(item).issubset(allowed), f"{label} has unsupported fields: {sorted(set(item)-allowed)}")
    gate_id = _nonblank(item.get("gate_id"), f"{label}.gate_id")
    _need(bool(GATE_ID.fullmatch(gate_id)), f"Invalid {label}.gate_id.")
    stage = item.get("stage")
    _need(stage in STAGES, f"{label}.stage must be PRE_MERGE, POST_INTEGRATION, or RELEASE_POINTER.")
    mode = item.get("mode")
    if gate_id in BUILTIN_MODES:
        _need(mode in BUILTIN_MODES[gate_id], f"Unsupported mode for {gate_id}.")
    elif gate_id.startswith("project_defined:"):
        _need(mode is None or isinstance(mode, str), f"{label}.mode must be null or a project-defined string.")
    _ref(item.get("evidence_plan_ref"), f"{label}.evidence_plan_ref")
    return item


def validate_contract(data: Any) -> dict[str, Any]:
    """Validate normalized Story contract schema 1 and return the same object.

    Validation is intentionally strict on authority binding fields and intentionally
    silent on product semantics. It cannot prove that ACs are correct or approvals are genuine.
    """
    _need(isinstance(data, dict), "Normalized Story contract must be a JSON object.")
    required = {
        "contract_schema_version", "story_id", "assigned_developer", "sprint", "risk_tier",
        "publication", "traceability_root", "ac_ids", "scope_ref", "preserve_ref",
        "dependency_ref", "engineering_owner", "engineering_impact",
        "story_required_assurance", "merge_mode_override", "build_start_ref",
        "final_acceptance_ref", "capability_id", "evidence_expectations",
    }
    _need(required.issubset(data), f"Normalized Story contract is missing fields: {sorted(required-set(data))}")
    _need(set(data) == required, f"Normalized Story contract has unsupported fields: {sorted(set(data)-required)}")
    _need(data.get("contract_schema_version") == CONTRACT_SCHEMA_VERSION, "Use normalized Story contract schema 2.")
    _ident(data.get("story_id"), "story_id")
    _ident(data.get("assigned_developer"), "assigned_developer")
    _ident(data.get("sprint"), "sprint")
    _need(type(data.get("risk_tier")) is int and data["risk_tier"] in (1, 2, 3), "risk_tier must be 1, 2, or 3.")

    publication = data.get("publication")
    _need(isinstance(publication, dict) and set(publication) == {"canonical_backlog", "story_card"}, "publication must contain canonical_backlog and story_card only.")
    backlog = publication["canonical_backlog"]
    _need(isinstance(backlog, dict) and set(backlog) == {"path", "revision", "publication_sha", "blob_sha"}, "canonical_backlog publication fields are invalid.")
    _relpath(backlog.get("path"), "canonical_backlog.path")
    _nonblank(backlog.get("revision"), "canonical_backlog.revision")
    authority_sha = _sha(backlog.get("publication_sha"), "canonical_backlog.publication_sha")
    _sha(backlog.get("blob_sha"), "canonical_backlog.blob_sha")
    story = publication["story_card"]
    _need(isinstance(story, dict) and set(story) == {"path", "publication_sha", "blob_sha"}, "story_card publication fields are invalid.")
    _relpath(story.get("path"), "story_card.path")
    _need(_sha(story.get("publication_sha"), "story_card.publication_sha") == authority_sha, "Canonical backlog and Story card must be bound to the same authority publication commit.")
    _sha(story.get("blob_sha"), "story_card.blob_sha")

    roots = _unique_strings(data.get("traceability_root"), "traceability_root", minimum=1)
    for i, value in enumerate(roots): _ref(value, f"traceability_root[{i}]")
    ac_ids = _unique_strings(data.get("ac_ids"), "ac_ids", minimum=1)
    for i, value in enumerate(ac_ids): _ref(value, f"ac_ids[{i}]")
    for field in ("scope_ref", "preserve_ref", "dependency_ref", "build_start_ref", "final_acceptance_ref"):
        _ref(data.get(field), field)

    owner = data.get("engineering_owner")
    _need(owner is None or isinstance(owner, dict), "engineering_owner must be null or an object.")
    if owner is not None:
        _need(set(owner) == {"owner_ref", "attestation_provider", "attestation_identity"}, "engineering_owner fields are invalid.")
        _nonblank(owner.get("owner_ref"), "engineering_owner.owner_ref")
        # Contract validation is provider-agnostic: a contract is portable and may be
        # validated without a project configuration in hand. Whether the declared
        # provider matches the project's configured forge is checked where that context
        # exists, in assurance._allowed_human_users.
        _need(owner.get("attestation_provider") in ("gitlab", "github"),
              "engineering_owner.attestation_provider must be gitlab or github.")
        _nonblank(owner.get("attestation_identity"), "engineering_owner.attestation_identity")

    impact = data.get("engineering_impact")
    _need(isinstance(impact, dict) and set(impact) == IMPACT_KEYS, "engineering_impact must contain the six v0.2 lens keys exactly.")
    for key, value in impact.items(): _need(value in IMPACT_VALUES, f"Invalid engineering impact value for {key}.")

    obligations = data.get("story_required_assurance")
    _need(isinstance(obligations, list), "story_required_assurance must be a list.")
    seen: set[tuple[str, str, Any]] = set()
    for i, item in enumerate(obligations):
        validate_gate_obligation(item, index=i)
        key = (item["gate_id"], item["stage"], item.get("mode"))
        _need(key not in seen, "Duplicate Story assurance obligation.")
        seen.add(key)

    override = data.get("merge_mode_override")
    _need(override in (None, "DEVELOPER_REVIEW"), "merge_mode_override may only be null or DEVELOPER_REVIEW.")
    _ident(data.get("capability_id"), "capability_id")
    # Carried so check applicability can be derived per Story rather than demanded of
    # every Story alike. May be empty: a contract migrated from schema 1 has no evidence
    # kinds to carry, and an empty list derives nothing rather than guessing. What keeps
    # that safe is that migration moves every previously required check into
    # mandatory_checks, which derive from nothing and apply unconditionally.
    import handoff
    handoff.validate_evidence_expectations(data.get("evidence_expectations"), ac_ids)
    return data


def canonical_bytes(data: dict[str, Any]) -> bytes:
    """Stable UTF-8 JSON representation used only for runtime binding digests."""
    validate_contract(data)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def contract_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def load_contract(path: str) -> dict[str, Any]:
    data = r.read_json(path)
    validate_contract(data)
    return data


def _git_blob_at(repo: Path, publication_sha: str, relpath: str) -> str:
    """Return the exact blob SHA for relpath at publication_sha."""
    r.commit(repo, publication_sha)
    _, out = r.git(repo, "ls-tree", "-z", publication_sha, "--", relpath)
    rows = [row for row in out.split("\0") if row]
    _need(len(rows) == 1, f"Authority path is missing or ambiguous at publication commit: {relpath}")
    meta, sep, path = rows[0].partition("\t")
    _need(bool(sep) and path == relpath, f"Authority path mismatch at publication commit: {relpath}")
    parts = meta.split()
    _need(len(parts) == 3 and parts[1] == "blob", f"Authority reference is not a file blob: {relpath}")
    return _sha(parts[2], f"published blob for {relpath}")


def verify_contract_publication(repo: str | Path, data: dict[str, Any]) -> dict[str, str]:
    """Verify backlog + Story pointers against immutable Git publication facts."""
    repo = r.root(Path(repo))
    validate_contract(data)
    publication = data["publication"]
    verified: dict[str, str] = {}
    for key in ("canonical_backlog", "story_card"):
        item = publication[key]
        actual = _git_blob_at(repo, item["publication_sha"], item["path"])
        _need(actual == item["blob_sha"], f"{key} blob SHA does not match the published authority file.")
        verified[key] = actual
    return verified


AUTHORITY_CURRENT = "CURRENT"
AUTHORITY_SUPERSEDED = "SUPERSEDED"
AUTHORITY_UNDETERMINED = "UNDETERMINED"

# What the mechanical floor (A3) may say. Deliberately no "CURRENT": asking Git whether
# a file was touched cannot establish that a revision is in force, only that nothing
# visibly moved. Only the published pointer (A2) may say CURRENT.
MECHANICAL_NO_CHANGE = "NO_CHANGE_DETECTED"
MECHANICAL_CHANGED = "CHANGED"
MECHANICAL_UNDETERMINED = "UNDETERMINED"


def _authority_reference(repo: Path, cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve the commit that represents authority in force, without touching the network.

    Three candidates, in order, and the answer always names which one was used:

    1. `backlog_authority_ref`, when the project declares one. A backlog is not always
       maintained on the branch the code integrates into -- in the v0.6 pilot it was not,
       and a check anchored only to the target branch therefore could not see the
       revision that had superseded the one under test.
    2. the remote-tracking target branch, which is what the rest of the team sees;
    3. the local target branch.

    A precheck that silently fetched would turn a local read into a network operation,
    and one that silently used a stale local branch would answer a different question
    from the one asked.
    """
    declared = cfg.get("backlog_authority_ref")
    candidates = ([declared] if _nonblank_or_none(declared) else [])
    candidates += [f'refs/remotes/{cfg.get("remote", "origin")}/{cfg["target_branch"]}',
                   f'refs/heads/{cfg["target_branch"]}']
    for ref in candidates:
        sha = r.local_ref(repo, ref)
        if sha:
            return sha, ref
    return None, None


def _nonblank_or_none(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mechanical_authority_signal(repo: Path, cfg: dict[str, Any], data: dict[str, Any],
                                 reference: str | None, ref_name: str | None) -> dict[str, Any]:
    """A3: has anything touched the authority files since the publication commit?

    Purely mechanical, and deliberately weak. It cannot tell a substantive revision from
    a typo fix, and it cannot establish that a revision is still in force -- a file that
    nobody touched may still have been superseded elsewhere. It is a **floor**: it can
    raise the alarm, and it can never clear one.
    """
    publication = data["publication"]
    paths = sorted({publication[key]["path"] for key in ("canonical_backlog", "story_card")})
    base = {"paths": paths, "reference": ref_name, "changes": []}
    if reference is None:
        return {**base, "state": MECHANICAL_UNDETERMINED, "reason": "NO_AUTHORITY_REFERENCE"}
    changes: list[dict[str, Any]] = []
    for key in ("canonical_backlog", "story_card"):
        item = publication[key]
        sha, path = item["publication_sha"], item["path"]
        # 0 reachable, 1 not reachable, 128 the object is not in this repository at all.
        code, _ = r.git(repo, "merge-base", "--is-ancestor", sha, reference, allowed=(0, 1, 128))
        if code == 128:
            return {**base, "state": MECHANICAL_UNDETERMINED, "reason": "PUBLICATION_COMMIT_ABSENT"}
        if code != 0:
            return {**base, "state": MECHANICAL_UNDETERMINED, "reason": "PUBLICATION_NOT_ON_REFERENCE"}
        for row in r.git(repo, "log", "--format=%H %s", f"{sha}..{reference}", "--", path)[1].splitlines():
            commit, _, subject = row.partition(" ")
            changes.append({"key": key, "path": path, "commit": commit, "subject": subject})
    if changes:
        named = ", ".join(f'{x["commit"][:12]} ({x["path"]})' for x in changes[:4])
        return {**base, "state": MECHANICAL_CHANGED, "reason": "AUTHORITY_FILES_TOUCHED",
                "changes": changes,
                "detail": f"Commits on {ref_name} have touched the authority files since this contract's "
                          f"publication commit: {named}."}
    return {**base, "state": MECHANICAL_NO_CHANGE, "reason": None}


def _published_authority_pointer(repo: Path, data: dict[str, Any],
                                 reference: str | None, ref_name: str | None) -> dict[str, Any]:
    """A2: what the Feature card, as published, says is currently in force."""
    import handoff
    backlog = data["publication"]["canonical_backlog"]
    bound_revision = backlog["revision"]
    base = {"source": ref_name, "feature_path": backlog["path"], "bound_revision": bound_revision,
            "current_revision": None, "pointer": None}
    if reference is None:
        return {**base, "state": AUTHORITY_UNDETERMINED, "reason": "NO_AUTHORITY_REFERENCE",
                "detail": "No backlog_authority_ref is configured and no target branch could be "
                          "resolved, so what is in force cannot be determined."}
    pointer, reason = handoff.read_authority_pointer_at(repo, reference, backlog["path"])
    if pointer is None:
        detail = {
            "AUTHORITY_SOURCE_ABSENT": f'{backlog["path"]} does not exist at {ref_name}.',
            "AUTHORITY_SOURCE_UNREADABLE": f'{backlog["path"]} at {ref_name} carries no readable bp-meta block.',
            "NO_AUTHORITY_POINTER": f'{backlog["path"]} at {ref_name} publishes no authority_pointer, so '
                                    f'Backlog Preparation has not stated which revision is in force.',
        }[reason]
        return {**base, "state": AUTHORITY_UNDETERMINED, "reason": reason,
                "detail": detail + " This is reported as unknown, never as current."}
    current = pointer["current_revision"]
    base = {**base, "current_revision": current, "pointer": pointer}
    if current == bound_revision:
        return {**base, "state": AUTHORITY_CURRENT, "reason": None, "detail": None}
    known = bound_revision in (pointer.get("superseded_revisions") or [])
    return {**base, "state": AUTHORITY_SUPERSEDED,
            "reason": "REVISION_SUPERSEDED" if known else "REVISION_UNKNOWN_TO_FEATURE",
            "detail": f'This contract is bound to {bound_revision}, but {backlog["path"]} at {ref_name} '
                      f'publishes {current} as the revision in force'
                      + (f', and lists {bound_revision} as superseded.' if known else
                         f'. {bound_revision} is not among the revisions it lists as superseded either, '
                         f'so the two disagree about history as well.')
                      + " Re-bind the contract to the current revision. Completed work bound to the "
                        "earlier revision stays valid and is not re-judged."}


def check_authority_currency(repo: Path, cfg: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Is the authority this contract is bound to still the one in force?

    Publication binding proves a contract refers to a state that really *was* published.
    Git history is immutable, so that check succeeds forever -- including long after the
    Story card has been revised, corrected or withdrawn. In the v0.6 pilot, FEAT-001 was
    raised REV-02 -> REV-03 precisely to fix a wrong attestation identity, and the old
    contract still bound to REV-02 passed precheck unchanged.

    Two independent signals, with deliberately unequal authority:

    * **A2, the published pointer.** Backlog Preparation states, on the Feature card,
      which revision is in force. This is the only thing that may establish `CURRENT`.
    * **A3, the mechanical floor.** Git is asked whether anything has touched the
      authority files since the publication commit. It may raise an alarm and may never
      clear one -- a file nobody touched can still have been superseded elsewhere.

    Combined:

    | A2 | A3 | verdict |
    |---|---|---|
    | SUPERSEDED | any | SUPERSEDED |
    | CURRENT | CHANGED | SUPERSEDED, `AUTHORITY_CHANGED_WITHOUT_REVISION` |
    | CURRENT | other | CURRENT |
    | UNDETERMINED | CHANGED | SUPERSEDED, from the floor |
    | UNDETERMINED | other | UNDETERMINED |

    **This never re-judges completed work.** It gates a contract being used to authorize
    *new* execution. A traceability record written under an earlier revision stays valid
    and keeps naming the revision it was actually bound to.
    """
    reference, ref_name = _authority_reference(repo, cfg)
    pointer = _published_authority_pointer(repo, data, reference, ref_name)
    mechanical = _mechanical_authority_signal(repo, cfg, data, reference, ref_name)
    base = {"reference": ref_name, "reference_sha": reference,
            "reference_source": ("backlog_authority_ref" if ref_name and ref_name == cfg.get("backlog_authority_ref")
                                 else "target_branch" if ref_name else None),
            "pointer": pointer, "mechanical": mechanical}

    if pointer["state"] == AUTHORITY_SUPERSEDED:
        return {**base, "state": AUTHORITY_SUPERSEDED, "reason": pointer["reason"],
                "detail": pointer["detail"]}
    if pointer["state"] == AUTHORITY_CURRENT:
        if mechanical["state"] == MECHANICAL_CHANGED:
            return {**base, "state": AUTHORITY_SUPERSEDED, "reason": "AUTHORITY_CHANGED_WITHOUT_REVISION",
                    "detail": f'{pointer["feature_path"]} still publishes {pointer["current_revision"]} as '
                              f'the revision in force, but the authority files have changed since this '
                              f'contract was bound. {mechanical["detail"]} An edit that did not raise the '
                              f'revision is not something this can wave through; raise the revision, or '
                              f're-bind the contract.'}
        return {**base, "state": AUTHORITY_CURRENT, "reason": None, "detail": None}
    if mechanical["state"] == MECHANICAL_CHANGED:
        return {**base, "state": AUTHORITY_SUPERSEDED, "reason": "MECHANICAL_FLOOR",
                "detail": f'No authority pointer could be read ({pointer["reason"]}), and the mechanical '
                          f'floor found the authority files changed since this contract was bound. '
                          f'{mechanical["detail"]} Publish an authority_pointer on the Feature card so this '
                          f'can be answered properly, or re-bind the contract.'}
    return {**base, "state": AUTHORITY_UNDETERMINED, "reason": pointer["reason"],
            "detail": pointer["detail"] + " The mechanical floor found no change, which is not proof "
                                          "that the revision is still in force."}


def _normalized_source_item(item: dict[str, Any], source: str, evidence_plan_ref: str | None = None) -> dict[str, Any]:
    out = {
        "gate_id": item["gate_id"],
        "stage": item["stage"],
        "mode": item.get("mode"),
        "required_sources": [source],
        "evidence_plan_refs": [],
    }
    if evidence_plan_ref:
        out["evidence_plan_refs"].append(evidence_plan_ref)
    return out


def _stronger_mode(gate_id: str, left: Any, right: Any) -> Any:
    if left == right:
        return left
    ranks = MODE_STRENGTH.get(gate_id)
    if ranks is None:
        raise r.AEError(f"Conflicting modes for custom gate {gate_id}; resolve policy explicitly.")
    _need(left in ranks and right in ranks, f"Cannot compare assurance modes for {gate_id}.")
    return left if ranks[left] >= ranks[right] else right


def resolve_effective_assurance(policy: dict[str, Any], risk_tier: int, story_required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve deterministic project + Tier + Story assurance obligations.

    Obligations are grouped by gate + stage. A Story cannot delete a project obligation;
    if a known built-in gate uses different modes at the same stage, the stricter mode wins.
    Different stages remain separate because pre-merge and post-integration proof may both
    be legitimate requirements.
    """
    validate_assurance_policy(policy, require_approved=True)
    _need(type(risk_tier) is int and risk_tier in (1, 2, 3), "risk_tier must be 1, 2, or 3.")
    custom_ids = set(policy["custom_gates"])
    for i, item in enumerate(story_required):
        validate_gate_obligation(item, index=i)
        _need(item["gate_id"] in BUILTIN_GATES or item["gate_id"] in custom_ids,
              f"Story requires unknown project-defined gate {item['gate_id']}.")

    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    def add(item: dict[str, Any], source: str, evidence_plan_ref: str | None = None) -> None:
        key = (item["gate_id"], item["stage"])
        incoming = _normalized_source_item(item, source, evidence_plan_ref)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = incoming
            return
        existing["mode"] = _stronger_mode(item["gate_id"], existing.get("mode"), item.get("mode"))
        if source not in existing["required_sources"]:
            existing["required_sources"].append(source)
        if evidence_plan_ref and evidence_plan_ref not in existing["evidence_plan_refs"]:
            existing["evidence_plan_refs"].append(evidence_plan_ref)

    for item in policy["project_required_gates"]:
        add(item, "project")
    for item in policy["tier_required_gates"][str(risk_tier)]:
        add(item, "tier")
    for item in story_required:
        add(item, "story", item.get("evidence_plan_ref"))

    stage_order = {"PRE_MERGE": 0, "POST_INTEGRATION": 1, "RELEASE_POINTER": 2}
    return sorted(by_key.values(), key=lambda x: (stage_order[x["stage"]], x["gate_id"], str(x.get("mode"))))


def _attestor_users(policy: dict[str, Any], gate_id: str, mode: Any) -> list[str]:
    mapping = policy.get("human_attestors", {}).get(gate_id)
    if mapping is None:
        return []
    if isinstance(mapping, list):
        return list(mapping)
    if isinstance(mapping, dict) and isinstance(mode, str):
        return list(mapping.get(mode, []))
    return []


def _attestor_readiness(policy: dict[str, Any], contract: dict[str, Any], obligation: dict[str, Any]) -> dict[str, Any]:
    """Resolve whether a required human-capable attestor is configured.

    Phase 3 verifies configuration/identity availability only. It does not create an
    attestation or claim that a person has reviewed the candidate.
    """
    gate = obligation["gate_id"]
    mode = obligation.get("mode")
    owner = contract.get("engineering_owner")
    owner_identity = owner.get("attestation_identity") if isinstance(owner, dict) else None
    allowlisted = _attestor_users(policy, gate, mode)

    if gate == "technical_review":
        return {"kind": "EXISTING_REVIEWER_ROLE", "configured": True, "candidates": []}
    if gate == "traceability_completion":
        return {"kind": "CONTROL_PLANE_OR_TOOL", "configured": True, "candidates": []}
    if gate == "human_understanding":
        _need(bool(owner_identity), "human_understanding requires a resolved human engineering owner identity.")
        return {"kind": "ENGINEERING_OWNER", "configured": True, "candidates": [owner_identity]}
    if gate == "architecture_review":
        if mode == "CONFORMANCE" and owner_identity:
            candidates = list(dict.fromkeys([owner_identity, *allowlisted]))
            return {"kind": "ENGINEERING_OWNER_OR_AUTHORIZED_HUMAN", "configured": True, "candidates": candidates}
        _need(bool(allowlisted), f"architecture_review {mode} requires an authorized human attestor in project assurance policy.")
        return {"kind": "AUTHORIZED_HUMAN", "configured": True, "candidates": allowlisted}
    if gate == "security_review":
        _need(bool(allowlisted), f"security_review {mode} requires project-designated human attestor(s).")
        return {"kind": "COMPOSITE", "configured": True, "candidates": allowlisted}
    if gate == "operability_recovery":
        candidates = list(dict.fromkeys(([owner_identity] if owner_identity else []) + allowlisted))
        _need(bool(candidates), "operability_recovery requires an engineering owner or authorized project attestor.")
        return {"kind": "ENGINEERING_OWNER_OR_AUTHORIZED_HUMAN", "configured": True, "candidates": candidates}
    if gate == "nfr_performance":
        if mode == "COMPOSITE":
            _need(bool(allowlisted), "nfr_performance COMPOSITE requires an authorized human attestor.")
            return {"kind": "COMPOSITE", "configured": True, "candidates": allowlisted}
        return {"kind": "TOOL_OR_POLICY_DRIVEN", "configured": True, "candidates": allowlisted}
    if gate.startswith("project_defined:"):
        body = custom_gate_runtime_definition(policy, gate, stage=obligation["stage"], mode=mode)
        attestor_class=body["attestor_class"]
        candidates=list(allowlisted)
        if attestor_class=="ENGINEERING_OWNER":
            _need(bool(owner_identity), f"Custom gate {gate} requires the engineering owner identity.")
            candidates=[owner_identity]
        elif attestor_class in ("AUTHORIZED_HUMAN","COMPOSITE"):
            _need(bool(candidates), f"Custom gate {gate} requires configured human attestor(s).")
        return {"kind": "PROJECT_DEFINED", "configured": True, "candidates": candidates, "policy_ref": body.get("policy_ref"), "runtime_definition": body}
    raise r.AEError(f"Unsupported assurance gate {gate}.")


CHECK_APPLIED = "APPLIED"
CHECK_NOT_APPLICABLE = "NOT_APPLICABLE"
CHECK_CONFIGURATION_GAP = "CONFIGURATION_GAP"


def validate_merge_policy_checks(policy: Any) -> dict[str, Any]:
    """Validate the project schema 5 check configuration.

    Two lists with different authority, which is the whole point:

    * `mandatory_checks` — always required, of every Story, derived from nothing. **A
      Story cannot affect this list in any way.** Whatever a project required before the
      upgrade lands here, so an upgrade can never reduce enforcement that was in force.
    * `default_checks` — required only of a Story whose acceptance evidence can satisfy
      them, resolved through `check_evidence_kinds`.

    In v0.6 there was one list, `required_checks`, demanded of every Story whether or not
    the Story could possibly produce the evidence. The pilot met that directly: a Story
    with no automated tests had to be let through by deleting `focused_tests` from the
    project policy, weakening it for **every** Story rather than for the one that could
    not satisfy it.
    """
    _need(isinstance(policy, dict), "merge_policy must be an object.")
    mandatory = policy.get("mandatory_checks")
    default = policy.get("default_checks")
    mapping = policy.get("check_evidence_kinds")
    _need(isinstance(mandatory, list) and all(_is_check(x) for x in mandatory),
          "merge_policy.mandatory_checks must be a list of check names.")
    _need(isinstance(default, list) and all(_is_check(x) for x in default),
          "merge_policy.default_checks must be a list of check names.")
    _need(len(set(mandatory)) == len(mandatory) and len(set(default)) == len(default),
          "Check lists must not repeat a name.")
    overlap = sorted(set(mandatory) & set(default))
    _need(not overlap, f"A check is either mandatory or derived, never both: {overlap}.")
    _need(isinstance(mapping, dict), "merge_policy.check_evidence_kinds must be an object.")
    for check, kinds in mapping.items():
        _need(_is_check(check), f"check_evidence_kinds key {check!r} is not a check name.")
        _need(isinstance(kinds, list) and kinds and all(isinstance(k, str) and k.strip() for k in kinds),
              f"check_evidence_kinds[{check}] must be a non-empty list of evidence kinds.")
    return {"mandatory": list(mandatory), "default": list(default), "mapping": dict(mapping)}


def _is_check(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{1,63}", value))


def effective_required_checks(cfg: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Resolve which named checks this Story must actually satisfy, and say why.

    Deterministic, and derived from the Story's own acceptance evidence:

    * every `mandatory_checks` entry is `APPLIED`, with reason `PROJECT_MANDATORY`. The
      Story is not consulted;
    * a `default_checks` entry is `APPLIED` when some acceptance criterion expects
      evidence of a kind the project maps to it, naming the AC and the kind;
    * otherwise it is `NOT_APPLICABLE`, naming the kinds that would have applied it;
    * a `default_checks` entry with **no** `check_evidence_kinds` mapping is a
      `CONFIGURATION_GAP`. It is never treated as non-applicable and never silently
      skipped — an unmapped check is a question nobody answered, not a decision.

    A Story influences only whether a *derived* check applies. It can neither add a check
    nor remove a mandatory one.
    """
    policy = validate_merge_policy_checks(cfg.get("merge_policy"))
    expectations = contract.get("evidence_expectations") or []
    by_kind: dict[str, list[str]] = {}
    for item in expectations:
        by_kind.setdefault(item["kind"], []).append(item["ac_id"])

    resolved: list[dict[str, Any]] = []
    for check in policy["mandatory"]:
        resolved.append({"check": check, "status": CHECK_APPLIED, "source": "project",
                         "reason": "PROJECT_MANDATORY", "evidence_kinds": [], "ac_ids": [],
                         "detail": "Required of every Story by project policy; the Story is not consulted."})
    for check in policy["default"]:
        kinds = policy["mapping"].get(check)
        if kinds is None:
            resolved.append({"check": check, "status": CHECK_CONFIGURATION_GAP, "source": "project",
                             "reason": "NO_EVIDENCE_KIND_MAPPING", "evidence_kinds": [], "ac_ids": [],
                             "detail": f"'{check}' is a default check with no check_evidence_kinds entry, "
                                       f"so whether it applies to this Story cannot be derived. Map it, or "
                                       f"move it to mandatory_checks. It is not skipped."})
            continue
        matched = sorted({ac for kind in kinds for ac in by_kind.get(kind, [])})
        applying = sorted({kind for kind in kinds if by_kind.get(kind)})
        if matched:
            resolved.append({"check": check, "status": CHECK_APPLIED, "source": "story_evidence",
                             "reason": "EVIDENCE_EXPECTED", "evidence_kinds": applying, "ac_ids": matched,
                             "detail": f"Acceptance criteria {', '.join(matched)} expect evidence of kind "
                                       f"{', '.join(applying)}, which this project maps to '{check}'."})
        else:
            resolved.append({"check": check, "status": CHECK_NOT_APPLICABLE, "source": "story_evidence",
                             "reason": "NO_MATCHING_EVIDENCE", "evidence_kinds": sorted(kinds), "ac_ids": [],
                             "detail": f"No acceptance criterion expects evidence of kind "
                                       f"{', '.join(sorted(kinds))}, so '{check}' cannot be satisfied by this "
                                       f"Story and is not required of it."})

    gaps = [x for x in resolved if x["status"] == CHECK_CONFIGURATION_GAP]
    return {
        "required": [x["check"] for x in resolved if x["status"] == CHECK_APPLIED],
        "not_applicable": [x["check"] for x in resolved if x["status"] == CHECK_NOT_APPLICABLE],
        "configuration_gaps": [x["check"] for x in gaps],
        "resolution": resolved,
        "mandatory_checks": policy["mandatory"],
        "notice": None if not gaps else
                  "One or more default checks have no evidence-kind mapping. Resolve them before merging; "
                  "they are not treated as non-applicable.",
    }


def _verify_attestor_identities(repo: Path, cfg: dict[str, Any], effective: list[dict[str, Any]]) -> dict[str, Any]:
    """Annotate each obligation's attestor readiness with what the forge knows.

    v0.6 resolved *which* identity was configured for a gate and never checked that the
    identity existed. A Story with an engineering owner whose account name was wrong --
    a typo, a person who left, a display name where a username belonged -- passed
    precheck, leased a lane, built, was reviewed, and only failed at the attestation
    step, having consumed a full cycle. That happened in the v0.6 pilot.

    Two properties this must keep:

    * **Existence is not authorization.** The project's approved allow-lists remain the
      only authority on who may attest. This removes identities that could never act;
      it never adds one.
    * **Degradation is explicit.** Where the forge cannot be asked, every readiness
      block says `forge_verified: false` with the reason recorded once at the top
      level, and nothing is blocked. An unasked question must never look like a
      satisfied one, and a Story must never be stopped by a network condition.
    """
    candidates_by_gate = [
        (obligation, [c for c in obligation["attestor_readiness"]["candidates"] if isinstance(c, str) and c.strip()])
        for obligation in effective
    ]
    identity = r.forge_identity_check(repo, cfg, [c for _, names in candidates_by_gate for c in names])
    known, unknown = set(identity["known"]), set(identity["unknown"])

    blocking: list[dict[str, Any]] = []
    for obligation, names in candidates_by_gate:
        readiness = obligation["attestor_readiness"]
        if not names:
            # Nothing was asked, because nothing human is named here -- technical_review
            # and traceability_completion, for instance. null rather than false, so a
            # reader is not left thinking a check failed.
            readiness["forge_verified"] = None
            readiness["unknown_identities"] = []
            continue
        readiness["forge_verified"] = bool(identity["verified"])
        readiness["unknown_identities"] = sorted(n for n in names if n in unknown)
        if identity["verified"] and not any(n in known for n in names):
            blocking.append({
                "gate_id": obligation["gate_id"],
                "stage": obligation["stage"],
                "mode": obligation.get("mode"),
                "reason": "NO_RESOLVABLE_ATTESTOR",
                "attestor_kind": readiness["kind"],
                "candidates": list(names),
                "detail": (
                    f"No configured attestor for {obligation['gate_id']} exists on "
                    f"{cfg['forge']['provider']}: {', '.join(names)}. The gate is required and "
                    f"could not be closed by anyone."
                ),
            })

    return {
        "blocking": blocking,
        "summary": {
            "verified": bool(identity["verified"]),
            "provider": (cfg.get("forge") or {}).get("provider"),
            "known_identities": sorted(known),
            "unknown_identities": sorted(unknown),
            "reason": identity["reason"],
            "notice": (
                None if identity["verified"]
                else "Attestor identities were not verified against the forge. This is reported, "
                     "not treated as success, and it does not block: local work stays possible "
                     "without a reachable forge."
            ),
        },
    }


def effective_merge_mode(cfg: dict[str, Any], contract: dict[str, Any]) -> str:
    project = cfg.get("merge_mode")
    _need(project in ("AGENT_MERGE", "DEVELOPER_REVIEW"), "Project merge mode is invalid.")
    override = contract.get("merge_mode_override")
    _need(override in (None, "DEVELOPER_REVIEW"), "Story merge override may only narrow to DEVELOPER_REVIEW.")
    return "DEVELOPER_REVIEW" if project == "DEVELOPER_REVIEW" or override == "DEVELOPER_REVIEW" else "AGENT_MERGE"


def assurance_precheck(repo: str | Path, contract_data: dict[str, Any], *, developer: str | None = None, sprint: str | None = None) -> dict[str, Any]:
    """Read-only Phase-3 contract/assurance precheck.

    This is one load-bearing part of /ae-start. A READY result means the preparation
    contract and assurance obligations can safely enter the existing v0.4-style runtime
    preflight. It is deliberately not the final AUTHORIZED_TO_START verdict because
    target freshness, dependency/hotspot, baseline tests, writer capacity and pool state
    are checked by the existing Control Plane preflight and later Phase-4 manifest path.
    """
    repo = r.root(Path(repo))
    cfg = r.config(repo)
    r.config_ready(cfg)
    validate_contract(contract_data)
    if developer is not None:
        _ident(developer, "developer")
        _need(contract_data["assigned_developer"] == developer, "Story contract assigned_developer does not match requested developer.")
    if sprint is not None:
        _ident(sprint, "sprint")
        _need(contract_data["sprint"] == sprint, "Story contract sprint does not match requested sprint.")

    verified_blobs = verify_contract_publication(repo, contract_data)
    material = sorted(k for k, value in contract_data["engineering_impact"].items() if value == "MATERIAL")
    if material:
        _need(contract_data.get("engineering_owner") is not None,
              "Material engineering impact requires a resolved human engineering owner before runtime start.")

    policy = cfg["assurance_policy"]
    effective = resolve_effective_assurance(policy, contract_data["risk_tier"], contract_data["story_required_assurance"])
    for obligation in effective:
        readiness=_attestor_readiness(policy, contract_data, obligation)
        obligation["attestor_readiness"] = readiness
        if obligation["gate_id"].startswith("project_defined:"):
            obligation["runtime_definition"]=copy.deepcopy(readiness["runtime_definition"])
        obligation["virtual"] = obligation["gate_id"] == "technical_review"

    identity = _verify_attestor_identities(repo, cfg, effective)
    authority = check_authority_currency(repo, cfg, contract_data)
    blocking = list(identity["blocking"])
    if authority["state"] == AUTHORITY_SUPERSEDED:
        blocking.append({
            "gate_id": None,
            "stage": None,
            "mode": None,
            "reason": "AUTHORITY_SUPERSEDED",
            "detail": authority["detail"],
            "authority_reason": authority["reason"],
            "changes": authority["mechanical"]["changes"],
        })

    by_stage = {stage: [] for stage in ("PRE_MERGE", "POST_INTEGRATION", "RELEASE_POINTER")}
    for obligation in effective:
        by_stage[obligation["stage"]].append(obligation)

    return {
        "result": "CONTRACT_PRECHECK_BLOCKED" if blocking else "CONTRACT_PRECHECK_READY",
        "story_id": contract_data["story_id"],
        "developer": contract_data["assigned_developer"],
        "sprint": contract_data["sprint"],
        "risk_tier": contract_data["risk_tier"],
        "contract_digest": contract_digest(contract_data),
        "authority_publication_sha": contract_data["publication"]["canonical_backlog"]["publication_sha"],
        "verified_blobs": verified_blobs,
        "traceability_root": list(contract_data["traceability_root"]),
        "material_impact": material,
        "engineering_owner": contract_data.get("engineering_owner"),
        "effective_merge_mode": effective_merge_mode(cfg, contract_data),
        "effective_assurance": by_stage,
        "assurance_policy_ref": policy["approval_ref"],
        "forge_identity_check": identity["summary"],
        "authority_currency": authority,
        "blocking": blocking,
        "execution_verdict": "BLOCKED_UNSATISFIABLE_ASSURANCE" if blocking else "PENDING_EXISTING_RUNTIME_PREFLIGHT",
        "next_required_checks": [
            "current target/base freshness",
            "dependency and hotspot state",
            "baseline build/test disposition",
            "writer capacity and reusable pool state",
            "remote effects required by the actual Story",
        ],
        "notice": (
            "This Story may not start: " + "; ".join(x["detail"] for x in blocking) +
            " No lane was leased and no task manifest was created."
            if blocking else
            "Phase-3 contract/assurance precheck only; it does not lease a lane, create a schema-3 task manifest, run BUILD, or by itself authorize execution."
        ),
    }



def handoff_generate_file(repo: str | Path, story_path: str, *, developer: str, sprint: str,
                          publication_sha: str, overrides: dict[str, Any] | None = None,
                          apply: bool = False) -> dict[str, Any]:
    """Derive a normalized contract from a Story card and register it. Preview by default.

    The contract becomes a derived artifact: hand-authoring it stops being the supported
    path, which is what removes the per-Story manual translation the v0.6 pilot needed.

    An existing registered contract is never silently replaced. Identical output is
    reported as current; different output is refused, because a contract already bound to
    a task manifest must not change underneath it.
    """
    import handoff
    repo_root = r.root(Path(repo))
    data = handoff.generate_contract(repo_root, story_path, developer=developer, sprint=sprint,
                                     publication_sha=publication_sha, overrides=overrides)
    path = registered_contract_path(repo_root, developer, data["story_id"])
    rel = path.resolve().relative_to(repo_root).as_posix()
    digest = contract_digest(data)
    base = {"story_id": data["story_id"], "developer": developer, "sprint": sprint,
            "contract_ref": rel, "contract_digest": digest, "risk_tier": data["risk_tier"],
            "source_card": story_path, "publication_sha": publication_sha}
    if path.exists():
        existing = r.read_json(path)
        if existing == data:
            return {**base, "result": "CONTRACT_CURRENT", "mode": "NO_CHANGE",
                    "notice": "The registered contract already matches what this Story card generates."}
        # Name the differing fields. A refusal that only says "differs" sends the reader
        # to diff two JSON files by hand, and the interesting answer is almost always one
        # reference that was translated differently.
        differing = sorted(k for k in set(existing) | set(data) if existing.get(k) != data.get(k))
        return {**base, "result": "CONTRACT_DIFFERS", "mode": "REFUSED",
                "existing_digest": contract_digest(existing) if existing.get("story_id") else None,
                "differing_fields": differing,
                "differences": {k: {"registered": existing.get(k), "generated": data.get(k)}
                                for k in differing},
                "notice": "A different contract is already registered for this Story. It may already be "
                          "bound to a task manifest, so it is not replaced. Reconcile explicitly."}
    if not apply:
        return {**base, "result": "CONTRACT_PREVIEW", "mode": "PREVIEW_ONLY", "contract": data,
                "notice": "Nothing was written. Re-run with --apply to register this contract."}
    path.parent.mkdir(parents=True, exist_ok=True)
    r.ensure_no_links(path.parent, repo_root)
    r.atomic_json(path, data)
    return {**base, "result": "CONTRACT_REGISTERED", "mode": "APPLIED",
            "notice": "Registering a contract is not an assignment: precheck and a BUILD lease still apply."}


def registered_contract_path(repo: str | Path, developer: str, story: str) -> Path:
    """Return the exact local-only normalized Story contract path for a Story."""
    repo = r.root(Path(repo))
    _ident(developer, "developer")
    _ident(story, "story")
    path = repo / ".agentic/local/contracts" / f"{developer}-{story}.json"
    r.ensure_no_links(path, repo)
    return path


def load_registered_contract(repo: str | Path, developer: str, story: str) -> tuple[Path, dict[str, Any]]:
    """Load the deterministic normalized contract from its registered local path."""
    path = registered_contract_path(repo, developer, story)
    _need(path.is_file(), f"Normalized Story contract not found at {path.relative_to(r.root(Path(repo))).as_posix()}. Run contract precheck/normalization before BUILD lease.")
    data = load_contract(str(path))
    _need(data["story_id"] == story, "Registered contract Story ID does not match requested Story.")
    _need(data["assigned_developer"] == developer, "Registered contract assigned_developer does not match requested developer.")
    return path, data


def _material_impact(data: dict[str, Any]) -> list[str]:
    return sorted(k for k, value in data["engineering_impact"].items() if value == "MATERIAL")


def task_contract_snapshot(repo: str | Path, contract_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Create the bounded contract snapshot stored in task schema 3."""
    repo = r.root(Path(repo))
    validate_contract(data)
    rel = contract_path.resolve().relative_to(repo).as_posix()
    publication = data["publication"]
    return {
        # Schema 2: the snapshot now carries the Story's acceptance evidence, so check
        # applicability is resolved from what was bound at BUILD time and cannot drift
        # afterwards. Project policy stays current; the Story's side does not.
        "schema_version": 2,
        "contract_ref": rel,
        "contract_digest": contract_digest(data),
        "canonical_backlog_ref": publication["canonical_backlog"]["path"],
        "backlog_revision": publication["canonical_backlog"]["revision"],
        "authority_publication_sha": publication["canonical_backlog"]["publication_sha"],
        "story_ref": publication["story_card"]["path"],
        "traceability_root": list(data["traceability_root"]),
        "ac_ids": list(data["ac_ids"]),
        "engineering_owner": copy.deepcopy(data.get("engineering_owner")),
        "material_impact": _material_impact(data),
        "capability_id": data.get("capability_id"),
        "final_acceptance_ref": data["final_acceptance_ref"],
        "evidence_expectations": copy.deepcopy(data.get("evidence_expectations") or []),
    }


def _built_in_gate_runtime_defaults(contract_data: dict[str, Any], obligation: dict[str, Any]) -> tuple[str, str]:
    """Return (attestor_class, binding_class) for built-in PRE_MERGE state creation.

    Phase 4 creates schema/state only. Collection/verification of evidence and human
    attestations lands in later phases.
    """
    gate = obligation["gate_id"]
    mode = obligation.get("mode")
    owner = contract_data.get("engineering_owner")
    if gate == "architecture_review":
        if mode == "CONFORMANCE" and owner:
            return "ENGINEERING_OWNER", "CANDIDATE"
        return "AUTHORIZED_HUMAN", "CANDIDATE"
    if gate == "security_review":
        return "COMPOSITE", "CANDIDATE"
    if gate == "human_understanding":
        return "ENGINEERING_OWNER", "CANDIDATE"
    if gate == "nfr_performance":
        return ("COMPOSITE" if mode == "COMPOSITE" else "TOOL"), "CANDIDATE"
    if gate == "operability_recovery":
        return ("ENGINEERING_OWNER" if owner else "AUTHORIZED_HUMAN"), "CANDIDATE"
    if gate == "traceability_completion":
        return "CONTROL_PLANE", "CANDIDATE"
    if gate == "technical_review":
        return "INDEPENDENT_REVIEWER", "CANDIDATE"
    raise r.AEError(f"Custom PRE_MERGE gate {gate} needs explicit runtime semantics; Phase 5 generic assurance registry will enforce custom gate definitions.")


def initial_gate_state(contract_data: dict[str, Any], obligation: dict[str, Any], digest: str) -> dict[str, Any]:
    """Create an unevaluated generic gate state for a required PRE_MERGE gate."""
    if obligation["gate_id"].startswith("project_defined:"):
        definition=obligation.get("runtime_definition")
        _need(isinstance(definition,dict), f"Custom PRE_MERGE gate {obligation['gate_id']} requires runtime semantics from project policy.")
        attestor_class=definition["attestor_class"]
        binding_class=definition["binding_class"]
        _need(binding_class in ("CONTRACT","CANDIDATE"), "PRE_MERGE custom gate must bind to CONTRACT or CANDIDATE.")
    else:
        attestor_class, binding_class = _built_in_gate_runtime_defaults(contract_data, obligation)
    return {
        "gate_id": obligation["gate_id"],
        "stage": obligation["stage"],
        "mode": obligation.get("mode"),
        "required_sources": list(obligation.get("required_sources", [])),
        "attestor_class": attestor_class,
        "binding_class": binding_class,
        "status": "PENDING",
        "binding": {"contract_digest": digest, "candidate_sha": None},
        "evidence_refs": [],
        "attestor_ref": None,
        "observed_at": None,
        "revalidation": {
            "disposition": "NOT_EVALUATED",
            "from_binding": None,
            "to_binding": None,
            "assessor_class": None,
            "assessor_ref": None,
        },
        "waiver_ref": None,
        "residual_risk_refs": [],
    }


def initial_task_assurance(contract_data: dict[str, Any], precheck: dict[str, Any]) -> dict[str, Any]:
    """Build the schema-1 assurance block embedded in task schema 3."""
    digest = precheck["contract_digest"]
    policy_effective = [*precheck["effective_assurance"]["PRE_MERGE"], *precheck["effective_assurance"]["POST_INTEGRATION"], *precheck["effective_assurance"]["RELEASE_POINTER"]]
    project_required = sorted({x["gate_id"] for x in policy_effective if any(src in x.get("required_sources", []) for src in ("project", "tier"))})
    story_required = sorted({x["gate_id"] for x in policy_effective if "story" in x.get("required_sources", [])})
    pre_merge: dict[str, Any] = {}
    for obligation in precheck["effective_assurance"]["PRE_MERGE"]:
        if obligation.get("virtual") or obligation["gate_id"] == "technical_review":
            continue
        _need(obligation["gate_id"] not in pre_merge, "Multiple PRE_MERGE obligations for the same gate are not representable in task schema 3.")
        pre_merge[obligation["gate_id"]] = initial_gate_state(contract_data, obligation, digest)

    downstream: list[dict[str, Any]] = []
    for stage in ("POST_INTEGRATION", "RELEASE_POINTER"):
        for obligation in precheck["effective_assurance"][stage]:
            downstream.append({
                "gate_id": obligation["gate_id"],
                "stage": obligation["stage"],
                "mode": obligation.get("mode"),
                "capability_id": contract_data.get("capability_id"),
                "required_sources": list(obligation.get("required_sources", [])),
                "evidence_plan_refs": list(obligation.get("evidence_plan_refs", [])),
                "registry_ref": None,
            })
    return {
        "schema_version": 1,
        "policy_snapshot": {
            "approval_ref": precheck["assurance_policy_ref"],
            "resolved_at": r.now(),
            "project_required": project_required,
            "story_required": story_required,
        },
        "pre_merge": pre_merge,
        "downstream_obligations": downstream,
    }


def initial_traceability_state() -> dict[str, Any]:
    return {"record_ref": None, "capability_record_ref": None, "release_pointer_refs": []}

def migrate_project_config_file_v04(project_config: str | Path, *, apply: bool=False) -> dict[str, Any]:
    """Preview/apply the explicit schema-2 -> schema-4 project config migration.

    Apply never approves assurance. Existing standing merge/remote/cleanup decisions are
    preserved exactly by `migrate_project_config_v04`; the v0.4 bytes are backed up under
    .agentic/local before atomic replacement.
    """
    path=Path(project_config).resolve()
    _need(path.name=='project.json' and path.parent.name=='.agentic', 'Project migration must target the repository .agentic/project.json file.')
    repo=r.root(path.parent.parent)
    _need(path==repo/'.agentic/project.json','Project migration target must be the exact repository .agentic/project.json.')
    source=r.read_json(path);proposed=migrate_project_config_v04(source)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    backup=repo/'.agentic/local/migrations'/f'project-schema2-{digest[:16]}.json'
    out={
        'result':'PROJECT_MIGRATION_APPLIED' if apply else 'PROJECT_MIGRATION_PREVIEW',
        'source_schema':2,'target_schema':PROJECT_SCHEMA_VERSION,'kit_version':KIT_VERSION,
        'assurance_policy_approved':False,'backup_ref':backup.relative_to(repo).as_posix(),
        'proposed_config':proposed,
        'notice':'Assurance policy remains unapproved. Maintainer must review/approve it explicitly before runtime readiness; standing merge/remote/cleanup approvals were not changed.',
    }
    if apply:
        _need(not r.git(repo,'status','--porcelain=v1','--untracked-files=all')[1], 'Repository must be clean before project config migration. Preserve current work first; do not reset/clean to bypass this gate.')
        pool_state_path=r.common(repo)/'agentic/pool.json'
        if pool_state_path.exists():
            pool_state=r.read_json(pool_state_path)
            active=[x.get('lease') for x in pool_state.get('lanes',{}).values() if x.get('lease')]
            _need(not active,'Release all active Control Plane lanes before project config migration.')
        backup.parent.mkdir(parents=True,exist_ok=True);r.ensure_no_links(backup.parent,repo)
        raw=path.read_bytes()
        if backup.exists():_need(backup.read_bytes()==raw,'Existing project migration backup differs; stop and inspect history.')
        else:backup.write_bytes(raw)
        r.atomic_json(path,proposed)
    return out

def upgrade_legacy_task_v04(repo: str | Path, task_file: str | Path, *, apply: bool=False) -> dict[str, Any]:
    """Explicitly bind an existing schema-2 task to a real normalized contract and schema 3.

    Existing review/test/merge evidence is preserved byte-for-byte in a local migration
    backup and field-for-field in the upgraded task. New assurance starts PENDING; nothing
    is inferred as PASS from legacy evidence.
    """
    repo=r.root(Path(repo));cfg=r.config(repo);r.config_ready(cfg)
    task_path,legacy=r.load_task(repo,task_file)
    _need(legacy.get('schema_version')==2,'Task upgrade accepts only an existing v0.4 schema-2 task.')
    # No migration while any lane still owns the Story. PREP may be used before this step,
    # but the actual conversion is serialized against active runtime leases.
    state_path=r.common(repo)/'agentic/pool.json'
    if state_path.exists():
        pool_state=r.read_json(state_path)
        active=[x.get('lease') for x in pool_state.get('lanes',{}).values() if x.get('lease')]
        _need(not any(x.get('task_id')==legacy['task_id'] for x in active),'Release every active lane for this Story before task migration.')
    contract_path,contract_data=load_registered_contract(repo,legacy['developer'],legacy['task_id'])
    precheck=assurance_precheck(repo,contract_data,developer=legacy['developer'])
    _need(precheck.get('result')=='CONTRACT_PRECHECK_READY','Normalized Story contract is not ready for explicit legacy binding.')
    if legacy.get('candidate_sha') is not None:
        _need(contract_data['risk_tier']==legacy['risk_tier'],'In-flight legacy candidate risk Tier differs from the new contract. Reconcile/re-review explicitly instead of silently changing Tier during migration.')
    old_override=legacy.get('merge_mode_override')
    new_override=contract_data.get('merge_mode_override')
    _need(not (old_override=='DEVELOPER_REVIEW' and new_override is None),'Normalized contract would broaden a legacy DEVELOPER_REVIEW restriction. Keep DEVELOPER_REVIEW in the contract.')
    upgraded=copy.deepcopy(legacy)
    upgraded['schema_version']=3
    upgraded['risk_tier']=contract_data['risk_tier']
    upgraded['merge_mode_override']=new_override
    upgraded['contract']=task_contract_snapshot(repo,contract_path,contract_data)
    upgraded['assurance']=initial_task_assurance(contract_data,precheck)
    upgraded['traceability']=initial_traceability_state()
    candidate=upgraded.get('candidate_sha')
    if candidate is not None:
        for gate in upgraded['assurance']['pre_merge'].values():
            if gate.get('binding_class')=='CANDIDATE':gate['binding']['candidate_sha']=candidate
    r.validate_task_schema3(upgraded)
    digest=hashlib.sha256(task_path.read_bytes()).hexdigest()
    backup=repo/'.agentic/local/migrations/tasks'/f"{legacy['developer']}-{legacy['task_id']}-schema2-{digest[:16]}.json"
    out={
        'result':'LEGACY_TASK_UPGRADED' if apply else 'LEGACY_TASK_UPGRADE_PREVIEW',
        'task_id':legacy['task_id'],'developer':legacy['developer'],'source_schema':2,'target_schema':3,
        'contract_digest':upgraded['contract']['contract_digest'],
        'pre_merge_assurance':sorted(upgraded['assurance']['pre_merge']),
        'downstream_assurance':[x['gate_id'] for x in upgraded['assurance']['downstream_obligations']],
        'legacy_evidence_preserved':True,'backup_ref':backup.relative_to(repo).as_posix(),
        'notice':'No legacy evidence was converted into assurance PASS. New mandatory assurance remains explicit in schema 3.',
    }
    if apply:
        backup.parent.mkdir(parents=True,exist_ok=True);r.ensure_no_links(backup.parent,repo)
        raw=task_path.read_bytes()
        if backup.exists():_need(backup.read_bytes()==raw,'Existing task migration backup differs; stop and inspect history.')
        else:backup.write_bytes(raw)
        r.atomic_json(task_path,upgraded)
    return out

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c_validate = sub.add_parser("validate", help="validate normalized Story contract")
    c_validate.add_argument("contract")
    c_digest = sub.add_parser("digest", help="validate and print normalized Story contract digest")
    c_digest.add_argument("contract")
    p_validate = sub.add_parser("project-validate", help="validate v0.6 schema-4 project configuration")
    p_validate.add_argument("project_config")
    p_migrate = sub.add_parser("project-migrate-preview", help="emit a no-write v0.4 -> v0.6 project config migration preview")
    p_migrate.add_argument("project_config")
    p_migrate_apply = sub.add_parser("project-migrate-v04", help="preview/apply explicit v0.4 project config migration; assurance remains unapproved")
    p_migrate_apply.add_argument("project_config")
    p_migrate_apply.add_argument("--apply", action="store_true")
    t_migrate = sub.add_parser("task-upgrade-v04", help="preview/apply explicit schema-2 task binding to a registered normalized contract")
    t_migrate.add_argument("task")
    t_migrate.add_argument("--repo", default=".")
    t_migrate.add_argument("--apply", action="store_true")
    c_precheck = sub.add_parser("precheck", help="run read-only Story contract + assurance readiness precheck")
    c_precheck.add_argument("contract")
    c_precheck.add_argument("--repo", default=".")
    c_precheck.add_argument("--developer")
    c_precheck.add_argument("--sprint")
    h_check = sub.add_parser("handoff-check", help="can this Story card produce a valid normalized contract?")
    h_check.add_argument("story")
    h_check.add_argument("--repo", default=".")
    h_check.add_argument("--ref", action="append", default=[], metavar="FIELD=VALUE",
                         help="supply a reference a legacy Story card has no source for")
    h_gen = sub.add_parser("handoff-generate", help="derive the normalized Story contract from a Story card")
    h_gen.add_argument("story")
    h_gen.add_argument("--repo", default=".")
    h_gen.add_argument("--developer", required=True)
    h_gen.add_argument("--sprint", required=True)
    h_gen.add_argument("--publication-sha", required=True)
    h_gen.add_argument("--ref", action="append", default=[], metavar="FIELD=VALUE")
    h_gen.add_argument("--apply", action="store_true", help="register the contract; preview by default")
    args = p.parse_args()
    try:
        if args.command in ("validate", "digest"):
            data = load_contract(args.contract)
            out = {"result": "CONTRACT_VALID", "schema_version": CONTRACT_SCHEMA_VERSION}
            if args.command == "digest": out["contract_digest"] = contract_digest(data)
        elif args.command == "project-validate":
            cfg = r.read_json(args.project_config)
            _need(cfg.get("schema_version") == PROJECT_SCHEMA_VERSION and cfg.get("kit_version") == KIT_VERSION, "Use Control Plane v0.7 project schema 5.")
            validate_assurance_policy(cfg.get("assurance_policy"))
            out = {"result": "PROJECT_CONFIG_VALID", "schema_version": PROJECT_SCHEMA_VERSION, "kit_version": KIT_VERSION, "assurance_policy_approved": cfg["assurance_policy"]["approved"]}
        elif args.command == "precheck":
            data = load_contract(args.contract)
            out = assurance_precheck(args.repo, data, developer=args.developer, sprint=args.sprint)
        elif args.command in ("handoff-check", "handoff-generate"):
            import handoff
            overrides = {}
            for pair in args.ref:
                field, sep, value = pair.partition("=")
                _need(sep and field.strip() and value.strip(), f"Use --ref FIELD=VALUE, not {pair!r}.")
                overrides[field.strip()] = value.strip()
            if args.command == "handoff-check":
                out = handoff.handoff_check(args.repo, args.story, overrides=overrides)
            else:
                out = handoff_generate_file(args.repo, args.story, developer=args.developer,
                                            sprint=args.sprint, publication_sha=args.publication_sha,
                                            overrides=overrides, apply=args.apply)
        elif args.command == "project-migrate-v04":
            out = migrate_project_config_file_v04(args.project_config, apply=args.apply)
        elif args.command == "task-upgrade-v04":
            out = upgrade_legacy_task_v04(args.repo, args.task, apply=args.apply)
        else:
            cfg = r.read_json(args.project_config)
            proposed = migrate_project_config_v04(cfg)
            out = {
                "result": "MIGRATION_PREVIEW",
                "source_schema": 2,
                "target_schema": PROJECT_SCHEMA_VERSION,
                "proposed_config": proposed,
                "notice": "Preview only. No file was written and assurance_policy remains unapproved until maintainer review.",
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    except (r.AEError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"AE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
