#!/usr/bin/env python3
"""The Backlog Preparation -> Control Plane handoff interface, and contract generation.

## Why this module exists

Both packages had well-specified internal schemas and **no specified interface between
them**. Authoring a normalized Story contract from a Story card took three human
judgements per Story in the v0.6 pilot, and the same three recurred for every Story:

1. **Reference format was incompatible.** A card declared
   `"scope_ref": "STORY-001§Scope, Exclusions & Preserve"`. `contract.REF_ID` allows no
   spaces and no `§`, so it had to be translated by hand to
   `backlog/stories/STORY-001.md#scope-exclusions-preserve` -- which required knowing the
   heading *and* the anchor-slug convention.
2. **Required fields had no source at all.** `build_start_ref` and `final_acceptance_ref`
   are mandatory in the contract and appear nowhere in `bp-meta`; they were read off the
   Feature card's headings by hand. So were `dependency_ref` and each obligation's
   `evidence_plan_ref`.
3. **Types disagreed.** The card said `"Tier 1"`; the contract requires the integer `1`.
   The card carried one `traceability_root` string; the contract requires a list.

Every one of those was an opportunity to get an authority reference subtly wrong, and
nothing checked the result until much later.

## What this module defines

A **shared versioned interface contract**, `HANDOFF_SCHEMA_VERSION`, owned jointly and
versioned independently of either package. Backlog Preparation emits complete
machine-readable metadata; Control Plane **generates** the normalized execution contract
from it deterministically.

That makes the normalized contract a **derived artifact**. Hand-authoring it stops being
the supported path, which is precisely what removes the three judgements above.

## Backward compatibility

`bp-meta` schema 1 cards -- every card in a backlog prepared before this -- are still
accepted for at least one release. `upgrade_legacy_meta` converts what can be converted
mechanically and **reports what cannot**, because three of the missing fields have no
source in schema 1 and inventing them would be worse than asking.

For those, generation takes explicit overrides. The judgement a human made invisibly in
v0.6 is still a human judgement; it is now named, validated and recorded in the result
rather than left in someone's head.

**One caveat the warnings state plainly:** a translated legacy reference is *syntactically*
valid, but nothing here proves the anchor it names exists in the document. The pilot's own
`preserve_ref` is the example -- a human wrote `#preserve` where the mechanical slug of
that heading is `#scope-exclusions-preserve-preserve`. Check translated references, or
override them.

## What this module deliberately does NOT do

It does not resolve current authority, and it does not derive check applicability. Those
are F7/A2 and F3/C2, and both belong to a later wave. `authority_pointer` is reserved and
validated here so that adding it later is additive rather than breaking, and it is
otherwise **ignored**: nothing in this module or in contract generation reads it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import contract as c
    import runtime as r
except ModuleNotFoundError:  # pragma: no cover
    from . import contract as c
    from . import runtime as r

# The shared interface version, independent of both packages' own schema numbers. A
# minor addition (a new optional field) keeps this number; anything that changes the
# meaning of an existing field, or adds a required one, raises it.
HANDOFF_SCHEMA_VERSION = 1

# What Backlog Preparation stamps on a Story card that speaks this interface.
BP_META_SCHEMA_VERSION = 2
LEGACY_BP_META_SCHEMA_VERSION = 1

_META_BLOCK = re.compile(r"```bp-meta\s*\n(.*?)\n```", re.DOTALL)
_TIER = re.compile(r"\A\s*(?:Tier\s*)?([123])\s*\Z", re.IGNORECASE)
_SECTION_REF = re.compile(r"\A\s*(?P<doc>[^§]+?)\s*§\s*(?P<heading>.+?)\s*\Z")

# Fields the normalized contract needs and the card must therefore carry. Each one is a
# reference in `path#anchor` form that contract.REF_ID accepts without translation.
REFERENCE_FIELDS = ("scope_ref", "preserve_ref", "dependency_ref",
                    "build_start_ref", "final_acceptance_ref")

# Present in schema 1 and still carried, but not consumed when generating a contract.
# Listed so the validator can accept them without silently tolerating typos.
INFORMATIONAL_FIELDS = ("requirement_refs", "dependencies",
                        "stop_conditions", "implementation_state", "exclusions_ref",
                        "feature_id")

# Where an acceptance criterion's evidence is produced, and by whom. Carried from
# schema 1 unchanged; the vocabulary was already stable there.
EVIDENCE_STAGES = ("BUILD", "REVIEW", "QA", "POST_INTEGRATION")
EVIDENCE_OWNERS = ("story_builder", "reviewer", "qa", "engineering_owner", "control_plane")
EVIDENCE_STATUSES = ("NOT_RUN", "PLANNED")

# `kind` is what makes a check derivable (F3/C2): the project maps kinds to the checks
# they can satisfy. It is a free identifier rather than a fixed vocabulary, because the
# set of things a team can produce evidence of is not ours to enumerate -- but a kind the
# project has not mapped is a configuration gap, never a silent skip.
EVIDENCE_KIND = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")

# Reserved for the current-authority pointer (F7/A2). Validated if present so that a
# card carrying it is not rejected, and read by nothing.
RESERVED_FIELDS = ("authority_pointer",)


class HandoffError(r.AEError):
    """A Story card cannot satisfy the handoff interface.

    Deliberately a subclass of AEError. Validation here mixes this module's own checks
    with contract.py's field helpers, which raise AEError, and a caller should not have
    to know which field tripped in order to catch the failure. Every helper in this kit
    already catches AEError; this stays catchable the same way while remaining
    distinguishable where that matters.
    """


def _need(condition, message):
    if not condition:
        raise HandoffError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def slugify(heading: str) -> str:
    """The anchor convention: lowercase, non-alphanumerics collapsed to single hyphens.

    `Scope, Exclusions & Preserve` -> `scope-exclusions-preserve`. Used only to translate
    a legacy `Doc§Heading` reference; a card speaking schema 2 states the anchor itself.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", heading.strip().lower()).strip("-")
    _need(bool(slug), f"Heading {heading!r} does not produce a usable anchor.")
    return slug


def read_bp_meta(text: str) -> dict[str, Any]:
    """Extract the single ```bp-meta block from a Story card."""
    blocks = _META_BLOCK.findall(text)
    _need(blocks, "No ```bp-meta block found in this Story card.")
    _need(len(blocks) == 1, f"A Story card must carry exactly one bp-meta block; found {len(blocks)}.")
    try:
        meta = json.loads(blocks[0])
    except ValueError as exc:
        raise HandoffError(f"The bp-meta block is not valid JSON: {exc}") from exc
    _need(isinstance(meta, dict), "The bp-meta block must be a JSON object.")
    return meta


def read_story_card(repo: str | Path, story_path: str) -> dict[str, Any]:
    """Read the card from the working tree. For the pre-handoff check only."""
    repo = Path(repo)
    c._relpath(story_path, "story_card_path")
    path = repo / story_path
    _need(path.is_file(), f"Story card not found at {story_path}.")
    return read_bp_meta(path.read_text(encoding="utf-8-sig"))


def read_story_card_at(repo: str | Path, publication_sha: str, story_path: str) -> dict[str, Any]:
    """Read the card **as published**, at the commit the contract will be bound to.

    Generation must not mix sources. Reading metadata from the working tree while binding
    blob SHAs to a publication commit produces a contract that can state a revision the
    commit does not contain -- which is exactly the authority claim the publication
    binding exists to make true. Caught against the pilot's own backlog, where the
    working tree carries REV-03 and the Story's publication commit carries REV-02.
    """
    c._relpath(story_path, "story_card_path")
    c._sha(publication_sha, "publication_sha")
    code, out = r.git(repo, "show", f"{publication_sha}:{story_path}", allowed=(0, 128))
    _need(code == 0, f"{story_path} does not exist at publication commit {publication_sha[:12]}.")
    return read_bp_meta(out)


def _tier(value: Any) -> int:
    """Accept 1 or "Tier 1" during the transition; emit the integer."""
    if type(value) is int:
        _need(value in (1, 2, 3), "risk_tier must be 1, 2 or 3.")
        return value
    _need(_text(value), "risk_tier is missing.")
    match = _TIER.fullmatch(value)
    _need(match is not None, f"risk_tier {value!r} is not a tier; use the integer 1, 2 or 3.")
    return int(match.group(1))


def _translate_section_ref(value: str, default_doc_path: str, docs: dict[str, str]) -> str:
    """Turn `STORY-001§Some Heading` into `backlog/stories/STORY-001.md#some-heading`."""
    match = _SECTION_REF.fullmatch(value)
    _need(match is not None, f"Reference {value!r} is neither path#anchor nor Doc§Heading.")
    doc = match.group("doc").strip()
    path = docs.get(doc, default_doc_path)
    return f"{path}#{slugify(match.group('heading'))}"


_OBLIGATION_OVERRIDE = re.compile(r"\Arequired_assurance\[(\d+)\]\.evidence_plan_ref\Z")
_EVIDENCE_OVERRIDE = re.compile(r"\Aevidence_expectations\[(\d+)\]\.kind\Z")


def _check_override_keys(overrides: dict[str, Any]) -> None:
    """Refuse an override that names no field. A silently dropped one is worse than none.

    Without this, `--ref build_start_re=...` is ignored and `build_start_ref` is then
    reported missing anyway -- the operator supplied something and is told it is absent,
    with nothing connecting the two.
    """
    for key in overrides:
        _need(key in REFERENCE_FIELDS or _OBLIGATION_OVERRIDE.fullmatch(key)
              or _EVIDENCE_OVERRIDE.fullmatch(key),
              f"{key!r} is not a field an override can supply. Use one of "
              f"{', '.join(REFERENCE_FIELDS)}, required_assurance[N].evidence_plan_ref, "
              f"or evidence_expectations[N].kind.")


def _apply_evidence_overrides(meta: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
    """Fill each evidence expectation's `kind` from an override. Returns what is missing.

    Schema 1 described evidence only in prose, so no kind can be derived from it. The
    project's check mapping keys off `kind`, which means guessing one would silently
    decide which checks a Story must satisfy -- the exact discretion F3/C2 removes.
    """
    missing: list[str] = []
    expectations = meta.get("evidence_expectations")
    if not isinstance(expectations, list):
        return missing
    filled = []
    for i, item in enumerate(expectations):
        _need(isinstance(item, dict), f"evidence_expectations[{i}] must be an object.")
        item = dict(item)
        key = f"evidence_expectations[{i}].kind"
        if key in overrides:
            item["kind"] = overrides[key]
        elif not _text(item.get("kind")):
            missing.append(key)
        filled.append(item)
    meta["evidence_expectations"] = filled
    return missing


def _apply_obligation_overrides(meta: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
    """Fill each obligation's evidence_plan_ref from an override. Returns what is missing.

    Shared by both paths on purpose: a schema-2 card and a legacy one must treat an
    override identically, or the same command means two things.
    """
    missing: list[str] = []
    obligations = meta.get("required_assurance")
    if not isinstance(obligations, list):
        return missing
    filled = []
    for i, item in enumerate(obligations):
        _need(isinstance(item, dict), f"required_assurance[{i}] must be an object.")
        item = dict(item)
        key = f"required_assurance[{i}].evidence_plan_ref"
        if key in overrides:
            item["evidence_plan_ref"] = overrides[key]
        elif not _text(item.get("evidence_plan_ref")):
            missing.append(key)
        filled.append(item)
    meta["required_assurance"] = filled
    return missing


def upgrade_legacy_meta(meta: dict[str, Any], *, story_path: str,
                        overrides: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    """Convert a schema-1 bp-meta block to schema 2 in memory.

    Returns (meta, warnings, missing). `missing` names the fields schema 1 has no source
    for; they are supplied through `overrides` or the handoff is not ready. Nothing is
    invented: a field that cannot be derived is reported, never guessed.
    """
    overrides = dict(overrides or {})
    _check_override_keys(overrides)
    out = dict(meta)
    warnings: list[str] = []
    missing: list[str] = []

    out["artifact_schema_version"] = BP_META_SCHEMA_VERSION
    out["handoff_schema_version"] = HANDOFF_SCHEMA_VERSION
    out.setdefault("story_card_path", story_path)

    if not isinstance(meta.get("risk_tier"), int):
        out["risk_tier"] = _tier(meta.get("risk_tier"))
        warnings.append(f'risk_tier {meta.get("risk_tier")!r} was read as the integer {out["risk_tier"]}; '
                        f'schema 2 states it as an integer.')

    root = meta.get("traceability_root")
    if isinstance(root, str):
        out["traceability_root"] = [root]
        warnings.append("traceability_root was a single string and is now a list; schema 2 states a list.")

    docs = {Path(story_path).stem: story_path}
    backlog_path = meta.get("canonical_backlog_path")
    if _text(backlog_path):
        docs[Path(backlog_path).stem] = backlog_path

    for field in REFERENCE_FIELDS:
        if field in overrides:
            continue
        value = meta.get(field)
        if not _text(value):
            missing.append(field)
            continue
        if "§" in value:
            out[field] = _translate_section_ref(value, story_path, docs)
            warnings.append(f'{field} {value!r} was translated to {out[field]!r}. The anchor is a mechanical '
                            f'slug of the heading and is not checked against the document; verify it or '
                            f'override it.')

    missing.extend(_apply_obligation_overrides(out, overrides))
    missing.extend(_apply_evidence_overrides(out, overrides))

    for field in REFERENCE_FIELDS:
        if field in overrides:
            out[field] = overrides[field]
            warnings.append(f"{field} was supplied as an override, not by the Story card.")
    return out, warnings, sorted(set(missing))


AUTHORITY_POINTER_FIELDS = {"feature_id", "current_revision", "revision_published_at",
                            "superseded_revisions"}


def validate_authority_pointer(pointer: Any) -> dict[str, Any]:
    """Validate the current-authority pointer (F7/A2).

    **Backlog Preparation publishes this, on the Feature card. Control Plane reads it and
    never writes it** — writing it would make Control Plane a second authority over a
    backlog it does not own.

    It is on the *Feature* card deliberately. A Story card cannot be trusted to say what
    is currently in force, because a Story bound to a superseded revision is exactly the
    condition being detected: it would vouch for itself.

    Minimum contents are `feature_id` and `current_revision`. `revision_published_at` is
    optional — its absence degrades the verdict rather than faking one — and
    `superseded_revisions` lets a known-superseded revision be told apart from one the
    Feature has never heard of.
    """
    _need(isinstance(pointer, dict), "authority_pointer must be an object.")
    unknown = sorted(set(pointer) - AUTHORITY_POINTER_FIELDS)
    _need(not unknown, f"authority_pointer has unsupported fields: {unknown}")
    _need({"feature_id", "current_revision"}.issubset(pointer),
          "authority_pointer must state feature_id and current_revision.")
    c._ident(pointer.get("feature_id"), "authority_pointer.feature_id")
    c._nonblank(pointer.get("current_revision"), "authority_pointer.current_revision")
    published = pointer.get("revision_published_at")
    if published is not None:
        c._sha(published, "authority_pointer.revision_published_at")
    superseded = pointer.get("superseded_revisions", [])
    _need(isinstance(superseded, list) and all(_text(x) for x in superseded),
          "authority_pointer.superseded_revisions must be a list of revision names.")
    return pointer


def read_authority_pointer_at(repo: str | Path, reference: str, feature_path: str):
    """Read the Feature card's authority pointer at a Git reference.

    Returns (pointer, reason). A `None` pointer always comes with a reason, and the
    caller reports it as undetermined — never as current. Not being able to ask is a
    different answer from being told, and collapsing the two is how a stale contract
    passes.
    """
    code, out = r.git(repo, "show", f"{reference}:{feature_path}", allowed=(0, 128))
    if code != 0:
        return None, "AUTHORITY_SOURCE_ABSENT"
    try:
        meta = read_bp_meta(out)
    except r.AEError:
        return None, "AUTHORITY_SOURCE_UNREADABLE"
    pointer = meta.get("authority_pointer")
    if pointer is None:
        return None, "NO_AUTHORITY_POINTER"
    return validate_authority_pointer(pointer), None


def validate_evidence_expectations(value: Any, ac_ids: list[str]) -> list[dict[str, Any]]:
    """Validate what evidence each acceptance criterion expects, and of what kind.

    Schema 1 carried these as prose: `"method": "captured build log from documented
    commands"`. Prose cannot be derived from, so schema 2 adds `kind` -- an identifier the
    project maps to the checks that evidence can satisfy. That mapping is what makes a
    check's applicability derivable per Story (F3/C2) instead of a single project-wide
    list every Story must satisfy whether or not it can.

    `method` stays, because a human still has to know *how* the evidence is produced; it
    is documentation, and nothing derives from it.

    Every expectation must name an AC the Story actually declares. An expectation for an
    AC that does not exist is a preparation error, and silently ignoring it would let a
    check quietly fail to derive.
    """
    _need(isinstance(value, list), "evidence_expectations must be a list.")
    known = set(ac_ids)
    out = []
    for i, item in enumerate(value):
        label = f"evidence_expectations[{i}]"
        _need(isinstance(item, dict), f"{label} must be an object.")
        _need(set(item) == {"ac_id", "kind", "method", "stage", "owner", "initial_status"},
              f"{label} must state ac_id, kind, method, stage, owner and initial_status.")
        ac_id = c._nonblank(item.get("ac_id"), f"{label}.ac_id")
        _need(ac_id in known, f"{label}.ac_id {ac_id!r} is not one of this Story's ac_ids.")
        kind = item.get("kind")
        _need(isinstance(kind, str) and bool(EVIDENCE_KIND.fullmatch(kind)),
              f"{label}.kind must be a lowercase identifier; got {kind!r}.")
        c._nonblank(item.get("method"), f"{label}.method")
        _need(item.get("stage") in EVIDENCE_STAGES,
              f"{label}.stage must be one of {', '.join(EVIDENCE_STAGES)}.")
        _need(item.get("owner") in EVIDENCE_OWNERS,
              f"{label}.owner must be one of {', '.join(EVIDENCE_OWNERS)}.")
        _need(item.get("initial_status") in EVIDENCE_STATUSES,
              f"{label}.initial_status must be one of {', '.join(EVIDENCE_STATUSES)}.")
        out.append(item)
    return out


def validate_handoff(meta: Any) -> dict[str, Any]:
    """Validate a bp-meta block against the shared handoff interface. Runnable either side.

    This is what lets Backlog Preparation state, *before* claiming handoff, that a Story
    card can produce a valid normalized contract -- and name what is missing when it
    cannot.
    """
    _need(isinstance(meta, dict), "bp-meta must be a JSON object.")
    _need(meta.get("artifact_type") == "story", "This handoff interface covers Story cards.")
    _need(meta.get("artifact_schema_version") == BP_META_SCHEMA_VERSION,
          f"Use bp-meta schema {BP_META_SCHEMA_VERSION}; schema "
          f"{meta.get('artifact_schema_version')!r} must be upgraded first.")
    _need(meta.get("handoff_schema_version") == HANDOFF_SCHEMA_VERSION,
          f"Use handoff interface version {HANDOFF_SCHEMA_VERSION}.")

    required = {"artifact_type", "artifact_schema_version", "handoff_schema_version", "story_id",
                "capability_id", "canonical_backlog_path", "canonical_revision", "story_card_path",
                "traceability_root", "ac_ids", "risk_tier", "engineering_impact", "engineering_owner",
                "required_assurance", "merge_override", "preparation_state",
                "evidence_expectations", *REFERENCE_FIELDS}
    absent = sorted(required - set(meta))
    _need(not absent, f"bp-meta is missing fields the normalized contract needs: {absent}")
    unknown = sorted(set(meta) - required - set(INFORMATIONAL_FIELDS) - set(RESERVED_FIELDS))
    _need(not unknown, f"bp-meta has unsupported fields: {unknown}")

    c._ident(meta.get("story_id"), "story_id")
    c._ident(meta.get("capability_id"), "capability_id")
    c._relpath(meta.get("canonical_backlog_path"), "canonical_backlog_path")
    c._relpath(meta.get("story_card_path"), "story_card_path")
    c._nonblank(meta.get("canonical_revision"), "canonical_revision")
    _need(type(meta.get("risk_tier")) is int and meta["risk_tier"] in (1, 2, 3),
          "risk_tier must be the integer 1, 2 or 3.")

    roots = c._unique_strings(meta.get("traceability_root"), "traceability_root", minimum=1)
    for i, value in enumerate(roots):
        c._ref(value, f"traceability_root[{i}]")
    ac_ids = c._unique_strings(meta.get("ac_ids"), "ac_ids", minimum=1)
    for i, value in enumerate(ac_ids):
        c._ref(value, f"ac_ids[{i}]")
    # The whole point of the interface: these reach contract.REF_ID untranslated.
    for field in REFERENCE_FIELDS:
        c._ref(meta.get(field), field)

    impact = meta.get("engineering_impact")
    _need(isinstance(impact, dict) and set(impact) == c.IMPACT_KEYS,
          "engineering_impact must contain the six lens keys exactly.")
    for key, value in impact.items():
        _need(value in c.IMPACT_VALUES, f"Invalid engineering impact value for {key}.")

    owner = meta.get("engineering_owner")
    _need(owner is None or isinstance(owner, dict), "engineering_owner must be null or an object.")
    if owner is not None:
        _need(set(owner) == {"owner_ref", "attestation_provider", "attestation_identity"},
              "engineering_owner fields are invalid.")
        c._nonblank(owner.get("owner_ref"), "engineering_owner.owner_ref")
        _need(owner.get("attestation_provider") in ("gitlab", "github"),
              "engineering_owner.attestation_provider must be gitlab or github.")
        c._nonblank(owner.get("attestation_identity"), "engineering_owner.attestation_identity")

    obligations = meta.get("required_assurance")
    _need(isinstance(obligations, list), "required_assurance must be a list.")
    for i, item in enumerate(obligations):
        _need(isinstance(item, dict), f"required_assurance[{i}] must be an object.")
        _need(set(item) == {"gate_id", "stage", "mode", "evidence_plan_ref"},
              f"required_assurance[{i}] must state gate_id, stage, mode and evidence_plan_ref.")
        c.validate_gate_obligation(item, index=i)

    _need(meta.get("merge_override") in (None, "DEVELOPER_REVIEW"),
          "merge_override may only be null or DEVELOPER_REVIEW.")
    validate_evidence_expectations(meta.get("evidence_expectations"), ac_ids)

    pointer = meta.get("authority_pointer")
    # Reserved for F7/A2 and read by nothing here. Validated only so a card carrying it
    # is accepted rather than rejected, which is what makes adding it additive later.
    _need(pointer is None or isinstance(pointer, dict), "authority_pointer must be null or an object.")
    return meta


def handoff_check(repo: str | Path, story_path: str, *,
                  overrides: dict[str, Any] | None = None,
                  publication_sha: str | None = None) -> dict[str, Any]:
    """Can this Story card produce a valid normalized contract? Name what is missing.

    Read-only. With no `publication_sha` it reads the working tree, which is the gate
    Backlog Preparation runs before claiming BUILD READY. With one, it reads the card as
    published, which is what generation uses -- the two can disagree, and only the second
    is what a contract may be bound to.
    """
    meta = (read_story_card(repo, story_path) if publication_sha is None
            else read_story_card_at(repo, publication_sha, story_path))
    declared = meta.get("artifact_schema_version")
    warnings: list[str] = []
    missing: list[str] = []
    if declared == LEGACY_BP_META_SCHEMA_VERSION:
        meta, warnings, missing = upgrade_legacy_meta(meta, story_path=story_path, overrides=overrides)
        warnings.insert(0, f"This Story card is bp-meta schema {LEGACY_BP_META_SCHEMA_VERSION}. It is accepted "
                           f"for one release and upgraded in memory; nothing on disk was changed.")
    elif overrides:
        # The same treatment as the legacy path: one command must not mean two things.
        _check_override_keys(overrides)
        meta = dict(meta)
        missing.extend(_apply_obligation_overrides(meta, overrides))
        missing.extend(_apply_evidence_overrides(meta, overrides))
        for field in REFERENCE_FIELDS:
            if field in overrides:
                meta[field] = overrides[field]
        warnings.extend(f"{field} was supplied as an override, not by the Story card."
                        for field in sorted(overrides))

    base = {"story_card": story_path, "declared_schema": declared,
            "handoff_schema_version": HANDOFF_SCHEMA_VERSION,
            "source": "working_tree" if publication_sha is None else f"commit {publication_sha}",
            "warnings": warnings, "missing": missing}
    if missing:
        return {**base, "result": "HANDOFF_INCOMPLETE",
                "notice": "These fields have no source in this Story card. Either raise the card to "
                          "bp-meta schema 2 and state them, or supply them explicitly as overrides. "
                          "They are not inferred."}
    try:
        validate_handoff(meta)
    except r.AEError as exc:
        # A card that cannot produce a contract is a reportable outcome, not a crash:
        # naming what is wrong before handoff is this function's whole purpose.
        return {**base, "result": "HANDOFF_INVALID", "error": str(exc),
                "notice": "The Story card cannot produce a valid normalized contract."}
    return {**base, "result": "HANDOFF_READY",
            "story_id": meta["story_id"], "risk_tier": meta["risk_tier"],
            "canonical_revision": meta["canonical_revision"],
            "notice": "The Story card carries everything the normalized contract needs. This does not "
                      "check publication, assignment or project policy."}


def generate_contract(repo: str | Path, story_path: str, *, developer: str, sprint: str,
                      publication_sha: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive the normalized Story contract from the card. Deterministic.

    The same card at the same publication commit, for the same developer and sprint,
    always produces byte-identical output. `developer` and `sprint` are assignment
    decisions and are the only inputs that do not come from the card or from Git.

    Blob SHAs are read from the declared publication commit, so the contract is bound to
    what was actually published rather than to the working tree.
    """
    check = handoff_check(repo, story_path, overrides=overrides, publication_sha=publication_sha)
    _need(check["result"] == "HANDOFF_READY",
          f'This Story card is not ready for handoff ({check["result"]}): '
          f'{check.get("error") or ", ".join(check["missing"])}')
    # As published, never from the working tree -- see read_story_card_at.
    meta = read_story_card_at(repo, publication_sha, story_path)
    if meta.get("artifact_schema_version") == LEGACY_BP_META_SCHEMA_VERSION:
        meta, _, _ = upgrade_legacy_meta(meta, story_path=story_path, overrides=overrides)
    elif overrides:
        _check_override_keys(overrides)
        meta = dict(meta)
        _apply_obligation_overrides(meta, overrides)
        _apply_evidence_overrides(meta, overrides)
        for field in REFERENCE_FIELDS:
            if field in overrides:
                meta[field] = overrides[field]

    repo_path = Path(repo)
    backlog_path = meta["canonical_backlog_path"]
    card_path = meta["story_card_path"]
    data = {
        "contract_schema_version": c.CONTRACT_SCHEMA_VERSION,
        "story_id": meta["story_id"],
        "assigned_developer": developer,
        "sprint": sprint,
        "risk_tier": meta["risk_tier"],
        "publication": {
            "canonical_backlog": {
                "path": backlog_path,
                "revision": meta["canonical_revision"],
                "publication_sha": publication_sha,
                "blob_sha": c._git_blob_at(repo_path, publication_sha, backlog_path),
            },
            "story_card": {
                "path": card_path,
                "publication_sha": publication_sha,
                "blob_sha": c._git_blob_at(repo_path, publication_sha, card_path),
            },
        },
        "traceability_root": list(meta["traceability_root"]),
        "ac_ids": list(meta["ac_ids"]),
        "scope_ref": meta["scope_ref"],
        "preserve_ref": meta["preserve_ref"],
        "dependency_ref": meta["dependency_ref"],
        "engineering_owner": None if meta["engineering_owner"] is None else dict(meta["engineering_owner"]),
        "engineering_impact": dict(meta["engineering_impact"]),
        "story_required_assurance": [dict(x) for x in meta["required_assurance"]],
        "evidence_expectations": [dict(x) for x in meta["evidence_expectations"]],
        "merge_mode_override": meta["merge_override"],
        "build_start_ref": meta["build_start_ref"],
        "final_acceptance_ref": meta["final_acceptance_ref"],
        "capability_id": meta["capability_id"],
    }
    c.validate_contract(data)
    return data
