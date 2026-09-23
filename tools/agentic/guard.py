#!/usr/bin/env python3
"""Bounded PreToolUse guard. Not a shell sandbox or a forge approval verifier.

It catches known direct operations only. Unknown scripts/aliases/nested interpreters
still depend on native permissions, OS isolation, and server-side forge policy.

The guard runs as a hook on every tool call, so it stays import-light and does not
pull in runtime.py or contract.py. The two version constants below are therefore
duplicated from contract.py rather than imported; they must be kept in step with it.
"""
from __future__ import annotations
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 5  # contract.PROJECT_SCHEMA_VERSION
KIT_VERSION = "0.7"  # contract.KIT_VERSION
FULL_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
PUSH_REF = re.compile(r"(?:HEAD|[0-9a-f]{40}|[0-9a-f]{64}):refs/heads/(?:work|ae/records)/[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def protected_path(path: str, cwd: str) -> bool:
    p = Path(path)
    p = (Path(cwd) / p).resolve() if not p.is_absolute() else p.resolve()
    s = p.as_posix().lower()
    parts = tuple(x.lower() for x in p.parts)
    basename = p.name.lower()
    return (
        basename == "claude.md"
        or basename == ".env" or basename.startswith(".env.")
        or basename.endswith((".pem", ".key"))
        or ".git" in parts
        or "/.claude/agents/" in s
        or "/.claude/skills/" in s
        or "/.claude/settings" in s
        or "/tools/agentic/" in s
        or s.endswith("/.agentic/project.json")
        or s.endswith("/docs/agentic/agent_rules.md")
        or s.endswith("/docs/agentic/workflow.md")
        or s.endswith("/docs/agentic/project_config.md")
        or s.endswith("/docs/agentic/team_coordination.md")
        or s.endswith("/docs/agentic/cleanup.md")
        or s.endswith("/docs/agentic/operating_rules.md")
        or s.endswith("/docs/agentic/pool.md")
        or s.endswith("/docs/agentic/merge_policy.md")
        or s.endswith("/docs/agentic/assurance.md")
        or s.endswith("/docs/agentic/traceability.md")
        or s.endswith("/docs/agentic/migration.md")
        or s.endswith("/docs/agentic/controls.md")
    )


def value_after(tokens: list[str], name: str) -> str | None:
    for i, token in enumerate(tokens):
        if token.startswith(name + "="):
            return token.split("=", 1)[1]
        if token == name and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def remote_ready(config: dict[str, Any]) -> bool:
    ref = config.get("remote_actions_review_ref")
    assurance = config.get("assurance_policy")
    assurance_ref = assurance.get("approval_ref") if isinstance(assurance, dict) else None
    return (config.get("schema_version") == SCHEMA_VERSION
            and config.get("kit_version") == KIT_VERSION
            and config.get("configuration_approved") is True
            and config.get("remote_actions_ready") is True
            and isinstance(ref, str) and bool(ref.strip())
            and "__CONFIGURE__" not in ref
            and isinstance(assurance, dict)
            and assurance.get("approved") is True
            and isinstance(assurance_ref, str) and bool(assurance_ref.strip())
            and "__CONFIGURE__" not in assurance_ref)


def inspect(payload: dict[str, Any], config: dict[str, Any]) -> tuple[str | None, str]:
    name = payload.get("tool_name")
    inp = payload.get("tool_input")
    if not isinstance(name, str) or not isinstance(inp, dict):
        return "deny", "Invalid tool payload; check hook installation."
    if name in ("Write", "Edit"):
        path = inp.get("file_path")
        cwd = payload.get("cwd")
        if not isinstance(path, str) or not isinstance(cwd, str):
            return "deny", "Path or working directory is missing; do not change files."
        if protected_path(path, cwd):
            return "deny", "Policy, agent configuration, Git internals or secret changes require a maintainer; not ordinary source work."
        # A helper lease is a local coordination check, not proof of tool caller identity.
        root=Path(ROOT).resolve()
        pool_path=root/'.claude/worktrees'
        target=(Path(cwd)/path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if target.is_relative_to(pool_path):
            try:
                import pool
                data=pool.load(root);relative=target.relative_to(pool_path);lane=relative.parts[0]
                item=data['lanes'][lane]
                if item.get('state')!='LEASED' or item.get('lease',{}).get('role') not in ('BUILD','FIX'):
                    return "deny", "Source edits require an active BUILD/FIX lease."
            except Exception:
                return "deny", "Lane lease could not be verified."
        return None, ""
    if name != "Bash":
        return None, ""
    command = inp.get("command")
    if not isinstance(command, str) or not command.strip():
        return "deny", "Empty or invalid command."
    try:
        t = shlex.split(command)
    except ValueError:
        return "ask", "The pattern guard cannot inspect this command. Human review is required."
    low = command.lower()
    if "--dangerously-skip-permissions" in low or "--allow-dangerously-skip-permissions" in low:
        return "deny", "Do not bypass permissions to run the workflow."
    if re.search(r"\bgit\b[^\n]*(?:\breset\b[^\n]*--hard|\bclean\s+[^\n]*-[a-z]*f)", low):
        return "deny", "Hard reset or forced clean is outside routine authority."
    # Both forges, because the guard must not depend on which one a project configured.
    # gh spells granting approval as submitting a review.
    if re.search(r"\bglab\s+mr\s+(approve|revoke)\b", low) or re.search(r"\bgh\s+pr\s+review\b", low):
        return "deny", "An agent must not grant or revoke human approval."
    if re.search(r"\b(kubectl|helm|terraform|tofu)\b[^\n]*\b(apply|destroy|delete|upgrade|install)\b", low):
        return "deny", "Deployment or destructive infrastructure actions are outside this workflow."

    helpers=("pool.py","merge.py","cleanup.py","contract.py","assurance.py","pull_request.py")
    if any(Path(x).name in helpers for x in t):
        if len(t)<2 or t[0] not in ("python","python3") or t[1] not in tuple("tools/agentic/"+x for x in helpers):
            return "ask", "Use the reviewed helper from the coordinator root."
        if any(x in command for x in (";", "&", "|", "`", "$", "\n", ">", "<")):
            return "deny", "Run one direct helper call without shell expansion or chaining."
        if t[1].endswith("contract.py") and len(t) >= 3 and t[2] == "project-migrate-v04" and "--apply" in t:
            return "ask", "Project schema migration is a maintainer action; review the preview and approve this exact apply."
        if t[1].endswith("merge.py") and "--apply" in t and "--recover" not in t:
            if config.get("merge_mode") != "AGENT_MERGE":
                return "deny", "DEVELOPER_REVIEW: hand the MR to the developer; do not merge."
            p=config.get("merge_policy",{})
            if not remote_ready(config) or p.get("approved") is not True or not p.get("approval_ref") or "__CONFIGURE__" in str(p.get("approval_ref")):
                return "deny", "Standing merge policy is not approved."
        if t[1].endswith("cleanup.py") and "--apply" in t:
            p=config.get("cleanup",{})
            if not remote_ready(config) or p.get("mode")!="SAFE_MERGED" or p.get("approved") is not True or not p.get("approval_ref") or "__CONFIGURE__" in str(p.get("approval_ref")):
                return "deny", "Standing cleanup policy is not approved."
        if t[1].endswith("pool.py") and "--apply" in t and config.get("configuration_approved") is not True:
            return "deny", "Project configuration is not approved."
        if t[1].endswith("pull_request.py") and "--apply" in t and not remote_ready(config):
            return "deny", "Pull request/CI effects are not approved in project settings."
        return None, ""  # Trusted helper rechecks facts; does not grant server permissions.
    if re.search(r"\bgit\b[^\n]*\b(?:checkout|switch)\b|\bgit\b[^\n]*\bworktree\s+(?:add|move|remove|repair|prune)\b",low):
        return "deny", "Lane checkout/reuse is handled by pool.py; never switch an active lane directly."
    if re.search(r"\bgit\b[^\n]*\bupdate-ref\b[^\n]*\s-d\b", low):
        return "deny", "Direct ref deletion is outside routine commands. Use the reviewed, bounded cleanup helper."
    if re.search(r"\bgit\b[^\n]*\bbranch\b[^\n]*\s-[dD]\b", command):
        return "deny", "Direct branch deletion is not routine. Use the reviewed cleanup helper."
    if re.search(r"\bgit\b[^\n]*\bworktree\s+remove\b", low):
        return "deny", "Direct worktree removal is not routine. Use the helper; never force-delete worktree files."

    # Direct git push only: one explicit non-protected destination ref.
    if re.search(r"\bgit\b[^\n]*\bpush\b", low):
        if not remote_ready(config):
            return "deny", "Push/CI effects are not approved in project settings."
        try:
            j = t.index("push")
        except ValueError:
            return "ask", "Unrecognized push form; do not guess the remote/ref."
        args = t[j+1:]
        if any(x in t for x in (";", "&&", "||", "|", "&")) or "\n" in command:
            return "deny", "Push must be one direct command without chaining. Use git -C for the working directory."
        if any(x.startswith(("--force", "--mirror", "--all", "--tags", "--delete")) or x in ("-f", "-d") for x in args):
            return "deny", "Force, mirror, deletion or multi-branch push is outside routine authority."
        args = [x for x in args if x not in ("-u", "--set-upstream")]
        if len(args) != 2 or args[0] != config.get("remote", "origin") or not PUSH_REF.fullmatch(args[1]):
            return "deny", "Push needs an explicit ref: git -C <workspace> push origin <SHA>:refs/heads/work/<dev>/<story> (or ae/records/...)."
        if ".." in args[1] or "//" in args[1]:
            return "deny", "Invalid push ref."
        return None, ""

    # Creation moved behind the forge seam in v0.7. v0.6 allowed these two CLI forms
    # under a pattern check, which could only verify the flags it recognized and could
    # not reach a self-managed host at all -- the CLI's own default host is not the
    # project's. pull_request.py performs the same checks against the configured forge
    # and re-reads what was actually recorded.
    if re.search(r"\bgh\s+pr\s+create\b", low) or re.search(r"\bglab\s+mr\s+(create|new)\b", low):
        return "deny", "Use pull_request.py; it opens the request on the configured forge and verifies what was created."

    if re.search(r"\bglab\s+mr\s+(merge|accept)\b",low) or re.search(r"\bgh\s+pr\s+merge\b",low):
        return "deny", "Use merge.py for exact-SHA checks, selected mode and the shared integration lease."
    if re.search(r"\b(?:glab|gh)\s+api\b",low) and any(x in low for x in ("approve", "unapprove", "/merge", "--method put", "--method post", "--method delete", "-x put", "-x post", "-x delete")):
        # Plain GET merge_requests inspection is allowed by the normal permission layer.
        if any(x in low for x in ("--method put", "--method post", "--method delete", "-x put", "-x post", "-x delete", "/approve")):
            return "deny", "Do not call remote mutation APIs directly; use the bounded helper."

    return None, ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload")
        config = json.loads((ROOT / ".agentic/project.json").read_text(encoding="utf-8-sig"))
        decision, reason = inspect(payload, config)
        if decision:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}, ensure_ascii=False))
        return 0  # No decision means normal permission processing, not automatic allow.
    except Exception:
        print("AE_GUARD_ERROR: configuration/payload could not be read. Check the hook before continuing.", file=sys.stderr)
        return 2  # Blocking once the script successfully started; not a startup-failure guarantee.

if __name__ == "__main__":
    raise SystemExit(main())
