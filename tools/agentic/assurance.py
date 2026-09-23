#!/usr/bin/env python3
"""Generic assurance registry, human attestation, revalidation, and merge closure for Control Plane v0.5 Release Candidate.

This module keeps one generic registry for all assurance gates. Phase 6 adds
server-verifiable human attestation using exact forge pull-request comments and deterministic
binding digests. It does not create specialist security/architecture engines.

Phase boundary:
- tool/control-plane gates can be satisfied when exact binding + evidence are valid;
- ENGINEERING_OWNER / AUTHORIZED_HUMAN / COMPOSITE gates can be satisfied only by
  an exact server-backed AE-ASSURE comment from an authorized forge identity;
- human merge approval remains a separate evidence meaning;
- candidate-bound closed gates are revalidated gate-by-gate after candidate changes;
- auto-first classification is deliberately conservative: exact tree equivalence can auto-reuse, otherwise UNKNOWN until a bounded assessment is recorded;
- human/composite gates reuse prior attestation only for EQUIVALENT/NON_MATERIAL deltas; MATERIAL deltas require targeted fresh attestation/evidence;
- merge.py can now enforce exact PRE_MERGE assurance closure through this module;
- WAIVED remains an explicit human exception; candidate changes require a newly bound waiver rather than silent carry-forward.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

try:
    import runtime as r
    import contract as c
except ModuleNotFoundError:  # pragma: no cover
    from . import runtime as r
    from . import contract as c

OPEN_STATUSES = {"PENDING", "IN_PROGRESS", "FAILED", "BLOCKED"}
REVALIDATION_DISPOSITIONS = {"EQUIVALENT", "NON_MATERIAL_TO_GATE", "MATERIAL_TO_GATE", "UNKNOWN"}
REVALIDATION_ASSESSORS = {"TOOL", "CONTROL_PLANE", "INDEPENDENT_REVIEWER", "ENGINEERING_OWNER", "AUTHORIZED_HUMAN"}
CLOSED_STATUSES = {"SATISFIED", "WAIVED"}
MUTABLE_NON_SUCCESS = {"PENDING", "IN_PROGRESS", "FAILED", "BLOCKED"}
EVIDENCE_REF = re.compile(r"[^\r\n\x00]{1,512}\Z")
ACTOR_REF = re.compile(r"[^\r\n\x00]{1,256}\Z")


def _ref(value: Any, label: str, *, actor: bool = False) -> str:
    pattern = ACTOR_REF if actor else EVIDENCE_REF
    r.need(isinstance(value, str) and value.strip() and pattern.fullmatch(value) is not None and "__CONFIGURE__" not in value,
           f"Invalid {label}.")
    return value.strip()


def _load(repo: Path, task_arg: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    repo = r.require_coordinator(repo)
    cfg = r.config(repo)
    r.config_ready(cfg)
    path, task = r.load_task(repo, task_arg)
    r.need(task.get("schema_version") == 3, "Generic assurance registry requires a v0.5 schema-3 task manifest.")
    return path, task, cfg


def _gate(task: dict[str, Any], gate_id: str) -> dict[str, Any]:
    gates = task.get("assurance", {}).get("pre_merge", {})
    r.need(gate_id in gates, f"PRE_MERGE assurance gate {gate_id} is not required for this Story.")
    gate = gates[gate_id]
    r.need(isinstance(gate, dict), "Assurance gate state is invalid.")
    return gate


def _custom_definition(cfg: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> dict[str, Any] | None:
    if not gate_id.startswith("project_defined:"):
        return None
    return c.custom_gate_runtime_definition(cfg["assurance_policy"], gate_id, stage=gate["stage"], mode=gate.get("mode"))


def _expected_semantics(cfg: dict[str, Any], task: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> dict[str, Any]:
    """Return runtime constraints and reject locally-tampered semantic fields."""
    mode = gate.get("mode")
    owner = task.get("contract", {}).get("engineering_owner")
    if gate_id == "architecture_review":
        expected_attestor = "ENGINEERING_OWNER" if mode == "CONFORMANCE" and owner else "AUTHORIZED_HUMAN"
        expected_binding = "CANDIDATE"
        minimum = 1
    elif gate_id == "security_review":
        expected_attestor, expected_binding, minimum = "COMPOSITE", "CANDIDATE", 1
    elif gate_id == "human_understanding":
        expected_attestor, expected_binding, minimum = "ENGINEERING_OWNER", "CANDIDATE", 0
    elif gate_id == "nfr_performance":
        expected_attestor = "COMPOSITE" if mode == "COMPOSITE" else "TOOL"
        expected_binding, minimum = "CANDIDATE", 1
    elif gate_id == "operability_recovery":
        expected_attestor = "ENGINEERING_OWNER" if owner else "AUTHORIZED_HUMAN"
        expected_binding, minimum = "CANDIDATE", 1
    elif gate_id == "traceability_completion":
        expected_attestor, expected_binding, minimum = "CONTROL_PLANE", "CANDIDATE", 1
    elif gate_id == "technical_review":
        raise r.AEError("technical_review is virtual and must use the existing task.review object, not assurance.pre_merge.")
    elif gate_id.startswith("project_defined:"):
        definition = _custom_definition(cfg, gate_id, gate)
        expected_attestor = definition["attestor_class"]
        expected_binding = definition["binding_class"]
        minimum = definition["minimum_evidence"]
    else:
        raise r.AEError(f"Unsupported assurance gate {gate_id}.")
    r.need(gate.get("attestor_class") == expected_attestor,
           f"Assurance gate {gate_id} attestor_class does not match project/built-in semantics.")
    r.need(gate.get("binding_class") == expected_binding,
           f"Assurance gate {gate_id} binding_class does not match project/built-in semantics.")
    return {"attestor_class": expected_attestor, "binding_class": expected_binding, "minimum_evidence": minimum}


def _binding_ready(task: dict[str, Any], gate: dict[str, Any]) -> None:
    binding = gate["binding"]
    r.need(binding.get("contract_digest") == task["contract"]["contract_digest"], "Assurance gate is bound to a different Story contract.")
    if gate["binding_class"] == "CANDIDATE":
        candidate = task.get("candidate_sha")
        r.need(candidate is not None, "Candidate-bound assurance cannot be completed before an exact candidate exists.")
        r.full(candidate)
        r.need(binding.get("candidate_sha") == candidate, "Assurance gate candidate binding does not match the current task candidate.")
    elif gate["binding_class"] == "CONTRACT":
        r.need(binding.get("candidate_sha") is None or binding.get("candidate_sha") == task.get("candidate_sha"), "Contract-bound gate contains an unexpected candidate binding.")
    else:
        raise r.AEError("Story PRE_MERGE assurance has unsupported binding class.")


def _binding_digest(task: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return deterministic human-attestation digest + canonical payload for one Story gate."""
    _binding_ready(task, gate)
    if gate["binding_class"] == "CANDIDATE":
        bound_version = task["candidate_sha"]
    elif gate["binding_class"] == "CONTRACT":
        bound_version = task["contract"]["authority_publication_sha"]
    else:
        raise r.AEError("Story human attestation supports CONTRACT or CANDIDATE binding only.")
    payload = {
        "scope_type": "STORY",
        "scope_id": task["task_id"],
        "gate_id": gate_id,
        "stage": gate["stage"],
        "mode": gate.get("mode"),
        "contract_digest": task["contract"]["contract_digest"],
        "binding_class": gate["binding_class"],
        "bound_version": bound_version,
    }
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), payload


def _project_attestor_users(cfg: dict[str, Any], gate_id: str, mode: Any) -> list[str]:
    mapping=cfg.get("assurance_policy",{}).get("human_attestors",{}).get(gate_id)
    if isinstance(mapping,list):
        return list(mapping)
    if isinstance(mapping,dict) and isinstance(mode,str):
        return list(mapping.get(mode,[]))
    return []


def _allowed_human_users(cfg: dict[str, Any], task: dict[str, Any], gate_id: str, gate: dict[str, Any], semantics: dict[str, Any]) -> list[str]:
    cls=semantics["attestor_class"]
    if cls == "ENGINEERING_OWNER":
        owner=task.get("contract",{}).get("engineering_owner")
        provider=(cfg.get("forge") or {}).get("provider")
        r.need(isinstance(owner,dict) and owner.get("attestation_provider")==provider,
               f"{gate_id} requires an engineering owner identity backed by the configured "
               f"forge ({provider}); the contract declares "
               f"{(owner or {}).get('attestation_provider')!r}.")
        identity=owner.get("attestation_identity")
        r.need(r.text(identity), f"{gate_id} engineering owner identity is unresolved.")
        return [identity]
    if cls in ("AUTHORIZED_HUMAN","COMPOSITE"):
        users=_project_attestor_users(cfg,gate_id,gate.get("mode"))
        r.need(bool(users), f"{gate_id} requires configured authorized human attestor(s) for mode {gate.get('mode')}.")
        return users
    raise r.AEError(f"Assurance gate {gate_id} does not use server-backed human attestation.")


def _attestation_ref_count(gate: dict[str, Any]) -> int:
    return sum(1 for ref in gate.get("evidence_refs",[]) if not str(ref).startswith("human-attestation:"))


def _assert_gate_semantic_integrity(cfg: dict[str, Any], task: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> dict[str, Any]:
    semantics=_expected_semantics(cfg,task,gate_id,gate)
    if gate.get("status")=="SATISFIED":
        r.need(_attestation_ref_count(gate) >= semantics["minimum_evidence"],
               f"SATISFIED assurance gate {gate_id} does not meet its non-attestation evidence minimum.")
        if semantics["attestor_class"] in ("ENGINEERING_OWNER","AUTHORIZED_HUMAN","COMPOSITE"):
            r.need(_is_attestor_ref(gate.get("attestor_ref")),
                   f"SATISFIED human assurance gate {gate_id} lacks server-backed attestor provenance.")
            r.need(any(_is_attestation_evidence_ref(x) for x in gate.get("evidence_refs",[])),
                   f"SATISFIED human assurance gate {gate_id} lacks a server attestation evidence reference.")
    if gate.get("status")=="WAIVED":
        r.need(isinstance(gate.get("waiver_ref"),str) and gate["waiver_ref"].startswith(".agentic/local/waivers/"),
               f"WAIVED assurance gate {gate_id} lacks a bounded local waiver record reference.")
        r.need(_is_attestor_ref(gate.get("attestor_ref")),
               f"WAIVED assurance gate {gate_id} lacks server-backed risk-owner provenance.")
    return semantics



# Server attestation provenance, one spelling per provider. GitLab keeps the exact v0.5
# wording so records written under v0.5 still parse and compare byte for byte after an
# upgrade; GitHub uses its own nouns. Both carry the same four facts: provider, request
# number, comment id, and the account that wrote it.
ATTESTOR_REF_RE = re.compile(
    r"(?:gitlab:mr:(?P<gl_n>\d+):note:(?P<gl_id>\d+)"
    r"|github:pr:(?P<gh_n>\d+):comment:(?P<gh_id>\d+))"
    r":user:(?P<user>[^:\r\n]+)"
)
ATTESTATION_EVIDENCE_PREFIXES = ("human-attestation:gitlab:mr:", "human-attestation:github:pr:")


def _is_attestor_ref(value: Any) -> bool:
    return isinstance(value, str) and ATTESTOR_REF_RE.fullmatch(value) is not None


def _is_attestation_evidence_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ATTESTATION_EVIDENCE_PREFIXES)


def _parse_attestor_ref(value: Any, task: dict[str, Any]) -> tuple[int, str]:
    ref=_ref(value,"attestor_ref",actor=True)
    m=ATTESTOR_REF_RE.fullmatch(ref)
    r.need(m is not None,"Human assurance attestor_ref is not a supported forge comment reference.")
    number=int(m.group("gl_n") or m.group("gh_n"))
    comment_id=int(m.group("gl_id") or m.group("gh_id"))
    username=m.group("user")
    r.need(type(task.get("mr_iid")) is int and task["mr_iid"]==number,"Human assurance attestation belongs to another pull request.")
    return comment_id,username


def _parse_expiry(value: Any) -> dt.datetime | None:
    if value is None:return None
    r.need(isinstance(value,str) and value.strip(),"Waiver expiry must be null or ISO-8601 text.")
    raw=value.strip().replace("Z","+00:00")
    try: parsed=dt.datetime.fromisoformat(raw)
    except ValueError as e: raise r.AEError("Waiver expiry must be valid ISO-8601.") from e
    r.need(parsed.tzinfo is not None,"Waiver expiry must include a timezone offset.")
    return parsed.astimezone(dt.timezone.utc)


def _current_contract(repo: Path, task: dict[str, Any]) -> dict[str, Any]:
    rel=task.get("contract",{}).get("contract_ref")
    r.need(isinstance(rel,str) and rel and not rel.startswith(("/","\\")) and ".." not in Path(rel).parts,"Task contract_ref is invalid.")
    path=(Path(repo)/rel).absolute();r.ensure_no_links(path,repo);r.need(path.is_file(),"Bound normalized Story contract is missing.")
    data=c.load_contract(str(path))
    r.need(c.contract_digest(data)==task["contract"]["contract_digest"],"Normalized Story contract changed after task binding; rerun preparation/precheck and rebind explicitly.")
    r.need(data["story_id"]==task["task_id"] and data["assigned_developer"]==task["developer"],"Bound Story contract identity differs from task manifest.")
    r.need(data["risk_tier"]==task["risk_tier"],"Bound Story contract risk tier differs from task manifest.")
    r.need(data.get("merge_mode_override")==task.get("merge_mode_override"),"Bound Story contract merge restriction differs from task manifest.")
    c.verify_contract_publication(repo,data)
    return data


def _policy_snapshot_current(cfg: dict[str, Any], task: dict[str, Any]) -> None:
    policy=cfg.get("assurance_policy",{})
    r.need(policy.get("approved") is True and r.text(policy.get("approval_ref")),"Approved assurance policy is required for schema-3 merge evaluation.")
    snapshot=task.get("assurance",{}).get("policy_snapshot",{})
    r.need(snapshot.get("approval_ref")==policy.get("approval_ref"),"Assurance policy changed after Story precheck; rerun assurance-aware precheck/rebind before merge.")



def _effective_assurance_current(cfg: dict[str, Any], task: dict[str, Any], contract_data: dict[str, Any]) -> None:
    """Re-resolve current project+tier+Story obligations and compare to bound task state.

    This catches manual policy edits even when an operator failed to rotate approval_ref.
    """
    effective=c.resolve_effective_assurance(cfg["assurance_policy"],contract_data["risk_tier"],contract_data["story_required_assurance"])
    expected_pre={(x["gate_id"],x["stage"],x.get("mode")) for x in effective if x["stage"]=="PRE_MERGE" and x["gate_id"]!="technical_review"}
    actual_pre={(gid,state["stage"],state.get("mode")) for gid,state in task["assurance"]["pre_merge"].items()}
    r.need(actual_pre==expected_pre,"Effective PRE_MERGE assurance changed after task binding; rerun assurance-aware precheck/rebind before merge.")
    expected_down={(x["gate_id"],x["stage"],x.get("mode")) for x in effective if x["stage"] in ("POST_INTEGRATION","RELEASE_POINTER")}
    actual_down={(x["gate_id"],x["stage"],x.get("mode")) for x in task["assurance"]["downstream_obligations"]}
    r.need(actual_down==expected_down,"Effective downstream assurance changed after task binding; rerun assurance-aware precheck/rebind before merge.")
    technical_expected=any(x["gate_id"]=="technical_review" and x["stage"]=="PRE_MERGE" for x in effective)
    snap=task["assurance"]["policy_snapshot"]
    technical_bound="technical_review" in set(snap.get("project_required",[]))|set(snap.get("story_required",[]))
    r.need(technical_expected==technical_bound,"Virtual technical-review assurance requirement changed after task binding.")

def _verify_satisfied_for_merge(repo: Path, cfg: dict[str, Any], task: dict[str, Any], gate_id: str, gate: dict[str, Any], *, verify_server: bool) -> dict[str, Any]:
    semantics=_assert_gate_semantic_integrity(cfg,task,gate_id,gate);_binding_ready(task,gate)
    out={"gate_id":gate_id,"status":"SATISFIED","mode":gate.get("mode"),"binding_class":gate["binding_class"],"attestor_class":semantics["attestor_class"]}
    if semantics["attestor_class"] in ("ENGINEERING_OWNER","AUTHORIZED_HUMAN","COMPOSITE"):
        note_id,username=_parse_attestor_ref(gate.get("attestor_ref"),task)
        digest,_=_binding_digest(task,gate_id,gate);expected=f"AE-ASSURE {gate_id} {task['task_id']} {digest}"
        allowed=_allowed_human_users(cfg,task,gate_id,gate,semantics)
        r.need(username in allowed,"Recorded assurance attestor is no longer authorized by current project policy.")
        if verify_server:
            proof=r.verify_forge_comment(repo,cfg,task,note_id,expected,allowed,verify_mr=False)
            r.need(proof["attestor_ref"]==gate.get("attestor_ref"),"Server human-attestation provenance no longer matches the recorded gate.")
            r.need(proof["evidence_ref"] in gate.get("evidence_refs",[]),"Recorded gate no longer contains the exact server attestation evidence reference.")
        out.update(binding_digest=digest,attestor_identity=username,note_id=note_id,server_verified=verify_server)
    else:
        r.need(r.text(gate.get("attestor_ref")) and r.text(gate.get("observed_at")),f"SATISFIED assurance gate {gate_id} lacks attestor/time provenance.")
        out.update(attestor_ref=gate.get("attestor_ref"),server_verified=False)
    return out


def _verify_waived_for_merge(repo: Path, cfg: dict[str, Any], task: dict[str, Any], gate_id: str, gate: dict[str, Any], *, verify_server: bool) -> dict[str, Any]:
    _assert_gate_semantic_integrity(cfg,task,gate_id,gate);_binding_ready(task,gate)
    _rule,owners=_waiver_policy(cfg,gate_id)
    rel=gate.get("waiver_ref")
    r.need(isinstance(rel,str) and rel.startswith(".agentic/local/waivers/") and ".." not in Path(rel).parts and Path(rel).suffix==".json","WAIVED gate has an invalid waiver_ref.")
    path=(Path(repo)/rel).absolute();r.ensure_no_links(path,repo);r.need(path.is_file(),"WAIVED gate bounded waiver record is missing.")
    record=r.read_json(path);_validate_waiver_record(record,approval_allowed=True)
    r.need(record.get("approval_ref") is not None,"Waiver record has no server-backed approval reference.")
    digest,_=_binding_digest(task,gate_id,gate)
    r.need(record["scope_type"]=="STORY" and record["gate_id"]==gate_id and record["scope_id"]==task["task_id"] and record["binding_digest"]==digest,"Waiver record is bound to another gate/Story/version.")
    r.need(record["risk_owner"] in owners,"Waiver risk owner is no longer authorized by current project policy.")
    r.need(record["approval_ref"]==gate.get("attestor_ref"),"Waiver approval provenance differs from gate attestor_ref.")
    expiry=_parse_expiry(record.get("expiry"))
    if expiry is not None:r.need(expiry>dt.datetime.now(dt.timezone.utc),"Waiver has expired; obtain a new bounded risk acceptance or satisfy the assurance gate.")
    wdigest=waiver_digest(record);expected=f"AE-WAIVE {gate_id} {task['task_id']} {digest} {wdigest}"
    note_id,username=_parse_attestor_ref(gate.get("attestor_ref"),task)
    r.need(username==record["risk_owner"],"Waiver attestor does not match the authorized risk owner in the waiver record.")
    if verify_server:
        proof=r.verify_forge_comment(repo,cfg,task,note_id,expected,[record["risk_owner"]],verify_mr=False)
        r.need(proof["attestor_ref"]==record["approval_ref"],"Server waiver approval no longer matches the bounded waiver record.")
    return {"gate_id":gate_id,"status":"WAIVED","mode":gate.get("mode"),"waiver_id":record["waiver_id"],"waiver_ref":rel,"risk_owner":record["risk_owner"],"residual_risk":record["residual_risk"],"expiry":record.get("expiry"),"review_condition":record.get("review_condition"),"binding_digest":digest,"server_verified":verify_server}


def validate_for_merge(repo: Path, cfg: dict[str, Any], task: dict[str, Any], *, require_closed: bool, verify_server: bool) -> dict[str, Any]:
    """Validate schema-3 Story contract/policy and assurance for merge decision.

    DEVELOPER_REVIEW may carry open assurance to the human path, while AGENT_MERGE
    calls this with require_closed=True and server-verifies human/waiver proofs.
    """
    repo=r.root(repo);r.validate_task_schema3(task);contract_data=_current_contract(repo,task);_policy_snapshot_current(cfg,task);_effective_assurance_current(cfg,task,contract_data)
    blocking=[];closed=[];exceptions=[]
    for gate_id,gate in sorted(task["assurance"]["pre_merge"].items()):
        status=gate.get("status")
        if status=="SATISFIED":closed.append(_verify_satisfied_for_merge(repo,cfg,task,gate_id,gate,verify_server=verify_server))
        elif status=="WAIVED":
            item=_verify_waived_for_merge(repo,cfg,task,gate_id,gate,verify_server=verify_server);closed.append(item);exceptions.append(item)
        else:
            _expected_semantics(cfg,task,gate_id,gate)
            blocking.append({"gate_id":gate_id,"status":status,"mode":gate.get("mode")})
    if require_closed:
        r.need(not blocking,"Mandatory PRE_MERGE assurance is not closed: "+", ".join(f"{x['gate_id']}={x['status']}" for x in blocking))
    return {"result":"ASSURANCE_PRE_MERGE_CLOSED" if not blocking else "ASSURANCE_PRE_MERGE_PENDING","task_id":task["task_id"],"candidate_sha":task.get("candidate_sha"),"contract_digest":task["contract"]["contract_digest"],"closed":closed,"blocking":blocking,"waived_exceptions":exceptions,"server_verified":verify_server}

def attestation_request(repo: Path, task_arg: Path, gate_id: str) -> dict[str, Any]:
    """Return the exact note a human must post. This performs no mutation and no attestation."""
    _,task,cfg=_load(repo,task_arg)
    gate=_gate(task,gate_id)
    semantics=_expected_semantics(cfg,task,gate_id,gate)
    r.need(semantics["attestor_class"] in ("ENGINEERING_OWNER","AUTHORIZED_HUMAN","COMPOSITE"),
           "This gate is not human/composite; use tool/control-plane evidence semantics instead.")
    r.need(gate["status"] != "NEEDS_REVALIDATION", "Gate needs revalidation before a new human attestation can close it.")
    digest,payload=_binding_digest(task,gate_id,gate)
    users=_allowed_human_users(cfg,task,gate_id,gate,semantics)
    return {
        "result":"HUMAN_ATTESTATION_REQUIRED",
        "task_id":task["task_id"],
        "gate_id":gate_id,
        "stage":gate["stage"],
        "mode":gate.get("mode"),
        "binding_digest":digest,
        "binding":payload,
        "allowed_forge_users":users,
        "expected_note":f"AE-ASSURE {gate_id} {task['task_id']} {digest}",
        "mr_iid":task.get("mr_iid"),
        "minimum_non_attestation_evidence":semantics["minimum_evidence"],
        "notice":"Post the exact one-line note from an allowed human account on the bound Story MR. Human merge approval is separate evidence.",
    }


def satisfy_human(repo: Path, task_arg: Path, gate_id: str, note_id: int, refs: list[str] | None = None, apply: bool = False) -> dict[str, Any]:
    """Verify exact server-backed human attestation and close one human/composite PRE_MERGE gate."""
    r.need(type(note_id) is int and note_id>0,"A positive forge comment ID is required.")
    refs=refs or []
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate=_gate(task,gate_id)
        semantics=_expected_semantics(cfg,task,gate_id,gate)
        r.need(semantics["attestor_class"] in ("ENGINEERING_OWNER","AUTHORIZED_HUMAN","COMPOSITE"),
               "This gate does not accept human/composite attestation.")
        r.need(gate["status"] != "NEEDS_REVALIDATION", "Gate needs revalidation before it can be satisfied again.")
        _binding_ready(task,gate)
        _append_evidence(gate,refs)
        r.need(_attestation_ref_count(gate) >= semantics["minimum_evidence"],
               f"Minimum non-attestation evidence requirement is not met for {gate_id}.")
        r.need(type(task.get("mr_iid")) is int and task["mr_iid"]>0,
               "Server-backed human attestation requires an exact Story MR before SATISFIED can be recorded.")
        digest,_payload=_binding_digest(task,gate_id,gate)
        expected=f"AE-ASSURE {gate_id} {task['task_id']} {digest}"
        allowed=_allowed_human_users(cfg,task,gate_id,gate,semantics)
        proof=r.verify_forge_comment(repo,cfg,task,note_id,expected,allowed,verify_mr=True)
        _transition(gate,"SATISFIED")
        att_ref=_ref(proof["attestor_ref"],"attestor_ref",actor=True)
        evidence_ref=_ref(proof["evidence_ref"],"human_attestation_evidence_ref")
        if evidence_ref not in gate["evidence_refs"]:
            gate["evidence_refs"].append(evidence_ref)
        gate["attestor_ref"]=att_ref
        gate["observed_at"]=proof["observed_at"]
        gate["waiver_ref"]=None
        _assert_gate_semantic_integrity(cfg,task,gate_id,gate)
        return {
            "result":"ASSURANCE_SATISFIED",
            "gate_id":gate_id,
            "status":gate["status"],
            "binding_digest":digest,
            "attestor_identity":proof["username"],
            "attestor_ref":att_ref,
            "note_id":note_id,
            "evidence_refs":list(gate["evidence_refs"]),
        }
    return _mutate(repo,task_arg,apply,op)


def _waiver_policy(cfg: dict[str, Any], gate_id: str) -> tuple[dict[str, Any], list[str]]:
    rule=cfg.get("assurance_policy",{}).get("waiver_policy",{}).get("gates",{}).get(gate_id)
    r.need(isinstance(rule,dict) and rule.get("waivable") is True,
           f"Assurance gate {gate_id} is not explicitly waivable by project policy.")
    owners=rule.get("risk_owners")
    r.need(isinstance(owners,list) and owners and all(r.text(x) for x in owners),
           f"Assurance gate {gate_id} has no authorized waiver risk owners.")
    return rule,list(owners)


def _waiver_path(repo: Path, waiver_id: str) -> Path:
    r.ident(waiver_id)
    path=Path(repo)/".agentic/local/waivers"/f"{waiver_id}.json"
    r.ensure_no_links(path,repo)
    return path


def _waiver_core(record: dict[str, Any]) -> dict[str, Any]:
    keys=("schema_version","waiver_id","gate_id","scope_type","scope_id","binding_digest","reason","unmet_requirement","residual_risk","compensating_controls","risk_owner","expiry","review_condition")
    return {k:copy.deepcopy(record.get(k)) for k in keys}


def waiver_digest(record: dict[str, Any]) -> str:
    raw=json.dumps(_waiver_core(record),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_waiver_record(record: dict[str, Any], *, approval_allowed: bool = True) -> dict[str, Any]:
    required={"schema_version","waiver_id","gate_id","scope_type","scope_id","binding_digest","reason","unmet_requirement","residual_risk","compensating_controls","risk_owner","approval_ref","expiry","review_condition"}
    r.need(isinstance(record,dict) and set(record)==required,"Waiver record fields are invalid.")
    r.need(record.get("schema_version")==1,"Use waiver schema 1.")
    r.ident(record.get("waiver_id"));r.need(isinstance(record.get("gate_id"),str) and record["gate_id"],"Waiver gate_id required.")
    r.need(record.get("scope_type") in ("STORY","CAPABILITY"),"Waiver scope_type must be STORY or CAPABILITY.")
    r.ident(record.get("scope_id"));r.need(re.fullmatch(r"[0-9a-f]{64}",str(record.get("binding_digest",""))) is not None,"Waiver binding_digest must be SHA-256 hex.")
    for k in ("reason","unmet_requirement","residual_risk","risk_owner"):
        _ref(record.get(k),f"waiver.{k}",actor=(k=="risk_owner"))
    controls=record.get("compensating_controls");r.need(isinstance(controls,list) and all(isinstance(x,str) and x.strip() and '\n' not in x and '\r' not in x for x in controls),"Waiver compensating_controls invalid.")
    for k in ("expiry","review_condition"):
        r.need(record.get(k) is None or (isinstance(record.get(k),str) and record[k].strip()),f"Waiver {k} must be null or a non-empty string.")
    if approval_allowed:
        r.need(record.get("approval_ref") is None or (isinstance(record.get("approval_ref"),str) and record["approval_ref"].strip()),"Waiver approval_ref invalid.")
    else:
        r.need(record.get("approval_ref") is None,"Waiver draft must not pre-claim approval_ref.")
    return record


def create_waiver_request(repo: Path, task_arg: Path, gate_id: str, waiver_id: str, risk_owner: str,
                          reason: str, unmet_requirement: str, residual_risk: str,
                          compensating_controls: list[str] | None = None, expiry: str | None = None,
                          review_condition: str | None = None, apply: bool = False) -> dict[str, Any]:
    """Create/preview one exact human-only waiver draft and expected forge comment."""
    repo=r.require_coordinator(repo);path,task,cfg=_load(repo,task_arg);gate=_gate(task,gate_id)
    r.need(gate["status"] not in CLOSED_STATUSES,"Closed assurance gate cannot receive a new waiver draft.")
    r.need(gate["status"] != "NEEDS_REVALIDATION","Finish binding revalidation before requesting a waiver on the changed candidate.")
    _expected_semantics(cfg,task,gate_id,gate);_binding_ready(task,gate)
    _rule,owners=_waiver_policy(cfg,gate_id);risk_owner=_ref(risk_owner,"waiver.risk_owner",actor=True)
    r.need(risk_owner in owners,"Requested waiver risk owner is not authorized by project policy.")
    binding_digest,_=_binding_digest(task,gate_id,gate)
    record={
        "schema_version":1,"waiver_id":r.ident(waiver_id),"gate_id":gate_id,"scope_type":"STORY","scope_id":task["task_id"],
        "binding_digest":binding_digest,"reason":_ref(reason,"waiver.reason"),"unmet_requirement":_ref(unmet_requirement,"waiver.unmet_requirement"),
        "residual_risk":_ref(residual_risk,"waiver.residual_risk"),"compensating_controls":list(compensating_controls or []),
        "risk_owner":risk_owner,"approval_ref":None,"expiry":expiry,"review_condition":review_condition,
    }
    _validate_waiver_record(record,approval_allowed=False);digest=waiver_digest(record);wpath=_waiver_path(repo,waiver_id)
    if apply:
        if wpath.exists():
            existing=r.read_json(wpath);_validate_waiver_record(existing)
            r.need(waiver_digest(existing)==digest and existing.get("approval_ref") is None,"Existing waiver draft differs or is already approved; use a new waiver_id.")
        else:r.atomic_json(wpath,record)
    return {
        "result":"WAIVER_REQUEST_READY","mode":"APPLIED" if apply else "PREVIEW_ONLY","waiver_id":waiver_id,
        "waiver_ref":wpath.resolve().relative_to(repo).as_posix(),"gate_id":gate_id,"binding_digest":binding_digest,
        "waiver_digest":digest,"risk_owner":risk_owner,
        "expected_note":f"AE-WAIVE {gate_id} {task['task_id']} {binding_digest} {digest}",
        "notice":"WAIVED is not PASS. Post the exact note from the named authorized risk-owner account; hard server/legal controls remain authoritative.",
    }


def apply_waiver(repo: Path, task_arg: Path, gate_id: str, waiver_id: str, note_id: int, apply: bool = False) -> dict[str, Any]:
    """Verify exact risk-owner server-backed proof, approve the bounded waiver, and mark gate WAIVED."""
    r.need(type(note_id) is int and note_id>0,"A positive forge comment ID is required.")
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate=_gate(task,gate_id);_expected_semantics(cfg,task,gate_id,gate)
        r.need(gate["status"] not in CLOSED_STATUSES,"Closed assurance gate cannot be waived again.")
        r.need(gate["status"] != "NEEDS_REVALIDATION","Finish binding revalidation before applying a waiver.")
        _binding_ready(task,gate);_rule,owners=_waiver_policy(cfg,gate_id)
        wpath=_waiver_path(repo,waiver_id);r.need(wpath.exists(),"Waiver draft does not exist; create an exact waiver request first.")
        record=r.read_json(wpath);_validate_waiver_record(record,approval_allowed=False)
        digest,_=_binding_digest(task,gate_id,gate)
        r.need(record["scope_type"]=="STORY" and record["gate_id"]==gate_id and record["scope_id"]==task["task_id"] and record["binding_digest"]==digest,"Waiver draft is bound to another gate/Story/version.")
        r.need(record["risk_owner"] in owners,"Waiver draft risk owner is no longer authorized by project policy.")
        wdigest=waiver_digest(record);expected=f"AE-WAIVE {gate_id} {task['task_id']} {digest} {wdigest}"
        r.need(type(task.get("mr_iid")) is int and task["mr_iid"]>0,"Server-backed waiver approval requires an exact Story MR.")
        proof=r.verify_forge_comment(repo,cfg,task,note_id,expected,[record["risk_owner"]],verify_mr=True)
        record["approval_ref"]=proof["attestor_ref"]
        if apply:r.atomic_json(wpath,record)
        _transition(gate,"WAIVED");gate["waiver_ref"]=wpath.resolve().relative_to(repo).as_posix();gate["attestor_ref"]=proof["attestor_ref"];gate["observed_at"]=proof["observed_at"]
        if proof["evidence_ref"] not in gate["evidence_refs"]:gate["evidence_refs"].append(proof["evidence_ref"])
        _assert_gate_semantic_integrity(cfg,task,gate_id,gate)
        return {"result":"ASSURANCE_WAIVED","gate_id":gate_id,"status":"WAIVED","waiver_id":waiver_id,"waiver_ref":gate["waiver_ref"],"waiver_digest":wdigest,"risk_owner":record["risk_owner"],"attestor_ref":proof["attestor_ref"],"notice":"WAIVED is an auditable exception, not SATISFIED. Phase-8 merge enforcement will still apply hard controls and waiver validity rules."}
    return _mutate(repo,task_arg,apply,op)


def _technical_review_virtual(task: dict[str, Any]) -> dict[str, Any] | None:
    required = set(task.get("assurance", {}).get("policy_snapshot", {}).get("project_required", [])) | set(task.get("assurance", {}).get("policy_snapshot", {}).get("story_required", []))
    if "technical_review" not in required:
        return None
    review = task.get("review") or {}
    candidate = task.get("candidate_sha")
    satisfied = bool(candidate and review.get("verdict") == "ACCEPT" and review.get("candidate_sha") == candidate and review.get("report_ref"))
    return {
        "gate_id": "technical_review",
        "virtual": True,
        "status": "SATISFIED" if satisfied else "PENDING",
        "candidate_sha": candidate,
        "report_ref": review.get("report_ref"),
    }


def summarize(task: dict[str, Any]) -> dict[str, Any]:
    gates = task["assurance"]["pre_merge"]
    virtual = _technical_review_virtual(task)
    summary = {
        "result": "ASSURANCE_STATUS",
        "task_id": task["task_id"],
        "candidate_sha": task.get("candidate_sha"),
        "contract_digest": task["contract"]["contract_digest"],
        "pre_merge": {gid: {
            "status": state["status"],
            "mode": state.get("mode"),
            "attestor_class": state["attestor_class"],
            "binding_class": state["binding_class"],
            "evidence_count": len(state.get("evidence_refs", [])),
            "attestor_ref": state.get("attestor_ref"),
            "waiver_ref": state.get("waiver_ref"),
            "revalidation": copy.deepcopy(state.get("revalidation")),
        } for gid, state in sorted(gates.items())},
        "downstream_obligations": copy.deepcopy(task["assurance"]["downstream_obligations"]),
    }
    if virtual is not None:
        summary["technical_review"] = virtual
    return summary


def premerge_closure(task: dict[str, Any]) -> dict[str, Any]:
    blocking: list[dict[str, Any]] = []
    for gate_id, gate in sorted(task["assurance"]["pre_merge"].items()):
        if gate["status"] != "SATISFIED":
            blocking.append({"gate_id": gate_id, "status": gate["status"]})
    virtual = _technical_review_virtual(task)
    if virtual is not None and virtual["status"] != "SATISFIED":
        blocking.append({"gate_id": "technical_review", "status": virtual["status"], "virtual": True})
    return {
        "result": "ASSURANCE_PRE_MERGE_CLOSED" if not blocking else "ASSURANCE_PRE_MERGE_PENDING",
        "task_id": task["task_id"],
        "candidate_sha": task.get("candidate_sha"),
        "blocking": blocking,
        "notice": "Read-only registry closure report. merge.py Phase-8 enforcement performs current contract/policy/binding and server-backed proof validation before AGENT_MERGE.",
    }


def _transition(gate: dict[str, Any], target: str) -> None:
    current = gate["status"]
    allowed = {
        "PENDING": {"IN_PROGRESS", "FAILED", "BLOCKED", "SATISFIED", "WAIVED"},
        "IN_PROGRESS": {"FAILED", "BLOCKED", "SATISFIED", "WAIVED"},
        "FAILED": {"IN_PROGRESS", "FAILED", "BLOCKED", "SATISFIED", "WAIVED"},
        "BLOCKED": {"IN_PROGRESS", "FAILED", "BLOCKED", "SATISFIED", "WAIVED"},
        "SATISFIED": set(),
        "WAIVED": set(),
        "NEEDS_REVALIDATION": set(),  # Closed only by dedicated revalidation functions below.
    }
    r.need(target in allowed.get(current, set()), f"Invalid assurance transition {current} -> {target} at this implementation phase.")
    gate["status"] = target


def _append_evidence(gate: dict[str, Any], refs: list[str]) -> None:
    for value in refs:
        ref = _ref(value, "evidence_ref")
        if ref not in gate["evidence_refs"]:
            gate["evidence_refs"].append(ref)


def _mutate(repo: Path, task_arg: Path, apply: bool, fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    if not apply:
        _, task, cfg = _load(repo, task_arg)
        working = copy.deepcopy(task)
        out = fn(working, cfg)
        r.validate_task_schema3(working)
        out["mode"] = "PREVIEW_ONLY"
        return out
    repo = r.require_coordinator(repo)
    with r.local_lock(repo):
        path, task, cfg = _load(repo, task_arg)
        out = fn(task, cfg)
        r.validate_task_schema3(task)
        r.atomic_json(path, task)
        out["mode"] = "APPLIED"
        return out


def add_evidence(repo: Path, task_arg: Path, gate_id: str, refs: list[str], apply: bool = False) -> dict[str, Any]:
    r.need(bool(refs), "At least one evidence reference is required.")
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        _expected_semantics(cfg, task, gate_id, gate)
        r.need(gate["status"] not in CLOSED_STATUSES, "Closed assurance gate is immutable; revalidation/waiver uses dedicated later-phase semantics.")
        _append_evidence(gate, refs)
        return {"result": "ASSURANCE_EVIDENCE_ATTACHED", "gate_id": gate_id, "status": gate["status"], "evidence_refs": list(gate["evidence_refs"])}
    return _mutate(repo, task_arg, apply, op)


def begin(repo: Path, task_arg: Path, gate_id: str, actor_ref: str, apply: bool = False) -> dict[str, Any]:
    actor = _ref(actor_ref, "actor_ref", actor=True)
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        _expected_semantics(cfg, task, gate_id, gate)
        _transition(gate, "IN_PROGRESS")
        gate["observed_at"] = r.now()
        gate["attestor_ref"] = actor
        return {"result": "ASSURANCE_IN_PROGRESS", "gate_id": gate_id, "status": gate["status"]}
    return _mutate(repo, task_arg, apply, op)


def disposition(repo: Path, task_arg: Path, gate_id: str, status: str, actor_ref: str, refs: list[str] | None = None, apply: bool = False) -> dict[str, Any]:
    r.need(status in ("FAILED", "BLOCKED"), "Assurance disposition supports FAILED or BLOCKED only.")
    actor = _ref(actor_ref, "actor_ref", actor=True)
    refs = refs or []
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        _expected_semantics(cfg, task, gate_id, gate)
        _transition(gate, status)
        _append_evidence(gate, refs)
        gate["observed_at"] = r.now()
        gate["attestor_ref"] = actor
        return {"result": f"ASSURANCE_{status}", "gate_id": gate_id, "status": gate["status"], "evidence_refs": list(gate["evidence_refs"])}
    return _mutate(repo, task_arg, apply, op)


def satisfy_tool(repo: Path, task_arg: Path, gate_id: str, attestor_ref: str, refs: list[str], apply: bool = False) -> dict[str, Any]:
    attestor = _ref(attestor_ref, "attestor_ref", actor=True)
    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        semantics = _expected_semantics(cfg, task, gate_id, gate)
        r.need(semantics["attestor_class"] in ("TOOL", "CONTROL_PLANE"),
               "This gate requires server-backed human/composite attestation; use satisfy-human.")
        r.need(gate["status"] != "NEEDS_REVALIDATION", "Gate needs revalidation before it can be satisfied again.")
        _binding_ready(task, gate)
        _append_evidence(gate, refs)
        r.need(len(gate["evidence_refs"]) >= semantics["minimum_evidence"], "Minimum evidence requirement is not met.")
        _transition(gate, "SATISFIED")
        gate["observed_at"] = r.now()
        gate["attestor_ref"] = attestor
        gate["waiver_ref"] = None
        return {"result": "ASSURANCE_SATISFIED", "gate_id": gate_id, "status": gate["status"], "attestor_ref": attestor, "evidence_refs": list(gate["evidence_refs"])}
    return _mutate(repo, task_arg, apply, op)



def _revalidation_record_path(repo: Path, task: dict[str, Any], gate_id: str, to_binding: str, record_hash: str) -> Path:
    safe_gate = re.sub(r"[^A-Za-z0-9._-]+", "_", gate_id).strip("_") or "gate"
    short = str(to_binding)[:12]
    path = Path(repo) / ".agentic/local/revalidation" / f"{task['developer']}-{task['task_id']}-{safe_gate}-{short}-{record_hash[:10]}.json"
    r.ensure_no_links(path, repo)
    return path


def _candidate_delta(repo: Path, old_sha: str, new_sha: str) -> dict[str, Any]:
    """Mechanical auto-first delta classifier.

    Exact whole-tree equality is strong enough to call EQUIVALENT. Any tree content
    delta remains UNKNOWN because Phase 7 intentionally has no gate-to-path semantic
    model. A later bounded Control Plane/reviewer assessment may classify that delta
    NON_MATERIAL_TO_GATE or MATERIAL_TO_GATE.
    """
    r.commit(repo, old_sha); r.commit(repo, new_sha)
    old_tree = r.git(repo, "rev-parse", old_sha + "^{tree}")[1]
    new_tree = r.git(repo, "rev-parse", new_sha + "^{tree}")[1]
    raw = r.git(repo, "diff", "--name-only", "-z", old_sha, new_sha, "--")[1]
    changed = [x for x in raw.split("\x00") if x]
    disposition = "EQUIVALENT" if old_tree == new_tree else "UNKNOWN"
    return {
        "disposition": disposition,
        "old_tree": old_tree,
        "new_tree": new_tree,
        "changed_path_count": len(changed),
        "changed_paths": changed[:100],
        "changed_paths_truncated": len(changed) > 100,
    }


def _revalidation_context(task: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> tuple[str, str, str]:
    r.need(gate.get("status") == "NEEDS_REVALIDATION", f"Assurance gate {gate_id} does not need revalidation.")
    r.need(gate.get("binding_class") == "CANDIDATE", "Phase-7 Story revalidation currently applies to candidate-bound PRE_MERGE gates.")
    rv = gate.get("revalidation") or {}
    old_sha = rv.get("from_binding"); new_sha = rv.get("to_binding")
    r.full(old_sha); r.full(new_sha)
    current = task.get("candidate_sha"); r.full(current)
    r.need(new_sha == current, "Revalidation target binding is not the current task candidate.")
    r.need(gate.get("binding", {}).get("candidate_sha") == old_sha,
           "Revalidation source binding no longer matches the gate's previous candidate.")
    previous = "WAIVED" if gate.get("waiver_ref") else "SATISFIED"
    r.need(gate.get("attestor_ref") is not None or previous == "WAIVED",
           "NEEDS_REVALIDATION gate lacks previous closed-state provenance.")
    return old_sha, new_sha, previous


def _build_revalidation_record(repo: Path, task: dict[str, Any], gate_id: str, gate: dict[str, Any], *,
                               disposition: str, assessor_class: str, assessor_identity_ref: str,
                               rationale_ref: str, delta: dict[str, Any] | None) -> tuple[str, Path, dict[str, Any]]:
    old_sha, new_sha, previous = _revalidation_context(task, gate_id, gate)
    observed = r.now()
    record = {
        "schema_version": 1,
        "task_id": task["task_id"],
        "developer": task["developer"],
        "gate_id": gate_id,
        "from_binding": old_sha,
        "to_binding": new_sha,
        "previous_status": previous,
        "previous_attestor_ref": gate.get("attestor_ref"),
        "previous_waiver_ref": gate.get("waiver_ref"),
        "previous_evidence_refs": copy.deepcopy(gate.get("evidence_refs", [])),
        "disposition": disposition,
        "assessor_class": assessor_class,
        "assessor_identity_ref": assessor_identity_ref,
        "rationale_ref": rationale_ref,
        "delta": copy.deepcopy(delta),
        "observed_at": observed,
    }
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    record_hash = hashlib.sha256(raw).hexdigest()
    path = _revalidation_record_path(repo, task, gate_id, new_sha, record_hash)
    rel = path.resolve().relative_to(repo).as_posix()
    return "revalidation:" + rel, path, record


def _apply_revalidation(task: dict[str, Any], cfg: dict[str, Any], gate_id: str, gate: dict[str, Any], *,
                        disposition: str, assessor_class: str, assessor_ref: str) -> dict[str, Any]:
    r.need(disposition in REVALIDATION_DISPOSITIONS, "Invalid revalidation disposition.")
    old_sha, new_sha, previous = _revalidation_context(task, gate_id, gate)
    # Rebind the gate to the current candidate only as part of an explicit disposition.
    gate["binding"]["candidate_sha"] = new_sha
    gate["revalidation"].update(
        disposition=disposition,
        from_binding=old_sha,
        to_binding=new_sha,
        assessor_class=assessor_class,
        assessor_ref=assessor_ref,
    )

    if disposition in ("EQUIVALENT", "NON_MATERIAL_TO_GATE"):
        if previous == "SATISFIED":
            gate["status"] = "SATISFIED"
            _assert_gate_semantic_integrity(cfg, task, gate_id, gate)
            result = "ASSURANCE_REVALIDATED_REUSED"
        else:
            # Waivers are exact human risk-acceptance bindings. Do not silently carry
            # one to a new candidate even when the code tree is equivalent.
            gate["status"] = "PENDING"
            gate["waiver_ref"] = None
            gate["attestor_ref"] = None
            gate["observed_at"] = None
            gate["evidence_refs"] = [x for x in gate.get("evidence_refs", []) if not str(x).startswith("human-attestation:")]
            result = "ASSURANCE_REVALIDATED_WAIVER_REAPPROVAL_REQUIRED"
    elif disposition == "MATERIAL_TO_GATE":
        # Previous evidence remains preserved inside the bounded revalidation record.
        # Current gate state is reopened so new evidence/attestation must be collected.
        gate["status"] = "PENDING"
        gate["attestor_ref"] = None
        gate["observed_at"] = None
        gate["waiver_ref"] = None
        gate["evidence_refs"] = []
        result = "ASSURANCE_TARGETED_REVALIDATION_REQUIRED"
    else:  # UNKNOWN
        gate["status"] = "NEEDS_REVALIDATION"
        result = "ASSURANCE_REVALIDATION_DECISION_REQUIRED"

    return {
        "result": result,
        "task_id": task["task_id"],
        "gate_id": gate_id,
        "disposition": disposition,
        "previous_status": previous,
        "status": gate["status"],
        "from_binding": old_sha,
        "to_binding": new_sha,
        "assessor_class": assessor_class,
        "assessor_ref": assessor_ref,
    }


def revalidate_auto(repo: Path, task_arg: Path, gate_id: str, apply: bool = False) -> dict[str, Any]:
    """Conservative auto-first assessment: exact tree equality => EQUIVALENT, else UNKNOWN."""
    repo = r.require_coordinator(repo)
    # Compute immutable Git delta outside mutation first; _mutate rechecks task state.
    _, snapshot, _ = _load(repo, task_arg)
    snap_gate = _gate(snapshot, gate_id)
    old_sha, new_sha, _ = _revalidation_context(snapshot, gate_id, snap_gate)
    delta = _candidate_delta(repo, old_sha, new_sha)
    rationale = (f"git-tree-equivalent:{delta['old_tree']}" if delta["disposition"] == "EQUIVALENT"
                 else f"git-tree-diff:{old_sha[:12]}..{new_sha[:12]}:paths={delta['changed_path_count']}")

    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        _expected_semantics(cfg, task, gate_id, gate)
        cur_old, cur_new, _ = _revalidation_context(task, gate_id, gate)
        r.need(cur_old == old_sha and cur_new == new_sha, "Task/gate changed during revalidation assessment; recompute against current state.")
        rec_ref, rec_path, rec_data = _build_revalidation_record(repo, task, gate_id, gate,
            disposition=delta["disposition"], assessor_class="TOOL", assessor_identity_ref="git-tree-delta",
            rationale_ref=rationale, delta=delta)
        out = _apply_revalidation(task, cfg, gate_id, gate, disposition=delta["disposition"], assessor_class="TOOL", assessor_ref=rec_ref)
        r.validate_task_schema3(task)
        if apply: r.atomic_json(rec_path, rec_data)
        out["delta"] = delta
        out["revalidation_record_ref"] = rec_ref
        return out
    return _mutate(repo, task_arg, apply, op)


def revalidate_assess(repo: Path, task_arg: Path, gate_id: str, disposition: str, assessor_class: str,
                      assessor_ref: str, rationale_ref: str, apply: bool = False) -> dict[str, Any]:
    """Record one bounded gate-local delta judgment.

    This is not human assurance attestation. It decides only whether previous gate
    evidence remains reusable or targeted assurance must be recollected.
    """
    r.need(disposition in REVALIDATION_DISPOSITIONS, "Invalid revalidation disposition.")
    r.need(assessor_class in REVALIDATION_ASSESSORS, "Invalid revalidation assessor class.")
    actor = _ref(assessor_ref, "revalidation.assessor_ref", actor=True)
    rationale = _ref(rationale_ref, "revalidation.rationale_ref")

    def op(task: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        gate = _gate(task, gate_id)
        _expected_semantics(cfg, task, gate_id, gate)
        old_sha, new_sha, _ = _revalidation_context(task, gate_id, gate)
        delta = _candidate_delta(repo, old_sha, new_sha)
        rec_ref, rec_path, rec_data = _build_revalidation_record(repo, task, gate_id, gate,
            disposition=disposition, assessor_class=assessor_class, assessor_identity_ref=actor,
            rationale_ref=rationale, delta=delta)
        out = _apply_revalidation(task, cfg, gate_id, gate, disposition=disposition, assessor_class=assessor_class, assessor_ref=rec_ref)
        r.validate_task_schema3(task)
        if apply: r.atomic_json(rec_path, rec_data)
        out["mechanical_delta"] = delta
        out["rationale_ref"] = rationale
        out["revalidation_record_ref"] = rec_ref
        return out
    return _mutate(repo, task_arg, apply, op)


# ---------------------------------------------------------------------------
# Phase 10 — capability / post-integration assurance
# ---------------------------------------------------------------------------

def capability_state_ref(capability_id: str) -> str:
    r.ident(capability_id)
    return f".agentic/local/capabilities/{capability_id}.json"


def capability_record_ref(capability_id: str) -> str:
    r.ident(capability_id)
    return f"docs/agentic/records/team/{capability_id}/assurance.json"


def _capability_path(repo: Path, capability_id: str) -> Path:
    path = Path(repo) / capability_state_ref(capability_id)
    r.ensure_no_links(path, repo)
    return path


def _capability_gate_semantics(cfg: dict[str, Any], state: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> dict[str, Any]:
    mode = gate.get("mode")
    if gate_id == "human_understanding":
        expected_attestor, minimum = "ENGINEERING_OWNER", 0
    elif gate_id == "operability_recovery":
        # Capability aggregation prefers the engineering owner when one unique owner
        # spans all contributing Stories; otherwise the project must name an authorized
        # operational reviewer.
        owners = _capability_engineering_owner_users(state, strict=False)
        expected_attestor = "ENGINEERING_OWNER" if len(owners) == 1 else "AUTHORIZED_HUMAN"
        minimum = 1
    elif gate_id == "nfr_performance":
        expected_attestor = "COMPOSITE" if mode == "COMPOSITE" else "TOOL"
        minimum = 1
    elif gate_id == "traceability_completion":
        expected_attestor, minimum = "CONTROL_PLANE", 1
    elif gate_id == "architecture_review":
        expected_attestor = "AUTHORIZED_HUMAN"
        minimum = 1
    elif gate_id == "security_review":
        expected_attestor, minimum = "COMPOSITE", 1
    elif gate_id.startswith("project_defined:"):
        definition = c.custom_gate_runtime_definition(cfg["assurance_policy"], gate_id, stage="POST_INTEGRATION", mode=mode)
        r.need(definition["binding_class"] == "INTEGRATED_CAPABILITY",
               f"Custom capability gate {gate_id} must use INTEGRATED_CAPABILITY binding.")
        expected_attestor = definition["attestor_class"]
        minimum = definition["minimum_evidence"]
    else:
        raise r.AEError(f"Unsupported POST_INTEGRATION assurance gate {gate_id}.")
    r.need(gate.get("attestor_class") == expected_attestor,
           f"Capability gate {gate_id} attestor_class does not match built-in/project semantics.")
    r.need(gate.get("binding_class") == "INTEGRATED_CAPABILITY",
           f"Capability gate {gate_id} must use INTEGRATED_CAPABILITY binding.")
    return {"attestor_class": expected_attestor, "binding_class": "INTEGRATED_CAPABILITY", "minimum_evidence": minimum}


def _capability_gate_definition(cfg: dict[str, Any], state: dict[str, Any], obligation: dict[str, Any], capability_digest: str) -> dict[str, Any]:
    gate_id = obligation["gate_id"]
    mode = obligation.get("mode")
    if gate_id == "human_understanding": attestor = "ENGINEERING_OWNER"
    elif gate_id == "operability_recovery":
        attestor = "ENGINEERING_OWNER" if len(_capability_engineering_owner_users(state, strict=False)) == 1 else "AUTHORIZED_HUMAN"
    elif gate_id == "nfr_performance": attestor = "COMPOSITE" if mode == "COMPOSITE" else "TOOL"
    elif gate_id == "traceability_completion": attestor = "CONTROL_PLANE"
    elif gate_id == "architecture_review": attestor = "AUTHORIZED_HUMAN"
    elif gate_id == "security_review": attestor = "COMPOSITE"
    elif gate_id.startswith("project_defined:"):
        definition = c.custom_gate_runtime_definition(cfg["assurance_policy"], gate_id, stage="POST_INTEGRATION", mode=mode)
        r.need(definition["binding_class"] == "INTEGRATED_CAPABILITY",
               f"Custom capability gate {gate_id} must use INTEGRATED_CAPABILITY binding.")
        attestor = definition["attestor_class"]
    else: raise r.AEError(f"Unsupported POST_INTEGRATION assurance gate {gate_id}.")
    return {
        "gate_id": gate_id,
        "stage": "POST_INTEGRATION",
        "mode": mode,
        "required_sources": sorted(set(obligation.get("required_sources", []))),
        "attestor_class": attestor,
        "binding_class": "INTEGRATED_CAPABILITY",
        "status": "PENDING",
        "binding": {"contract_digest": capability_digest, "integrated_target_sha": state["integrated_target_sha"]},
        "evidence_refs": [],
        "attestor_ref": None,
        "observed_at": None,
        "revalidation": {"disposition": "NOT_EVALUATED", "from_binding": None, "to_binding": None, "assessor_class": None, "assessor_ref": None},
        "waiver_ref": None,
        "residual_risk_refs": [],
        "evidence_plan_refs": sorted(set(obligation.get("evidence_plan_refs", []))),
    }


def _capability_digest_core(state: dict[str, Any]) -> dict[str, Any]:
    story_inputs = [{k: item[k] for k in ("story_id", "candidate_sha", "mr_iid", "merge_result_sha")} for item in state.get("story_inputs", [])]
    story_inputs.sort(key=lambda x: x["story_id"])
    obligations = []
    for gate_id, gate in sorted(state.get("gates", {}).items()):
        obligations.append({
            "gate_id": gate_id,
            "stage": "POST_INTEGRATION",
            "mode": gate.get("mode"),
            "required_sources": sorted(set(gate.get("required_sources", []))),
            "evidence_plan_refs": sorted(set(gate.get("evidence_plan_refs", []))),
        })
    return {
        "schema_version": 1,
        "scope_type": "CAPABILITY",
        "capability_id": state.get("capability_id"),
        "canonical_backlog_ref": state.get("canonical_backlog_ref"),
        "backlog_revision": state.get("backlog_revision"),
        "story_inputs": story_inputs,
        "obligations": obligations,
    }


def capability_digest(state: dict[str, Any]) -> str:
    raw = json.dumps(_capability_digest_core(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_capability_gate(state: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> None:
    required = {"gate_id", "stage", "mode", "required_sources", "attestor_class", "binding_class", "status", "binding", "evidence_refs", "attestor_ref", "observed_at", "revalidation", "waiver_ref", "residual_risk_refs", "evidence_plan_refs"}
    r.need(isinstance(gate, dict) and set(gate) == required, f"Capability gate {gate_id} fields are invalid.")
    r.need(gate.get("gate_id") == gate_id and gate.get("stage") == "POST_INTEGRATION", "Capability gate identity/stage mismatch.")
    r.need(gate.get("binding_class") == "INTEGRATED_CAPABILITY", "Capability gate binding class invalid.")
    r.need(gate.get("status") in ("PENDING", "IN_PROGRESS", "SATISFIED", "FAILED", "BLOCKED", "NEEDS_REVALIDATION", "WAIVED"), "Capability gate status invalid.")
    r.need(isinstance(gate.get("required_sources"), list) and gate["required_sources"] and set(gate["required_sources"]).issubset({"project", "tier", "story"}), "Capability gate required_sources invalid.")
    for key in ("evidence_refs", "residual_risk_refs", "evidence_plan_refs"):
        r.need(isinstance(gate.get(key), list) and all(isinstance(x, str) and x.strip() and "\n" not in x and "\r" not in x for x in gate[key]), f"Capability gate {key} invalid.")
    b = gate.get("binding")
    r.need(isinstance(b, dict) and set(b) == {"contract_digest", "integrated_target_sha"}, "Capability gate binding fields invalid.")
    r.need(re.fullmatch(r"[0-9a-f]{64}", str(b.get("contract_digest", ""))) is not None, "Capability contract digest invalid.")
    r.full(b.get("integrated_target_sha"))
    rv = gate.get("revalidation")
    r.need(isinstance(rv, dict) and set(rv) == {"disposition", "from_binding", "to_binding", "assessor_class", "assessor_ref"}, "Capability revalidation block invalid.")
    r.need(rv.get("disposition") in ("NOT_EVALUATED", *sorted(REVALIDATION_DISPOSITIONS)), "Capability revalidation disposition invalid.")
    if gate["status"] == "SATISFIED":
        r.need(r.text(gate.get("attestor_ref")) and r.text(gate.get("observed_at")), "SATISFIED capability gate requires attestor/time provenance.")
        # ENGINEERING_OWNER may have zero non-attestation evidence, but server human
        # provenance is still recorded as an evidence ref.
        r.need(bool(gate.get("evidence_refs")), "SATISFIED capability gate requires evidence provenance.")
    if gate["status"] == "WAIVED": r.need(r.text(gate.get("waiver_ref")), "WAIVED capability gate requires waiver_ref.")
    if gate["status"] == "NEEDS_REVALIDATION":
        r.need(rv.get("from_binding") is not None and rv.get("to_binding") is not None, "Capability revalidation requires from/to binding.")


def validate_capability_state(state: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "scope_type", "capability_id", "canonical_backlog_ref", "backlog_revision", "integrated_target_sha", "story_inputs", "gates", "product_acceptance_ref", "product_acceptance", "build_complete", "release_pointer_refs"}
    r.need(isinstance(state, dict) and set(state) == required and state.get("schema_version") == 1 and state.get("scope_type") == "CAPABILITY", "Capability assurance schema 1 fields are invalid.")
    r.ident(state.get("capability_id")); r._relative_repo_ref(state.get("canonical_backlog_ref"), "capability canonical backlog ref"); r.need(r.text(state.get("backlog_revision")), "Capability backlog revision required.")
    r.full(state.get("integrated_target_sha"))
    inputs = state.get("story_inputs"); r.need(isinstance(inputs, list) and inputs, "Capability requires at least one integrated Story input.")
    seen = set()
    for item in inputs:
        r.need(isinstance(item, dict) and set(item) == {"story_id", "developer", "candidate_sha", "mr_iid", "merge_result_sha", "traceability_ref"}, "Capability Story input fields invalid.")
        r.ident(item.get("story_id")); r.ident(item.get("developer")); r.full(item.get("candidate_sha")); r.full(item.get("merge_result_sha")); r.need(type(item.get("mr_iid")) is int and item["mr_iid"] > 0, "Capability Story MR IID invalid.")
        r._relative_repo_ref(item.get("traceability_ref"), "capability Story traceability_ref")
        r.need(item["story_id"] not in seen, "Capability contains duplicate Story input."); seen.add(item["story_id"])
    gates = state.get("gates"); r.need(isinstance(gates, dict), "Capability gates must be an object.")
    for gate_id, gate in gates.items(): _validate_capability_gate(state, gate_id, gate)
    r.need(state.get("product_acceptance_ref") is None or (isinstance(state.get("product_acceptance_ref"), str) and state["product_acceptance_ref"].strip()), "Capability product_acceptance_ref invalid.")
    pa = state.get("product_acceptance")
    r.need(isinstance(pa, dict) and set(pa) == {"status", "evidence_ref", "actor_ref", "observed_at", "integrated_target_sha"}, "Capability product_acceptance block invalid.")
    r.need(pa.get("status") in ("PENDING", "SATISFIED"), "Capability product acceptance status invalid.")
    if pa["status"] == "SATISFIED":
        r.need(r.text(pa.get("evidence_ref")) and r.text(pa.get("actor_ref")) and r.text(pa.get("observed_at")), "Satisfied product acceptance requires evidence/actor/time.")
        r.need(pa.get("integrated_target_sha") == state["integrated_target_sha"], "Product acceptance belongs to another integrated target.")
    else:
        r.need(pa.get("evidence_ref") is None and pa.get("actor_ref") is None and pa.get("observed_at") is None and pa.get("integrated_target_sha") is None, "Pending product acceptance must not pre-claim evidence.")
    r.need(type(state.get("build_complete")) is bool, "Capability build_complete must be bool.")
    r.need(isinstance(state.get("release_pointer_refs"), list) and all(r.text(x) for x in state["release_pointer_refs"]), "Capability release pointers invalid.")
    if state["build_complete"]:
        r.need(pa["status"] == "SATISFIED", "BUILD COMPLETE requires product capability acceptance.")
        r.need(all(g.get("status") in ("SATISFIED", "WAIVED") for g in gates.values()), "BUILD COMPLETE requires all capability gates closed.")
    return state


def _load_capability(repo: Path, capability_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    repo = r.require_coordinator(repo)
    cfg = r.config(repo); r.config_ready(cfg)
    path = _capability_path(repo, capability_id); r.need(path.is_file(), f"Capability assurance state {capability_id} is not registered.")
    state = r.read_json(path); validate_capability_state(state)
    return path, state, cfg


def _story_traceability(repo: Path, item: dict[str, Any]) -> dict[str, Any]:
    path = Path(repo) / item["traceability_ref"]
    r.ensure_no_links(path, repo); r.need(path.is_file(), f"Story traceability record missing: {item['traceability_ref']}")
    record = r.read_json(path); r.validate_traceability_index(record)
    r.need(record["story"]["story_id"] == item["story_id"] and record["story"]["developer"] == item["developer"], "Capability Story traceability identity mismatch.")
    r.need(record["candidate_sha"] == item["candidate_sha"] and record["mr"]["iid"] == item["mr_iid"] and record["merge"]["result_sha"] == item["merge_result_sha"], "Capability Story traceability integration facts mismatch.")
    return record


def _capability_engineering_owner_users(state: dict[str, Any], repo: Path | None = None, strict: bool = True) -> list[str]:
    # During initial gate construction we may not yet have a repo argument. In that
    # case use a conservative empty result so operability becomes AUTHORIZED_HUMAN;
    # register_capability_integration subsequently normalizes it with repo evidence.
    if repo is None:
        return []
    users = []
    for item in state.get("story_inputs", []):
        record = _story_traceability(repo, item)
        owner = record["story"].get("engineering_owner")
        if isinstance(owner, dict) and owner.get("attestation_provider") == "gitlab" and r.text(owner.get("attestation_identity")):
            users.append(owner["attestation_identity"])
    uniq = sorted(set(users))
    if strict: r.need(len(uniq) == 1, "Capability ENGINEERING_OWNER attestation requires one resolved forge engineering-owner identity across covered Stories.")
    return uniq


def _capability_allowed_human_users(repo: Path, cfg: dict[str, Any], state: dict[str, Any], gate_id: str, gate: dict[str, Any], semantics: dict[str, Any]) -> list[str]:
    cls = semantics["attestor_class"]
    if cls == "ENGINEERING_OWNER": return _capability_engineering_owner_users(state, repo=repo, strict=True)
    if cls in ("AUTHORIZED_HUMAN", "COMPOSITE"):
        users = _project_attestor_users(cfg, gate_id, gate.get("mode")); r.need(bool(users), f"Capability gate {gate_id} requires configured authorized human attestor(s).")
        return users
    raise r.AEError(f"Capability gate {gate_id} does not use human attestation.")


def _capability_binding_digest(state: dict[str, Any], gate_id: str, gate: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    digest = capability_digest(state)
    payload = {
        "scope_type": "CAPABILITY",
        "scope_id": state["capability_id"],
        "gate_id": gate_id,
        "stage": "POST_INTEGRATION",
        "mode": gate.get("mode"),
        "contract_digest": digest,
        "binding_class": "INTEGRATED_CAPABILITY",
        "bound_version": state["integrated_target_sha"],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), payload


def _representative_story(state: dict[str, Any]) -> dict[str, Any]:
    r.need(bool(state.get("story_inputs")), "Capability has no Story inputs.")
    return state["story_inputs"][-1]


def _pseudo_task_for_capability(state: dict[str, Any]) -> dict[str, Any]:
    rep = _representative_story(state)
    return {"mr_iid": rep["mr_iid"], "candidate_sha": rep["candidate_sha"]}


def _refresh_capability_gate_semantics(repo: Path, cfg: dict[str, Any], state: dict[str, Any]) -> None:
    for gate_id, gate in state.get("gates", {}).items():
        if gate_id == "operability_recovery":
            owners = _capability_engineering_owner_users(state, repo=repo, strict=False)
            expected = "ENGINEERING_OWNER" if len(owners) == 1 else "AUTHORIZED_HUMAN"
            if gate["status"] not in ("PENDING", "FAILED", "BLOCKED", "IN_PROGRESS") and gate.get("attestor_class") != expected:
                gate["status"] = "NEEDS_REVALIDATION"
                gate["revalidation"].update(disposition="UNKNOWN", from_binding=gate["binding"].get("integrated_target_sha"), to_binding=state["integrated_target_sha"], assessor_class="CONTROL_PLANE", assessor_ref="capability-owner-set-change")
            gate["attestor_class"] = expected
        _capability_gate_semantics(cfg, state, gate_id, gate)


def _merge_capability_obligations(cfg: dict[str, Any], state: dict[str, Any], obligations: list[dict[str, Any]], old_digest: str | None, old_target: str | None) -> None:
    # Add/merge generic gates first using a temporary digest; then recompute the final
    # capability digest with the complete Story/gate set and bind all gates to it.
    for ob in obligations:
        gate_id = ob["gate_id"]
        if gate_id not in state["gates"]:
            state["gates"][gate_id] = _capability_gate_definition(cfg, state, ob, "0" * 64)
        else:
            gate = state["gates"][gate_id]
            r.need(gate.get("mode") == ob.get("mode"), f"Capability gate {gate_id} mode conflict across Story obligations; reconcile canonical backlog/policy.")
            gate["required_sources"] = sorted(set(gate.get("required_sources", [])) | set(ob.get("required_sources", [])))
            gate["evidence_plan_refs"] = sorted(set(gate.get("evidence_plan_refs", [])) | set(ob.get("evidence_plan_refs", [])))
    new_digest = capability_digest(state)
    for gate_id, gate in state["gates"].items():
        prior_target = gate.get("binding", {}).get("integrated_target_sha")
        prior_digest = gate.get("binding", {}).get("contract_digest")
        changed = prior_target not in (None, state["integrated_target_sha"]) or prior_digest not in (None, "0" * 64, new_digest)
        if changed:
            if gate.get("status") in ("SATISFIED", "WAIVED"):
                gate["status"] = "NEEDS_REVALIDATION"
                gate["revalidation"].update(disposition="NOT_EVALUATED", from_binding=prior_target, to_binding=state["integrated_target_sha"], assessor_class=None, assessor_ref=None)
            else:
                gate["status"] = "PENDING"; gate["evidence_refs"] = []; gate["attestor_ref"] = None; gate["observed_at"] = None; gate["waiver_ref"] = None
                gate["revalidation"].update(disposition="NOT_EVALUATED", from_binding=None, to_binding=None, assessor_class=None, assessor_ref=None)
        gate["binding"] = {"contract_digest": new_digest, "integrated_target_sha": state["integrated_target_sha"]}


def register_capability_integration(repo: Path, cfg: dict[str, Any], task: dict[str, Any], proof: dict[str, Any], *, apply: bool) -> dict[str, Any] | None:
    """Create/update capability assurance state after verified Story integration.

    Only POST_INTEGRATION obligations create a capability registry. RELEASE_POINTER
    obligations are carried as pointers when a registry already exists.
    """
    if task.get("schema_version") != 3: return None
    post = [copy.deepcopy(x) for x in task.get("assurance", {}).get("downstream_obligations", []) if x.get("stage") == "POST_INTEGRATION"]
    if not post: return None
    cap_id = task.get("contract", {}).get("capability_id"); r.need(r.text(cap_id), "POST_INTEGRATION assurance requires capability_id."); r.ident(cap_id)
    trace_ref = task.get("traceability", {}).get("record_ref"); r.need(r.text(trace_ref), "Capability aggregation requires the Story traceability index first.")
    r.full(proof.get("target_sha")); r.full(proof.get("result_sha"))
    path = _capability_path(repo, cap_id)
    if path.exists():
        state = r.read_json(path); validate_capability_state(state)
        r.need(state["canonical_backlog_ref"] == task["contract"]["canonical_backlog_ref"] and state["backlog_revision"] == task["contract"]["backlog_revision"], "Capability Story uses a different canonical backlog/revision; do not aggregate ambiguous authority.")
        old_target = state["integrated_target_sha"]; old_digest = capability_digest(state)
    else:
        state = {
            "schema_version": 1, "scope_type": "CAPABILITY", "capability_id": cap_id,
            "canonical_backlog_ref": task["contract"]["canonical_backlog_ref"], "backlog_revision": task["contract"]["backlog_revision"],
            "integrated_target_sha": proof["target_sha"], "story_inputs": [], "gates": {},
            "product_acceptance_ref": task["contract"]["final_acceptance_ref"],
            "product_acceptance": {"status": "PENDING", "evidence_ref": None, "actor_ref": None, "observed_at": None, "integrated_target_sha": None},
            "build_complete": False, "release_pointer_refs": []}
        old_target = None; old_digest = None
    story_input = {"story_id": task["task_id"], "developer": task["developer"], "candidate_sha": task["candidate_sha"], "mr_iid": task["mr_iid"], "merge_result_sha": proof["result_sha"], "traceability_ref": trace_ref}
    existing = next((x for x in state["story_inputs"] if x["story_id"] == task["task_id"]), None)
    if existing is not None:
        r.need(existing == story_input, "Capability already contains different immutable integration facts for this Story.")
    else:
        state["story_inputs"].append(story_input)
    state["integrated_target_sha"] = proof["target_sha"]
    # Any new integrated composition invalidates prior product acceptance/build-complete.
    if old_target is not None and old_target != state["integrated_target_sha"]:
        state["product_acceptance"] = {"status": "PENDING", "evidence_ref": None, "actor_ref": None, "observed_at": None, "integrated_target_sha": None}
        state["build_complete"] = False
    _merge_capability_obligations(cfg, state, post, old_digest, old_target)
    _refresh_capability_gate_semantics(repo, cfg, state)
    for ob in task.get("assurance", {}).get("downstream_obligations", []):
        if ob.get("stage") == "RELEASE_POINTER":
            for ref in ob.get("evidence_plan_refs", []):
                if ref not in state["release_pointer_refs"]: state["release_pointer_refs"].append(ref)
    state["release_pointer_refs"] = sorted(set(state["release_pointer_refs"]))
    validate_capability_state(state)
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True); r.ensure_no_links(path.parent, repo); r.atomic_json(path, state)
        return {"result": "CAPABILITY_ASSURANCE_CREATED" if old_target is None else "CAPABILITY_ASSURANCE_UPDATED", "capability_id": cap_id, "state_ref": capability_state_ref(cap_id), "integrated_target_sha": state["integrated_target_sha"], "pending_gates": [gid for gid, g in state["gates"].items() if g["status"] not in ("SATISFIED", "WAIVED")], "build_complete": False}
    return {"result": "CAPABILITY_ASSURANCE_PREVIEW", "capability_id": cap_id, "state_ref": capability_state_ref(cap_id), "integrated_target_sha": state["integrated_target_sha"], "state": state}


def capability_status(repo: Path, capability_id: str) -> dict[str, Any]:
    _path, state, cfg = _load_capability(repo, capability_id); _refresh_capability_gate_semantics(repo, cfg, state); validate_capability_state(state)
    return {"result": "CAPABILITY_ASSURANCE_STATUS", "capability_id": capability_id, "integrated_target_sha": state["integrated_target_sha"], "story_count": len(state["story_inputs"]), "gates": {gid: {"status": g["status"], "mode": g.get("mode"), "attestor_class": g["attestor_class"], "evidence_count": len(g["evidence_refs"]), "attestor_ref": g.get("attestor_ref")} for gid, g in sorted(state["gates"].items())}, "product_acceptance": copy.deepcopy(state["product_acceptance"]), "build_complete": state["build_complete"], "release_pointer_refs": list(state["release_pointer_refs"])}


def _capability_mutate(repo: Path, capability_id: str, apply: bool, fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    repo = r.require_coordinator(repo)
    if not apply:
        _path, state, cfg = _load_capability(repo, capability_id); working = copy.deepcopy(state); out = fn(working, cfg); validate_capability_state(working); out["mode"] = "PREVIEW_ONLY"; return out
    with r.local_lock(repo):
        path, state, cfg = _load_capability(repo, capability_id); out = fn(state, cfg); validate_capability_state(state); r.atomic_json(path, state); out["mode"] = "APPLIED"; return out


def _capability_gate(state: dict[str, Any], gate_id: str) -> dict[str, Any]:
    gates = state.get("gates", {}); r.need(gate_id in gates, f"POST_INTEGRATION capability gate {gate_id} is not required."); return gates[gate_id]


def capability_add_evidence(repo: Path, capability_id: str, gate_id: str, refs: list[str], apply: bool = False) -> dict[str, Any]:
    r.need(bool(refs), "At least one capability evidence reference is required.")
    def op(state, cfg):
        gate = _capability_gate(state, gate_id); _capability_gate_semantics(cfg, state, gate_id, gate); r.need(gate["status"] not in ("SATISFIED", "WAIVED"), "Closed capability gate is immutable until composition changes.")
        for value in refs:
            ref = _ref(value, "capability.evidence_ref")
            if ref not in gate["evidence_refs"]: gate["evidence_refs"].append(ref)
        return {"result": "CAPABILITY_EVIDENCE_ATTACHED", "gate_id": gate_id, "evidence_refs": list(gate["evidence_refs"])}
    return _capability_mutate(repo, capability_id, apply, op)


def _capability_non_attestation_count(gate: dict[str, Any]) -> int:
    return sum(1 for ref in gate.get("evidence_refs", []) if not str(ref).startswith("human-attestation:"))


def _capability_reopen_for_fresh_attestation(gate: dict[str, Any]) -> None:
    if gate.get("status") == "NEEDS_REVALIDATION":
        gate["status"] = "PENDING"; gate["evidence_refs"] = []; gate["attestor_ref"] = None; gate["observed_at"] = None; gate["waiver_ref"] = None


def capability_satisfy_tool(repo: Path, capability_id: str, gate_id: str, attestor_ref: str, refs: list[str], apply: bool = False) -> dict[str, Any]:
    attestor = _ref(attestor_ref, "capability.attestor_ref", actor=True)
    def op(state, cfg):
        gate = _capability_gate(state, gate_id); semantics = _capability_gate_semantics(cfg, state, gate_id, gate); r.need(semantics["attestor_class"] in ("TOOL", "CONTROL_PLANE"), "Capability gate requires human/composite attestation.")
        _capability_reopen_for_fresh_attestation(gate)
        r.need(gate["status"] in ("PENDING", "IN_PROGRESS", "FAILED", "BLOCKED"), "Capability gate cannot be satisfied from current state.")
        for value in refs:
            ref = _ref(value, "capability.evidence_ref")
            if ref not in gate["evidence_refs"]: gate["evidence_refs"].append(ref)
        r.need(_capability_non_attestation_count(gate) >= semantics["minimum_evidence"], "Capability minimum evidence requirement is not met.")
        gate["status"] = "SATISFIED"; gate["attestor_ref"] = attestor; gate["observed_at"] = r.now(); gate["waiver_ref"] = None
        return {"result": "CAPABILITY_ASSURANCE_SATISFIED", "gate_id": gate_id, "status": gate["status"], "attestor_ref": attestor}
    return _capability_mutate(repo, capability_id, apply, op)


def capability_attestation_request(repo: Path, capability_id: str, gate_id: str) -> dict[str, Any]:
    _path, state, cfg = _load_capability(repo, capability_id); gate = _capability_gate(state, gate_id); semantics = _capability_gate_semantics(cfg, state, gate_id, gate)
    r.need(semantics["attestor_class"] in ("ENGINEERING_OWNER", "AUTHORIZED_HUMAN", "COMPOSITE"), "Capability gate is not human/composite.")
    digest, payload = _capability_binding_digest(state, gate_id, gate); users = _capability_allowed_human_users(repo, cfg, state, gate_id, gate, semantics); rep = _representative_story(state)
    return {"result": "CAPABILITY_HUMAN_ATTESTATION_REQUIRED", "capability_id": capability_id, "gate_id": gate_id, "binding_digest": digest, "binding": payload, "allowed_forge_users": users, "expected_note": f"AE-ASSURE {gate_id} {capability_id} {digest}", "representative_mr_iid": rep["mr_iid"], "minimum_non_attestation_evidence": semantics["minimum_evidence"], "notice": "Post the exact one-line note on the representative/final capability MR from an allowed human account. Binding digest defines the capability scope."}


def capability_satisfy_human(repo: Path, capability_id: str, gate_id: str, note_id: int, refs: list[str] | None = None, apply: bool = False) -> dict[str, Any]:
    r.need(type(note_id) is int and note_id > 0, "Positive forge comment ID required."); refs = refs or []
    def op(state, cfg):
        gate = _capability_gate(state, gate_id); semantics = _capability_gate_semantics(cfg, state, gate_id, gate); r.need(semantics["attestor_class"] in ("ENGINEERING_OWNER", "AUTHORIZED_HUMAN", "COMPOSITE"), "Capability gate does not accept human attestation.")
        _capability_reopen_for_fresh_attestation(gate)
        for value in refs:
            ref = _ref(value, "capability.evidence_ref")
            if ref not in gate["evidence_refs"]: gate["evidence_refs"].append(ref)
        r.need(_capability_non_attestation_count(gate) >= semantics["minimum_evidence"], "Capability minimum non-attestation evidence requirement is not met.")
        digest, _ = _capability_binding_digest(state, gate_id, gate); expected = f"AE-ASSURE {gate_id} {capability_id} {digest}"; allowed = _capability_allowed_human_users(repo, cfg, state, gate_id, gate, semantics)
        pseudo = _pseudo_task_for_capability(state); proof = r.verify_forge_comment(repo, cfg, pseudo, note_id, expected, allowed, verify_mr=False)
        ev = _ref(proof["evidence_ref"], "capability human attestation evidence")
        if ev not in gate["evidence_refs"]: gate["evidence_refs"].append(ev)
        gate["status"] = "SATISFIED"; gate["attestor_ref"] = _ref(proof["attestor_ref"], "capability attestor_ref", actor=True); gate["observed_at"] = proof["observed_at"]; gate["waiver_ref"] = None
        return {"result": "CAPABILITY_ASSURANCE_SATISFIED", "gate_id": gate_id, "status": gate["status"], "binding_digest": digest, "attestor_identity": proof["username"], "attestor_ref": gate["attestor_ref"]}
    return _capability_mutate(repo, capability_id, apply, op)



def capability_create_waiver_request(repo: Path, capability_id: str, gate_id: str, waiver_id: str, risk_owner: str,
                                      reason: str, unmet_requirement: str, residual_risk: str,
                                      compensating_controls: list[str] | None = None, expiry: str | None = None,
                                      review_condition: str | None = None, apply: bool = False) -> dict[str, Any]:
    repo = r.require_coordinator(repo); _path, state, cfg = _load_capability(repo, capability_id); gate = _capability_gate(state, gate_id)
    r.need(gate.get("status") not in ("SATISFIED", "WAIVED"), "Closed capability gate cannot receive a new waiver draft.")
    _capability_gate_semantics(cfg, state, gate_id, gate); _rule, owners = _waiver_policy(cfg, gate_id); risk_owner = _ref(risk_owner, "waiver.risk_owner", actor=True)
    r.need(risk_owner in owners, "Requested capability waiver risk owner is not authorized by project policy.")
    binding_digest, _ = _capability_binding_digest(state, gate_id, gate)
    record = {
        "schema_version": 1, "waiver_id": r.ident(waiver_id), "gate_id": gate_id, "scope_type": "CAPABILITY", "scope_id": capability_id,
        "binding_digest": binding_digest, "reason": _ref(reason, "waiver.reason"), "unmet_requirement": _ref(unmet_requirement, "waiver.unmet_requirement"),
        "residual_risk": _ref(residual_risk, "waiver.residual_risk"), "compensating_controls": list(compensating_controls or []),
        "risk_owner": risk_owner, "approval_ref": None, "expiry": expiry, "review_condition": review_condition,
    }
    _validate_waiver_record(record, approval_allowed=False); digest = waiver_digest(record); wpath = _waiver_path(repo, waiver_id); rep = _representative_story(state)
    if apply:
        if wpath.exists():
            existing = r.read_json(wpath); _validate_waiver_record(existing); r.need(waiver_digest(existing) == digest and existing.get("approval_ref") is None, "Existing waiver draft differs or is already approved; use a new waiver_id.")
        else: r.atomic_json(wpath, record)
    return {"result": "CAPABILITY_WAIVER_REQUEST_READY", "mode": "APPLIED" if apply else "PREVIEW_ONLY", "waiver_id": waiver_id, "waiver_ref": wpath.resolve().relative_to(repo).as_posix(), "gate_id": gate_id, "binding_digest": binding_digest, "waiver_digest": digest, "risk_owner": risk_owner, "representative_mr_iid": rep["mr_iid"], "expected_note": f"AE-WAIVE {gate_id} {capability_id} {binding_digest} {digest}", "notice": "Capability WAIVED is not PASS; post the exact note from the authorized risk-owner account."}


def capability_apply_waiver(repo: Path, capability_id: str, gate_id: str, waiver_id: str, note_id: int, apply: bool = False) -> dict[str, Any]:
    r.need(type(note_id) is int and note_id > 0, "Positive forge comment ID required.")
    def op(state, cfg):
        gate = _capability_gate(state, gate_id); _capability_gate_semantics(cfg, state, gate_id, gate); r.need(gate.get("status") not in ("SATISFIED", "WAIVED"), "Closed capability gate cannot be waived again.")
        _rule, owners = _waiver_policy(cfg, gate_id); wpath = _waiver_path(repo, waiver_id); r.need(wpath.exists(), "Capability waiver draft does not exist.")
        record = r.read_json(wpath); _validate_waiver_record(record, approval_allowed=False); digest, _ = _capability_binding_digest(state, gate_id, gate)
        r.need(record["scope_type"] == "CAPABILITY" and record["gate_id"] == gate_id and record["scope_id"] == capability_id and record["binding_digest"] == digest, "Capability waiver draft is bound to another gate/scope/version.")
        r.need(record["risk_owner"] in owners, "Capability waiver risk owner is no longer authorized.")
        wdigest = waiver_digest(record); expected = f"AE-WAIVE {gate_id} {capability_id} {digest} {wdigest}"; proof = r.verify_forge_comment(repo, cfg, _pseudo_task_for_capability(state), note_id, expected, [record["risk_owner"]], verify_mr=False)
        record["approval_ref"] = proof["attestor_ref"]
        if apply: r.atomic_json(wpath, record)
        gate["status"] = "WAIVED"; gate["waiver_ref"] = wpath.resolve().relative_to(repo).as_posix(); gate["attestor_ref"] = proof["attestor_ref"]; gate["observed_at"] = proof["observed_at"]
        if proof["evidence_ref"] not in gate["evidence_refs"]: gate["evidence_refs"].append(proof["evidence_ref"])
        return {"result": "CAPABILITY_ASSURANCE_WAIVED", "gate_id": gate_id, "status": "WAIVED", "waiver_id": waiver_id, "waiver_ref": gate["waiver_ref"], "risk_owner": record["risk_owner"], "attestor_ref": proof["attestor_ref"], "notice": "WAIVED is an auditable exception, not SATISFIED."}
    return _capability_mutate(repo, capability_id, apply, op)


def _verify_capability_waiver(repo: Path, cfg: dict[str, Any], state: dict[str, Any], gate_id: str, gate: dict[str, Any], *, verify_server: bool) -> dict[str, Any]:
    _capability_gate_semantics(cfg, state, gate_id, gate); _rule, owners = _waiver_policy(cfg, gate_id); rel = gate.get("waiver_ref")
    r.need(isinstance(rel, str) and rel.startswith(".agentic/local/waivers/") and ".." not in Path(rel).parts, "Capability WAIVED gate has invalid waiver_ref.")
    path = Path(repo) / rel; r.ensure_no_links(path, repo); r.need(path.is_file(), "Capability bounded waiver record missing.")
    record = r.read_json(path); _validate_waiver_record(record, approval_allowed=True); digest, _ = _capability_binding_digest(state, gate_id, gate)
    r.need(record["scope_type"] == "CAPABILITY" and record["scope_id"] == state["capability_id"] and record["gate_id"] == gate_id and record["binding_digest"] == digest, "Capability waiver is bound to another scope/version.")
    r.need(record["risk_owner"] in owners and record.get("approval_ref") == gate.get("attestor_ref"), "Capability waiver authority/provenance invalid.")
    expiry = _parse_expiry(record.get("expiry"))
    if expiry is not None: r.need(expiry > dt.datetime.now(dt.timezone.utc), "Capability waiver expired.")
    wdigest = waiver_digest(record); expected = f"AE-WAIVE {gate_id} {state['capability_id']} {digest} {wdigest}"; ref = _ref(gate.get("attestor_ref"), "capability waiver attestor", actor=True); m = re.fullmatch(r"gitlab:mr:(\d+):note:(\d+):user:([^:\r\n]+)", ref); r.need(m is not None, "Capability waiver server provenance invalid.")
    mr_iid, note_id, username = int(m.group(1)), int(m.group(2)), m.group(3); r.need(mr_iid == _representative_story(state)["mr_iid"] and username == record["risk_owner"], "Capability waiver note is not bound to current representative MR/risk owner.")
    if verify_server:
        proof = r.verify_forge_comment(repo, cfg, _pseudo_task_for_capability(state), note_id, expected, [record["risk_owner"]], verify_mr=False); r.need(proof["attestor_ref"] == record["approval_ref"], "Capability waiver server provenance mismatch.")
    return {"gate_id": gate_id, "status": "WAIVED", "waiver_id": record["waiver_id"], "risk_owner": record["risk_owner"], "residual_risk": record["residual_risk"], "binding_digest": digest, "server_verified": verify_server}

def capability_product_accept(repo: Path, capability_id: str, evidence_ref: str, actor_ref: str, apply: bool = False) -> dict[str, Any]:
    evidence = _ref(evidence_ref, "product_acceptance.evidence_ref"); actor = _ref(actor_ref, "product_acceptance.actor_ref", actor=True)
    def op(state, cfg):
        state["product_acceptance"] = {"status": "SATISFIED", "evidence_ref": evidence, "actor_ref": actor, "observed_at": r.now(), "integrated_target_sha": state["integrated_target_sha"]}
        state["build_complete"] = False
        return {"result": "PRODUCT_CAPABILITY_ACCEPTANCE_RECORDED", "capability_id": capability_id, "integrated_target_sha": state["integrated_target_sha"], "evidence_ref": evidence, "actor_ref": actor, "notice": "This records an authority/evidence reference; the helper does not infer product truth from the string itself."}
    return _capability_mutate(repo, capability_id, apply, op)


def _verify_capability_gate(repo: Path, cfg: dict[str, Any], state: dict[str, Any], gate_id: str, gate: dict[str, Any], *, verify_server: bool) -> dict[str, Any]:
    semantics = _capability_gate_semantics(cfg, state, gate_id, gate); digest = capability_digest(state)
    r.need(gate["binding"].get("contract_digest") == digest and gate["binding"].get("integrated_target_sha") == state["integrated_target_sha"], f"Capability gate {gate_id} binding is stale.")
    r.need(gate.get("status") == "SATISFIED", f"Capability gate {gate_id} is not SATISFIED.")
    r.need(_capability_non_attestation_count(gate) >= semantics["minimum_evidence"], f"Capability gate {gate_id} lacks minimum evidence.")
    if semantics["attestor_class"] in ("ENGINEERING_OWNER", "AUTHORIZED_HUMAN", "COMPOSITE"):
        ref = _ref(gate.get("attestor_ref"), "capability attestor_ref", actor=True); m = re.fullmatch(r"gitlab:mr:(\d+):note:(\d+):user:([^:\r\n]+)", ref); r.need(m is not None, "Capability human attestor_ref invalid.")
        mr_iid, note_id, username = int(m.group(1)), int(m.group(2)), m.group(3); rep = _representative_story(state); r.need(mr_iid == rep["mr_iid"], "Capability attestation is not attached to the current representative MR.")
        bdigest, _ = _capability_binding_digest(state, gate_id, gate); expected = f"AE-ASSURE {gate_id} {state['capability_id']} {bdigest}"; allowed = _capability_allowed_human_users(repo, cfg, state, gate_id, gate, semantics); r.need(username in allowed, "Capability attestor is no longer authorized.")
        if verify_server:
            proof = r.verify_forge_comment(repo, cfg, _pseudo_task_for_capability(state), note_id, expected, allowed, verify_mr=False); r.need(proof["attestor_ref"] == ref, "Capability server attestation provenance mismatch.")
        return {"gate_id": gate_id, "status": "SATISFIED", "attestor_identity": username, "binding_digest": bdigest, "server_verified": verify_server}
    return {"gate_id": gate_id, "status": "SATISFIED", "attestor_ref": gate.get("attestor_ref"), "server_verified": False}


def capability_closure(repo: Path, capability_id: str, *, verify_server: bool = False) -> dict[str, Any]:
    _path, state, cfg = _load_capability(repo, capability_id); blocking = []; closed = []
    for gate_id, gate in sorted(state["gates"].items()):
        if gate.get("status") == "SATISFIED":
            try: closed.append(_verify_capability_gate(repo, cfg, state, gate_id, gate, verify_server=verify_server))
            except r.AEError as exc: blocking.append({"gate_id": gate_id, "status": "INVALID", "error": str(exc)})
        elif gate.get("status") == "WAIVED":
            try: closed.append(_verify_capability_waiver(repo, cfg, state, gate_id, gate, verify_server=verify_server))
            except r.AEError as exc: blocking.append({"gate_id": gate_id, "status": "INVALID_WAIVER", "error": str(exc)})
        else: blocking.append({"gate_id": gate_id, "status": gate.get("status")})
    if state["product_acceptance"]["status"] != "SATISFIED" or state["product_acceptance"].get("integrated_target_sha") != state["integrated_target_sha"]:
        blocking.append({"gate_id": "product_capability_acceptance", "status": state["product_acceptance"]["status"]})
    return {"result": "CAPABILITY_ACCEPTANCE_CLOSED" if not blocking else "CAPABILITY_ACCEPTANCE_PENDING", "capability_id": capability_id, "integrated_target_sha": state["integrated_target_sha"], "closed": closed, "blocking": blocking, "build_complete_eligible": not blocking}


def _capability_versioned_record(state: dict[str, Any], closure: dict[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(state)
    record["record_type"] = "CAPABILITY_ASSURANCE"
    record["assurance_closed_at"] = r.now()
    record["build_complete"] = True
    # Versioned records expose waiver identity/provenance without depending on local-only paths.
    for gate in record["gates"].values():
        if gate.get("status") == "WAIVED":
            rel = gate.get("waiver_ref")
            if isinstance(rel, str): gate["waiver_ref"] = "waiver:" + Path(rel).stem
    return record


def _validate_capability_record(record: dict[str, Any]) -> dict[str, Any]:
    r.need(isinstance(record, dict) and record.get("record_type") == "CAPABILITY_ASSURANCE" and r.text(record.get("assurance_closed_at")), "Capability versioned assurance record invalid.")
    state = copy.deepcopy(record); state.pop("record_type"); state.pop("assurance_closed_at"); validate_capability_state(state); r.need(state["build_complete"] is True, "Capability record must represent BUILD COMPLETE."); return record


def capability_finalize(repo: Path, capability_id: str, apply: bool = False) -> dict[str, Any]:
    repo = r.require_coordinator(repo); closure = capability_closure(repo, capability_id, verify_server=apply); r.need(closure["build_complete_eligible"], "Capability is not ready for BUILD COMPLETE: " + ", ".join(f"{x['gate_id']}={x['status']}" for x in closure["blocking"]))
    path, state, cfg = _load_capability(repo, capability_id); record = _capability_versioned_record(state, closure); rel = capability_record_ref(capability_id); outpath = Path(repo) / rel
    if not apply: return {"result": "CAPABILITY_BUILD_COMPLETE_READY", "capability_id": capability_id, "record_ref": rel, "closure": closure, "mode": "PREVIEW_ONLY"}
    with r.local_lock(repo):
        path, state, cfg = _load_capability(repo, capability_id); closure = capability_closure(repo, capability_id, verify_server=True); r.need(closure["build_complete_eligible"], "Capability changed before finalization; reassess.")
        state["build_complete"] = True; validate_capability_state(state); r.atomic_json(path, state)
        record = _capability_versioned_record(state, closure); outpath.parent.mkdir(parents=True, exist_ok=True); r.ensure_no_links(outpath.parent, repo)
        if outpath.exists():
            old = r.read_json(outpath); _validate_capability_record(old)
            old_key = (old["capability_id"], old["integrated_target_sha"], [(x["story_id"], x["merge_result_sha"]) for x in old["story_inputs"]])
            new_key = (record["capability_id"], record["integrated_target_sha"], [(x["story_id"], x["merge_result_sha"]) for x in record["story_inputs"]])
            r.need(old_key == new_key, "Existing capability assurance record belongs to another integrated composition; publish/commit it before replacing with a new capability version.")
        r.atomic_json(outpath, record)
        # Bind the versioned capability record back into each local Story and Story index.
        for item in state["story_inputs"]:
            tp = r.task_path(repo, item["developer"], item["story_id"]); r.need(tp.is_file(), f"Local task missing for capability Story {item['story_id']}."); task = r.read_json(tp); r.validate_task_schema3(task); task["traceability"]["capability_record_ref"] = rel
            for ob in task.get("assurance", {}).get("downstream_obligations", []):
                if ob.get("stage") == "POST_INTEGRATION" and ob.get("capability_id") == capability_id: ob["registry_ref"] = rel
            r.atomic_json(tp, task)
            trace_path = Path(repo) / item["traceability_ref"]; tr = r.read_json(trace_path); r.validate_traceability_index(tr); tr["downstream"]["capability_record_ref"] = rel; r.atomic_json(trace_path, tr)
        return {"result": "BUILD_COMPLETE", "capability_id": capability_id, "integrated_target_sha": state["integrated_target_sha"], "record_ref": rel, "publication_required": True, "release_pointer_refs": list(state["release_pointer_refs"]), "notice": "BUILD COMPLETE is not deployment/release authorization. Publish the capability and updated Story records on the records branch."}

def status(repo: Path, task_arg: Path) -> dict[str, Any]:
    _, task, cfg = _load(repo, task_arg)
    for gate_id, gate in task["assurance"]["pre_merge"].items():
        _assert_gate_semantic_integrity(cfg, task, gate_id, gate)
    return summarize(task)


def check_pre_merge(repo: Path, task_arg: Path) -> dict[str, Any]:
    _, task, cfg = _load(repo, task_arg)
    for gate_id, gate in task["assurance"]["pre_merge"].items():
        _assert_gate_semantic_integrity(cfg, task, gate_id, gate)
    return premerge_closure(task)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("status")
    q.add_argument("--task", type=Path, required=True)
    q = sub.add_parser("check-pre-merge")
    q.add_argument("--task", type=Path, required=True)
    q = sub.add_parser("human-request")
    q.add_argument("--task", type=Path, required=True)
    q.add_argument("--gate", required=True)
    q = sub.add_parser("waiver-request")
    q.add_argument("--task", type=Path, required=True);q.add_argument("--gate", required=True);q.add_argument("--waiver-id", required=True);q.add_argument("--risk-owner", required=True)
    q.add_argument("--reason", required=True);q.add_argument("--unmet-requirement", required=True);q.add_argument("--residual-risk", required=True);q.add_argument("--compensating-control", action="append", default=[]);q.add_argument("--expiry");q.add_argument("--review-condition");q.add_argument("--apply", action="store_true")
    q = sub.add_parser("apply-waiver")
    q.add_argument("--task", type=Path, required=True);q.add_argument("--gate", required=True);q.add_argument("--waiver-id", required=True);q.add_argument("--note-id", type=int, required=True);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("revalidate-auto")
    q.add_argument("--task", type=Path, required=True);q.add_argument("--gate", required=True);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("revalidate")
    q.add_argument("--task", type=Path, required=True);q.add_argument("--gate", required=True);q.add_argument("--disposition", choices=sorted(REVALIDATION_DISPOSITIONS), required=True)
    q.add_argument("--assessor-class", choices=sorted(REVALIDATION_ASSESSORS), required=True);q.add_argument("--assessor-ref", required=True);q.add_argument("--rationale-ref", required=True);q.add_argument("--apply", action="store_true")

    q = sub.add_parser("capability-status");q.add_argument("--capability", required=True)
    q = sub.add_parser("capability-check");q.add_argument("--capability", required=True);q.add_argument("--verify-server", action="store_true")
    q = sub.add_parser("capability-evidence");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True);q.add_argument("--evidence-ref", action="append", default=[]);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-satisfy-tool");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True);q.add_argument("--attestor-ref", required=True);q.add_argument("--evidence-ref", action="append", default=[]);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-human-request");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True)
    q = sub.add_parser("capability-satisfy-human");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True);q.add_argument("--note-id", type=int, required=True);q.add_argument("--evidence-ref", action="append", default=[]);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-waiver-request");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True);q.add_argument("--waiver-id", required=True);q.add_argument("--risk-owner", required=True);q.add_argument("--reason", required=True);q.add_argument("--unmet-requirement", required=True);q.add_argument("--residual-risk", required=True);q.add_argument("--compensating-control", action="append", default=[]);q.add_argument("--expiry");q.add_argument("--review-condition");q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-apply-waiver");q.add_argument("--capability", required=True);q.add_argument("--gate", required=True);q.add_argument("--waiver-id", required=True);q.add_argument("--note-id", type=int, required=True);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-product-accept");q.add_argument("--capability", required=True);q.add_argument("--evidence-ref", required=True);q.add_argument("--actor-ref", required=True);q.add_argument("--apply", action="store_true")
    q = sub.add_parser("capability-finalize");q.add_argument("--capability", required=True);q.add_argument("--apply", action="store_true")

    for name in ("evidence", "begin", "fail", "block", "satisfy-tool", "satisfy-human"):
        q = sub.add_parser(name)
        q.add_argument("--task", type=Path, required=True)
        q.add_argument("--gate", required=True)
        q.add_argument("--apply", action="store_true")
        if name in ("evidence", "fail", "block", "satisfy-tool", "satisfy-human"):
            q.add_argument("--evidence-ref", action="append", default=[])
        if name in ("begin", "fail", "block"):
            q.add_argument("--actor-ref", required=True)
        if name == "satisfy-tool":
            q.add_argument("--attestor-ref", required=True)
        if name == "satisfy-human":
            q.add_argument("--note-id", type=int, required=True)

    a = p.parse_args()
    try:
        repo = r.root(a.repo)
        r.need(repo == Path(__file__).resolve().parents[2], "Run the installed helper from the coordinator root.")
        if a.command == "status": out = status(repo, a.task)
        elif a.command == "check-pre-merge": out = check_pre_merge(repo, a.task)
        elif a.command == "human-request": out = attestation_request(repo, a.task, a.gate)
        elif a.command == "waiver-request": out = create_waiver_request(repo,a.task,a.gate,a.waiver_id,a.risk_owner,a.reason,a.unmet_requirement,a.residual_risk,a.compensating_control,a.expiry,a.review_condition,a.apply)
        elif a.command == "apply-waiver": out = apply_waiver(repo,a.task,a.gate,a.waiver_id,a.note_id,a.apply)
        elif a.command == "revalidate-auto": out = revalidate_auto(repo,a.task,a.gate,a.apply)
        elif a.command == "revalidate": out = revalidate_assess(repo,a.task,a.gate,a.disposition,a.assessor_class,a.assessor_ref,a.rationale_ref,a.apply)
        elif a.command == "capability-status": out = capability_status(repo,a.capability)
        elif a.command == "capability-check": out = capability_closure(repo,a.capability,verify_server=a.verify_server)
        elif a.command == "capability-evidence": out = capability_add_evidence(repo,a.capability,a.gate,a.evidence_ref,a.apply)
        elif a.command == "capability-satisfy-tool": out = capability_satisfy_tool(repo,a.capability,a.gate,a.attestor_ref,a.evidence_ref,a.apply)
        elif a.command == "capability-human-request": out = capability_attestation_request(repo,a.capability,a.gate)
        elif a.command == "capability-satisfy-human": out = capability_satisfy_human(repo,a.capability,a.gate,a.note_id,a.evidence_ref,a.apply)
        elif a.command == "capability-waiver-request": out = capability_create_waiver_request(repo,a.capability,a.gate,a.waiver_id,a.risk_owner,a.reason,a.unmet_requirement,a.residual_risk,a.compensating_control,a.expiry,a.review_condition,a.apply)
        elif a.command == "capability-apply-waiver": out = capability_apply_waiver(repo,a.capability,a.gate,a.waiver_id,a.note_id,a.apply)
        elif a.command == "capability-product-accept": out = capability_product_accept(repo,a.capability,a.evidence_ref,a.actor_ref,a.apply)
        elif a.command == "capability-finalize": out = capability_finalize(repo,a.capability,a.apply)
        elif a.command == "evidence": out = add_evidence(repo, a.task, a.gate, a.evidence_ref, a.apply)
        elif a.command == "begin": out = begin(repo, a.task, a.gate, a.actor_ref, a.apply)
        elif a.command == "fail": out = disposition(repo, a.task, a.gate, "FAILED", a.actor_ref, a.evidence_ref, a.apply)
        elif a.command == "block": out = disposition(repo, a.task, a.gate, "BLOCKED", a.actor_ref, a.evidence_ref, a.apply)
        elif a.command == "satisfy-tool": out = satisfy_tool(repo, a.task, a.gate, a.attestor_ref, a.evidence_ref, a.apply)
        else: out = satisfy_human(repo, a.task, a.gate, a.note_id, a.evidence_ref, a.apply)
        print(json.dumps(out, indent=2, ensure_ascii=False)); return 0
    except (r.AEError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return r.cli_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
