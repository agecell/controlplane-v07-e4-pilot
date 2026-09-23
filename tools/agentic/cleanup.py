#!/usr/bin/env python3
"""Clean ONE registered merged Story branch; preserve every reusable lane folder.
Preview by default. A lane currently running another Story is never changed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import runtime as r
import pool


def validate_checks(task,cfg):
    r.mr_task(task);c=task.get('cleanup_checks',{});r.fresh(c.get('checked_at'))
    for k in ('dependencies_clear','processes_stopped','records_published'):
        r.need(c.get(k) is True and r.text(c.get(k+'_ref')),f'Confirm {k} with current evidence.')
    r.need(c.get('retention_required') is False,'KEEP: active QA, dependency or retention need.')
    r.need(task['branch']!=cfg['target_branch'] and task['branch'] not in cfg.get('cleanup',{}).get('protected_branches',[]),'Protected target is never eligible.')

def assess(repo,task,cfg):
    r.config_ready(cfg,True);validate_checks(task,cfg);data=pool.load(repo)
    r.need(not any(x.get('lease') and x['lease']['task_id']==task['task_id'] for x in data['lanes'].values()),'Release this Story lanes before cleanup; do not switch active readers/writers.')
    _,mr=r.project_pull(repo,cfg,task)
    a=r.forge_adapter(cfg)
    for page in range(1,101):
        rows=r.forge_call(cfg,a.open_pull_requests,repo,cfg,page)
        r.need(not any(x.get('source_branch')==task['branch'] or x.get('target_branch')==task['branch'] for x in rows),'KEEP: an open pull request still depends on this branch.')
        if len(rows)<100:break
    else:raise r.AEError('Open pull request list incomplete; no deletion allowed.')
    proof=r.integration_proof(repo,task,mr,r.fetch_target(repo,cfg))
    remote=r.remote_head(repo,cfg['remote'],task['branch']);local=r.local_ref(repo,'refs/heads/'+task['branch'])
    r.need(remote in (None,task['candidate_sha']),'Remote branch changed since merge; KEEP.')
    r.need(local in (None,task['candidate_sha']),'Local branch has other work; KEEP.')
    tracking=r.local_ref(repo,f'refs/remotes/{cfg["remote"]}/{task["branch"]}')
    r.need(tracking in (None,task['candidate_sha']),'Tracking ref points to another version; preserve it for review.')
    if remote:
        b=r.forge_call(cfg,a.branch,repo,cfg,task['branch'])
        r.need(b.get('name')==task['branch'] and b.get('sha')==remote and b.get('protected') is False and b.get('is_default') is False,'Remote branch identity/protection is not eligible.')
    r.need(not any(x.get('branch')=='refs/heads/'+task['branch'] for x in r.worktrees(repo)),'Branch is still checked out. Release it safely; cleanup never switches a lane.')
    return {'integration':'INTEGRATED','proof':proof,'remote_branch':'SAFE_DELETE' if remote else 'ALREADY_ABSENT','local_branch':'SAFE_DELETE' if local else 'ALREADY_ABSENT','tracking_ref':'SAFE_PRUNE' if tracking else 'ALREADY_ABSENT','worktree':'REUSABLE_POOL_PRESERVED','lane_release':'COMPLETE','branch':task['branch'],'candidate_sha':task['candidate_sha']}

def execute(repo,task,cfg,apply=False):
    r.require_coordinator(repo,task['developer'])
    if apply:
        p=cfg.get('cleanup',{});r.need(p.get('mode')=='SAFE_MERGED' and p.get('approved') is True and r.text(p.get('approval_ref')),'Cleanup standing policy not approved.')
    with r.local_lock(repo):
        result=assess(repo,task,cfg)
        if not apply:
            result.update(mode='PREVIEW_ONLY',cleanup='CLEANUP_COMPLETE' if result['remote_branch']==result['local_branch']==result['tracking_ref']=='ALREADY_ABSENT' else 'CLEANUP_PENDING');return result
        actions=[];ref='refs/heads/'+task['branch']
        if result['remote_branch']=='SAFE_DELETE':
            assess(repo,task,cfg)
            r.git(repo,'push','--porcelain',f'--force-with-lease={ref}:{task["candidate_sha"]}',cfg['remote'],':'+ref)
            r.need(r.remote_head(repo,cfg['remote'],task['branch']) is None,'Remote deletion not verified. Recover before retry.')
            actions.append('remote_branch_deleted')
        current=assess(repo,task,cfg)
        if current['local_branch']=='SAFE_DELETE':
            r.git(repo,'update-ref','-d',ref,task['candidate_sha']);actions.append('local_branch_deleted')
        tracking=f'refs/remotes/{cfg["remote"]}/{task["branch"]}';old=r.local_ref(repo,tracking)
        if old:
            r.need(old==task['candidate_sha'] and r.remote_head(repo,cfg['remote'],task['branch']) is None,'Tracking ref changed; preserve it.')
            r.git(repo,'update-ref','-d',tracking,old);actions.append('tracking_ref_pruned')
        final=assess(repo,task,cfg);final.update(mode='APPLIED',actions=actions,cleanup='CLEANUP_COMPLETE' if final['remote_branch']==final['local_branch']==final['tracking_ref']=='ALREADY_ABSENT' else 'CLEANUP_PENDING')
        return final

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path.cwd());p.add_argument('--task',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    try:
        repo=r.root(a.repo);r.need(repo==Path(__file__).resolve().parents[2],'Run the coordinator-installed helper.')
        _,task=r.load_task(repo,a.task);v=execute(repo,task,r.config(repo),a.apply);print(json.dumps(v,indent=2));return 0
    except (r.AEError,OSError,ValueError,TypeError,KeyError) as e:return r.cli_error(e)
if __name__=='__main__':raise SystemExit(main())
