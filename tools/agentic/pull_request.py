#!/usr/bin/env python3
"""Open ONE registered Story's pull request through the forge seam. Preview by default.

v0.6 had no such helper. Creating the merge request was delegated to `glab mr create` /
`gh pr create` under a guard allow-list, which does not work on a self-managed GitLab
whose host is not the CLI's configured default -- the v0.6 pilot opened both of its
merge requests by hand in the web UI, outside every check this kit performs.

This helper creates nothing the forge could then act on by itself: no auto-merge, no
squash, no delete-source-on-merge. Merging stays with merge.py, and deleting the branch
stays with cleanup.py, after the integration proof and the traceability record exist.

It does not write the task manifest. `mr_iid` is the identifier every later step is
checked against, so recording it stays a deliberate, reviewable edit rather than a side
effect of a network call.

Keep `--title` plain. The guard refuses any helper command containing shell
metacharacters, so a title with `&`, `|`, `$` or a redirection is rejected before this
script runs. Put anything richer in the file `--description-file` names.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import runtime as r


def description_text(repo, path):
    """Read the description from a file inside the repository, or return empty."""
    if path is None:
        return ""
    p = Path(path)
    p = (Path(repo) / p).absolute() if not p.is_absolute() else p
    r.ensure_no_links(p, Path(repo))
    return p.read_text(encoding="utf-8-sig")


def execute(repo, task, cfg, title, description, apply=False):
    r.require_coordinator(repo, task["developer"])
    r.config_ready(cfg, True)
    with r.local_lock(repo):
        a = r.forge_adapter(cfg)
        existing = r.forge_call(cfg, a.find_open_pull_request, repo, cfg, task["branch"])
        if existing is None and not apply:
            # Everything except the POST, so a preview is a real rehearsal rather than
            # an echo of the arguments: remote identity, publication at the candidate,
            # and the absence of a request already open for this branch.
            r.verify_remote(repo, cfg)
            published = r.remote_head(repo, cfg["remote"], task["branch"])
            r.need(published is not None, f'Publish {task["branch"]} at the candidate before opening the pull request.')
            r.need(published == task["candidate_sha"], "The published branch head differs from the recorded candidate.")
            return {
                "result": "PULL_REQUEST_PREVIEW",
                "mode": "PREVIEW_ONLY",
                "provider": cfg["forge"]["provider"],
                "source_branch": task["branch"],
                "target_branch": cfg["target_branch"],
                "candidate_sha": task["candidate_sha"],
                "title": title,
                "description_bytes": len(description.encode("utf-8")),
                "existing": None,
                "notice": "Nothing was created. Re-run with --apply to open the pull request.",
            }
        pull = r.create_pull(repo, cfg, task, title, description)
        return {
            "result": "PULL_REQUEST_OPEN",
            "mode": "APPLIED" if pull["created"] else "ALREADY_OPEN",
            "provider": cfg["forge"]["provider"],
            "mr_iid": pull["number"],
            "source_branch": pull["source_branch"],
            "target_branch": pull["target_branch"],
            "candidate_sha": pull["head_sha"],
            "draft": pull["draft"],
            "deletes_source_on_merge": pull["deletes_source_on_merge"],
            "auto_merge_queued": pull["auto_merge_queued"],
            "notice": "Record mr_iid in the task manifest before merge preflight. "
                      "Opening a pull request is not review, approval or integration.",
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--task", type=Path, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description-file", type=Path)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    try:
        repo = r.root(a.repo)
        r.need(repo == Path(__file__).resolve().parents[2], "Run the coordinator-installed helper.")
        _, task = r.load_task(repo, a.task)
        result = execute(repo, task, r.config(repo), a.title, description_text(repo, a.description_file), a.apply)
        print(json.dumps(result, indent=2))
        return 0
    except (r.AEError, OSError, ValueError, TypeError, KeyError) as e:
        return r.cli_error(e)


if __name__ == "__main__":
    raise SystemExit(main())
