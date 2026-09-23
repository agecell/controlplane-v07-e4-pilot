#!/usr/bin/env python3
"""Small local helpers, not an orchestrator. Python 3.10+; Git required.
No push, MR creation, approval, remote merge, deployment, or deletion is performed.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

class AEError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode:
        # Do not echo stderr: a remote URL or credentials might occur in it.
        raise AEError(f"Git operation failed ({args[0]}; exit {result.returncode}). Inspect locally without publishing secrets.")
    return result.stdout.rstrip("\n")


def root_of(path: Path) -> Path:
    return Path(git(path.resolve(), "rev-parse", "--show-toplevel")).resolve()


def full_commit(repo: Path, sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise AEError("Use a full lowercase commit SHA, not a moving branch or shortened hash.")
    actual = git(repo, "rev-parse", "--verify", sha + "^{commit}")
    if actual != sha:
        raise AEError("Commit SHA mismatch.")
    return actual


def config_at(root: Path) -> dict[str, Any]:
    import runtime
    return runtime.config(root)


def config_gaps(cfg: dict[str, Any]) -> list[str]:
    import contract
    import runtime
    gaps=[]
    try: runtime.config_ready(cfg)
    except runtime.AEError as e: gaps.append(str(e))
    if cfg.get("schema_version") != contract.PROJECT_SCHEMA_VERSION or cfg.get("kit_version") != contract.KIT_VERSION: gaps.append("schema_version")
    if not cfg.get("commands"): gaps.append("commands")
    return gaps


SOURCE_BRANCH_SETTING = {
    "gitlab": "Settings -> Merge requests -> \"Enable 'Delete source branch' option by default\" "
              "(API: remove_source_branch_after_merge)",
    "github": "Settings -> General -> \"Automatically delete head branches\" "
              "(API: delete_branch_on_merge)",
}


def forge_source_branch_policy(root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Read the project's own "delete source branch on merge" setting. Read-only.

    Controlled cleanup removes a work branch only after the integration proof and the
    traceability record exist. A forge configured to remove it at merge time destroys
    that evidence first, so merge preflight refuses -- and in the v0.6 pilot that refusal
    arrived at the *first merge*, long after setup, with a message describing the symptom
    rather than the two-step remedy. GitLab turns this on for every new project.

    Reported as true/false/unknown. Unknown is not a failure: doctor is a local check and
    must keep working with no forge, no CLI and no network.
    """
    provider = (cfg.get("forge") or {}).get("provider")
    unknown = {"deletes_source_branch": None, "checked": False}
    if provider not in ("gitlab", "github"):
        return {**unknown, "reason": "No forge provider is configured."}
    try:
        import runtime
        runtime.forge_ready(cfg)
        adapter = runtime.forge_adapter(cfg)
        project = runtime.forge_call(cfg, adapter.project, root, cfg)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
        return {**unknown, "reason": f"The {provider} project could not be read: {exc}"}
    return {"deletes_source_branch": project.get("deletes_source_branch") is True, "checked": True,
            "reason": None, "setting": SOURCE_BRANCH_SETTING[provider]}


def doctor(repo: Path) -> dict[str, Any]:
    root = root_of(repo)
    cfg = config_at(root)
    gaps = config_gaps(cfg)
    forge_cli = {"gitlab": "glab", "github": "gh"}.get((cfg.get("forge") or {}).get("provider"))
    tools = {name: bool(shutil.which(name)) for name in ("git", "claude", "python") + ((forge_cli,) if forge_cli else ())}
    source_branch = forge_source_branch_policy(root, cfg)
    if source_branch["deletes_source_branch"] is True:
        gaps.append("forge_deletes_source_branch")
    return {"check": "LOCAL_CONFIGURATION_CHECK", "repo": str(root), "python_version": sys.version.split()[0], "tools_on_path": tools,
            "configuration_gaps": gaps, "remote_actions_ready": cfg.get("remote_actions_ready") is True,
            "cleanup_mode": cfg.get("cleanup",{}).get("mode","REPORT_ONLY"),
            "cleanup_approved": cfg.get("cleanup",{}).get("approved") is True,
            "forge_provider": (cfg.get("forge") or {}).get("provider"),
            "forge_configured": bool((cfg.get("forge") or {}).get("host")) and (cfg.get("forge") or {}).get("host") != "__CONFIGURE__",
            "assurance_policy_approved": cfg.get("assurance_policy",{}).get("approved") is True,
            "forge_deletes_source_branch": source_branch["deletes_source_branch"],
            "forge_source_branch_check": source_branch,
            "note": "Not a Claude runtime test, forge permission check, CI check, or human approval."}


def snapshot(repo: Path, base: str, out: Path) -> dict[str, Any]:
    root = root_of(repo)
    base = full_commit(root, base)
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    branch = git(root, "branch", "--show-current")
    merge_base = git(root, "merge-base", base, head)
    changed = [p for p in git(root, "diff", "--no-renames", "--name-only", "-z", base, head, "--").split("\0") if p]
    diff = git(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", base, head, "--")
    if git(root, "rev-parse", "HEAD") != head or git(root, "status", "--porcelain=v1", "--untracked-files=all") != status:
        raise AEError("Workspace changed during snapshot; freeze before retrying.")
    out = out.resolve()
    try:
        rel = out.relative_to(root).as_posix()
        if not rel.startswith(".agentic/local/"):
            raise AEError("Evidence output must be outside source worktree, or inside ignored .agentic/local/.")
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", str(out)], capture_output=True, timeout=10)
        if ignored.returncode != 0: raise AEError("Local evidence directory is not gitignored.")
    except ValueError:
        pass
    data = {"schema_version":1, "observed_at":dt.datetime.now(dt.timezone.utc).isoformat(), "workspace":str(root),
            "branch":branch, "base_sha":base, "head_sha":head, "tree_sha":git(root,"rev-parse",head+"^{tree}"),
            "merge_base_sha":merge_base, "base_is_ancestor":merge_base==base, "clean":not bool(status),
            "status_porcelain":status, "changed_files":changed, "remote_verified":False,
            "evidence_eligible":not bool(status) and merge_base==base,
            "notice":"Local snapshot only; inspect diff for secrets before sharing. Review the forge and remote separately."}
    out.mkdir(parents=True, exist_ok=False)  # Never overwrite a prior evidence set.
    (out/"snapshot.json").write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    (out/"diff.patch").write_text(diff+"\n",encoding="utf-8")
    return data


def allowed(path: str, scopes: list[str]) -> bool:
    return any(path.startswith(s) if s.endswith("/") else path==s for s in scopes)


def scope_check(repo: Path, base: str, scope_file: Path) -> dict[str, Any]:
    root = root_of(repo); base=full_commit(root,base)
    spec = json.loads(scope_file.read_text(encoding="utf-8-sig"))
    scopes = spec.get("allowed_paths", [])
    if not isinstance(scopes,list) or not scopes or not all(isinstance(s,str) and s and not s.startswith(("/","\\")) and ".." not in s and "\\" not in s and ":" not in s and "*" not in s for s in scopes):
        raise AEError("allowed_paths must contain exact relative POSIX files or directories ending in /. No wildcards.")
    head = git(root,"rev-parse","HEAD")
    files = [p for p in git(root,"diff","--no-renames","--name-only","-z",base,head,"--").split("\0") if p]
    violations = [p for p in files if not allowed(p,scopes)]
    clean = not bool(git(root,"status","--porcelain=v1","--untracked-files=all"))
    return {"head_sha":head,"base_sha":base,"changed_files":files,"out_of_scope":violations,"clean":clean,
            "passed":not violations and clean,"notice":"Checks committed file paths, not semantics, secret content, or human approval."}


def create_task(repo: Path, developer: str, story: str, base: str) -> dict[str, Any]:
    raise AEError("Per-Story worktree creation is retired in v0.4. Use pool.py init and pool.py lease; do not create another folder.")


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    for cmd in ("doctor","snapshot","scope-check","create-task"):
        q=sub.add_parser(cmd); q.add_argument("--repo",type=Path,default=Path.cwd())
        if cmd != "doctor":q.add_argument("--base",required=True)
        if cmd == "snapshot":q.add_argument("--out",type=Path,required=True)
        if cmd == "scope-check":q.add_argument("--scope-file",type=Path,required=True)
        if cmd == "create-task":
            q.add_argument("--developer",required=True);q.add_argument("--story",required=True)
    args=p.parse_args()
    try:
        if args.command=="doctor":
            result=doctor(args.repo); code=2 if result["configuration_gaps"] else 0
        elif args.command=="snapshot":result=snapshot(args.repo,args.base,args.out);code=0
        elif args.command=="scope-check":
            result=scope_check(args.repo,args.base,args.scope_file);code=0 if result["passed"] else 2
        else:result=create_task(args.repo,args.developer,args.story,args.base);code=0
        print(json.dumps(result,indent=2,ensure_ascii=False));return code
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as exc:
        print(f"AE_ERROR: {exc}",file=sys.stderr);return 2
if __name__=="__main__":
    raise SystemExit(main())
