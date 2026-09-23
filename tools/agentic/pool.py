#!/usr/bin/env python3
"""Reusable local lane pool. Init/lease/release are explicit; status never changes Git.
One lease owns one lane, even for readers. Leases never expire or get stolen.
"""
from __future__ import annotations
import argparse
import json
import uuid
from pathlib import Path
import runtime as r
try:
    import contract as c
except ModuleNotFoundError:  # pragma: no cover
    from . import contract as c


# Ignored artifacts the kit itself creates inside a lane. Fixed, and deliberately NOT
# user-extensible: the undeclared-ignored-files refusal exists precisely so that nobody
# can quietly widen what may be discarded, and a configurable "kit artifacts" list would
# be that widening under another name. A kit that blocks a lane release on its own
# droppings only makes the refusal look arbitrary -- which is how the v0.6 pilot met it,
# after running a helper from inside a lane created tools/agentic/__pycache__/.
KIT_GENERATED_DIRECTORIES = ('__pycache__',)

def kit_generated(entry):
    """True when this ignored entry is something the kit produced, at any depth."""
    return any(part in KIT_GENERATED_DIRECTORIES for part in entry.split('/') if part)

def state_path(repo):return r.common(repo)/'agentic/pool.json'

def load(repo):
    data=r.read_json(state_path(repo))
    r.need(data.get('schema_version')==2 and Path(data.get('coordinator','')).resolve()==Path(repo).resolve(),'Wrong pool coordinator/schema.')
    return data

def save(repo,data):r.atomic_json(state_path(repo),data)

def pool_config(cfg):
    p=cfg.get('pool',{})
    r.need(p.get('model')=='REUSABLE_LANE_POOL' and type(p.get('size'))==int and 2<=p['size']<=32,'Pool size must be 2..32.')
    r.need(p.get('root')=='.claude/worktrees','This helper uses .claude/worktrees as the pool root.')
    allowed=p.get('reusable_ignored_directories',[])
    r.need(isinstance(allowed,list),'Reusable cache directory list required.')
    for x in allowed:
        r.need(isinstance(x,str) and x.endswith('/') and not x.startswith(('/','\\','.')) and '..' not in x and '\\' not in x and ':' not in x and '*' not in x,'Cache directories must be exact relative directories; no hidden roots, wildcards or parent paths.')
    return p

def lane_path(repo,lane,cfg):
    p=pool_config(cfg);r.need(lane in [f'lane-{i:02}' for i in range(1,p['size']+1)],'Lane is outside configured pool.')
    path=Path(repo)/p['root']/lane;r.ensure_no_links(path,Path(repo));return path

def clean_for_switch(path,cfg):
    r.need(not r.git(path,'status','--porcelain=v1','--untracked-files=all')[1],'Preserve modified or untracked files before reusing this lane.')
    ignored=[x for x in r.git(path,'ls-files','--others','--ignored','--exclude-standard','--directory','-z')[1].split('\0') if x]
    allowed=pool_config(cfg)['reusable_ignored_directories']
    # Name the offenders. v0.6 refused with a sentence that said neither which paths were
    # the problem nor where to declare them, so the obvious way out was to widen the
    # configuration with a broad pattern -- weakening the very protection the refusal
    # exists to provide.
    undeclared=[x for x in ignored if not any(x.startswith(a) for a in allowed) and not kit_generated(x)]
    shown=', '.join(undeclared[:10])+(f' (and {len(undeclared)-10} more)' if len(undeclared)>10 else '')
    r.need(not undeclared,
           f'This lane holds ignored files that nothing has declared reusable: {shown}. '
           f'Add the exact directories to pool.reusable_ignored_directories in '
           f'.agentic/project.json if they are a rebuildable cache, or remove them yourself. '
           f'Nothing was deleted and the lane is kept.')
    r.need(not (path/'.gitmodules').exists(),'Submodule lane reuse needs a separately reviewed procedure.')
    rows=[x for x in r.worktrees(path) if Path(x['worktree']).resolve()==path.resolve()]
    r.need(len(rows)==1 and 'locked' not in rows[0] and 'prunable' not in rows[0],'Lane is locked/prunable or not registered.')

def idle_safe(repo,lane,cfg):
    path=lane_path(repo,lane,cfg);clean_for_switch(path,cfg)
    r.need(not r.git(path,'branch','--show-current')[1],'Idle lane must be detached, not hold a task branch.')
    return path

def init(repo,developer,base,apply=False):
    repo=r.require_coordinator(repo,developer);cfg=r.config(repo);r.config_ready(cfg);p=pool_config(cfg);r.commit(repo,base)
    r.git(repo,'cat-file','-e',base+':docs/agentic/AGENT_RULES.md')
    names=[f'lane-{i:02}' for i in range(1,p['size']+1)]
    with r.local_lock(repo):
        if state_path(repo).exists():
            data=load(repo);r.need(data['developer']==developer and list(data['lanes'])==names,'Pool owner/size changed; migration review required.')
        else:data={'schema_version':2,'coordinator':str(repo),'developer':developer,'lanes':{},'contributors':{},'created_at':r.now()}
        for lane in names:
            path=lane_path(repo,lane,cfg)
            if lane in data['lanes']:
                r.need(path.is_dir(),'Registered lane missing; recover instead of recreating.')
                continue
            r.need(not path.exists(),'Unregistered path already exists; do not adopt or overwrite it.')
            r.need(r.git(repo,'check-ignore',str(path),allowed=(0,1))[0]==0,'Pool root must be ignored before initialization.')
        if not apply:return {'mode':'PREVIEW_ONLY','pool_size':p['size'],'lanes':names,'creates': [x for x in names if x not in data['lanes']]}
        # Publish the registry first. An interrupted init can be inspected, never auto-deleted.
        save(repo,data)
        for lane in names:
            if lane in data['lanes']:continue
            path=lane_path(repo,lane,cfg);path.parent.mkdir(parents=True,exist_ok=True)
            r.git(repo,'worktree','add','--detach',str(path),base)
            data['lanes'][lane]={'state':'IDLE','generation':0,'workspace':str(path),'idle_sha':base,'lease':None}
            save(repo,data)
        return {'mode':'APPLIED','pool_size':p['size'],'lanes':names,'result':'POOL_READY'}

def status(repo):
    repo=r.require_coordinator(repo);cfg=r.config(repo);data=load(repo);rows=[]
    for lane,item in data['lanes'].items():
        path=lane_path(repo,lane,cfg)
        try:
            head=r.git(path,'rev-parse','HEAD')[1];branch=r.git(path,'branch','--show-current')[1]
            dirty=bool(r.git(path,'status','--porcelain=v1','--untracked-files=all')[1])
            observed='RECOVERY_REQUIRED' if item['state']=='IDLE' and (branch or head!=item.get('idle_sha') or dirty) else item['state']
        except (r.AEError,OSError):head=None;branch=None;dirty=None;observed='RECOVERY_REQUIRED'
        rows.append({'lane_id':lane,'state':observed,'workspace':str(path),'head':head,'branch':branch,'dirty':dirty,'lease':item.get('lease')})
    return {'result':'STATUS_ONLY','developer':data['developer'],'lanes':rows,'notice':'Registry state is not proof that child processes stopped.'}

def lease(repo,developer,story,lane,role,sha,invocation,apply=False):
    repo=r.require_coordinator(repo,developer);cfg=r.config(repo);r.config_ready(cfg);r.ident(story);r.ident(invocation)
    r.need(role in r.ROLES,'Unknown role.');r.commit(repo,sha)
    with r.local_lock(repo):
        data=load(repo);r.need(data['developer']==developer,'Wrong pool owner.')
        item=data['lanes'].get(lane);r.need(item is not None and item['state']=='IDLE' and item.get('lease') is None,'Lane is not free. Never steal/reassign a lease.')
        path=idle_safe(repo,lane,cfg)
        r.need(r.git(path,'rev-parse','HEAD')[1]==item['idle_sha'],'Idle lane changed outside the pool; recover first.')
        active=[x['lease'] for x in data['lanes'].values() if x.get('lease')]
        r.need(not any(x['invocation_id']==invocation for x in active),'Invocation is already assigned.')
        writes=role in ('BUILD','FIX')
        if writes:
            r.need(sum(x['role'] in ('BUILD','FIX') for x in active)<cfg.get('max_local_builders',1),'Local builder limit reached.')
            r.need(not any(x['task_id']==story for x in active),'This Story has an active writer/reader. Finish and release it first.')
        else:
            r.need(not any(x['task_id']==story and x['role'] in ('BUILD','FIX') for x in active),'Freeze and release the writer before assigning a final reader.')
        history=data['contributors'].get(story,[])
        if role=='REVIEW':
            r.need(not any(x['invocation_id']==invocation or x['lane_id']==lane for x in history),'Reviewer invocation and lane must be independent from this Story authors.')
        branch=f'work/{developer}/{story}';taskfile=r.task_path(repo,developer,story)
        task=r.load_task(repo,taskfile)[1] if taskfile.exists() else None
        contract_path=None;contract_data=None;contract_precheck=None
        if role=='BUILD':
            r.need(task is None and r.local_ref(repo,'refs/heads/'+branch) is None,'Story already exists. Resume/FIX, never duplicate.')
            r.git(repo,'cat-file','-e',sha+':docs/agentic/AGENT_RULES.md')
            contract_path,contract_data=c.load_registered_contract(repo,developer,story)
            contract_precheck=c.assurance_precheck(repo,contract_data,developer=developer)
            r.need(contract_precheck.get('result')=='CONTRACT_PRECHECK_READY','Normalized Story contract is not ready for runtime binding.')
        elif role=='FIX':
            r.need(task is not None and r.local_ref(repo,'refs/heads/'+branch)==sha,'FIX must resume the exact existing Story branch.')
        if task:
            r.need(task.get('workspace_model')=='REUSABLE_LANE_POOL' and task.get('developer')==developer,'Legacy/foreign task cannot be assigned implicitly.')
            if task.get('schema_version')==3:
                contract_path,contract_data=c.load_registered_contract(repo,developer,story)
                r.need(c.contract_digest(contract_data)==task['contract']['contract_digest'],'Registered Story contract changed after task binding. Re-run preparation/precheck and reconcile explicitly before continuing.')
                r.need(task['contract']['contract_ref']==contract_path.resolve().relative_to(repo).as_posix(),'Task contract_ref does not match the registered normalized contract path.')
            elif task.get('schema_version')==2 and role!='PREP':
                r.validate_legacy_task_compatibility(cfg,task)
        elif role not in ('BUILD','PREP'):
            raise r.AEError('Create/read the approved Story record before assigning this role.')
        if writes:
            r.need(not any(w.get('branch')=='refs/heads/'+branch for w in r.worktrees(repo)),'Branch is checked out elsewhere.')
        if not apply:
            out={'mode':'PREVIEW_ONLY','lane_id':lane,'role':role,'task_id':story,'sha':sha,'branch':branch if writes else None}
            if role=='BUILD' and contract_precheck:
                out.update(contract_digest=contract_precheck['contract_digest'],risk_tier=contract_data['risk_tier'],effective_merge_mode=contract_precheck['effective_merge_mode'],pre_merge_assurance=[x['gate_id'] for x in contract_precheck['effective_assurance']['PRE_MERGE'] if not x.get('virtual')],downstream_assurance=[x['gate_id'] for stage in ('POST_INTEGRATION','RELEASE_POINTER') for x in contract_precheck['effective_assurance'][stage]])
            return out
        token=uuid.uuid4().hex
        lease_data={'token':token,'task_id':story,'developer':developer,'role':role,'invocation_id':invocation,'expected_sha':sha,'branch':branch if writes else None,'created_at':r.now()}
        item.update(state='PREPARING',generation=item['generation']+1,lease=lease_data);save(repo,data)
        try:
            if role=='BUILD':r.git(path,'checkout','--no-overwrite-ignore','-b',branch,sha)
            elif role=='FIX':r.git(path,'checkout','--no-overwrite-ignore',branch)
            else:r.git(path,'checkout','--no-overwrite-ignore','--detach',sha)
            r.need(r.git(path,'rev-parse','HEAD')[1]==sha,'Prepared lane SHA changed.')
            if role=='BUILD':
                r.need(contract_path is not None and contract_data is not None and contract_precheck is not None,'BUILD requires a validated normalized Story contract.')
                task={
                    'schema_version':3,
                    'workspace_model':'REUSABLE_LANE_POOL',
                    'task_id':story,
                    'developer':developer,
                    'branch':branch,
                    'base_sha':sha,
                    'candidate_sha':None,
                    'state':'BUILD_PREPARED',
                    'mr_iid':None,
                    'risk_tier':contract_data['risk_tier'],
                    'allocations':[],
                    'merge_mode_override':contract_data.get('merge_mode_override'),
                    'contract':c.task_contract_snapshot(repo,contract_path,contract_data),
                    'assurance':c.initial_task_assurance(contract_data,contract_precheck),
                    'traceability':c.initial_traceability_state(),
                }
                r.validate_task_schema3(task)
            if task:
                task.setdefault('allocations',[]).append({'lane_id':lane,'generation':item['generation'],'invocation_id':invocation,'role':role,'token':token,'sha':sha,'assigned_at':r.now()})
                r.atomic_json(taskfile,task)
            if writes:
                data['contributors'].setdefault(story,[]).append({'lane_id':lane,'invocation_id':invocation})
            item['state']='LEASED';save(repo,data)
            return {'result':'LANE_PREPARED','lane_id':lane,'workspace':str(path),'generation':item['generation'],'lease':lease_data,'task_manifest':str(taskfile) if task else None,'notice':'Workspace prepared; this helper did not invoke a subagent.'}
        except Exception:
            item['state']='RECOVERY_REQUIRED';save(repo,data);raise

def validate_lease(repo,lane,token):
    data=load(repo);item=data['lanes'].get(lane)
    r.need(item and item['state']=='LEASED' and item.get('lease',{}).get('token')==token,'Missing, stale or mismatched lane lease.')
    return data,item

def release(repo,lane,token,head,checks,apply=False):
    repo=r.require_coordinator(repo);cfg=r.config(repo);r.config_ready(cfg);r.commit(repo,head)
    with r.local_lock(repo):
        data,item=validate_lease(repo,lane,token);a=item['lease'];path=lane_path(repo,lane,cfg)
        r.fresh(checks.get('checked_at'));r.need(checks.get('lease_token')==token,'Release evidence belongs to another lease.')
        for field in ('processes_stopped','records_saved'):
            r.need(checks.get(field) is True and r.text(checks.get(field+'_ref')),f'Confirm {field} with an evidence reference.')
        clean_for_switch(path,cfg)
        r.need(r.git(path,'rev-parse','HEAD')[1]==head,'Lane changed after the release check.')
        if a['role'] in ('BUILD','FIX'):
            r.need(r.git(path,'branch','--show-current')[1]==a['branch'] and r.local_ref(repo,'refs/heads/'+a['branch'])==head,'Save source on its assigned branch first.')
        else:
            r.need(not r.git(path,'branch','--show-current')[1] and head==a['expected_sha'],'Read/QA lane contains an unexpected branch or detached commit. Preserve and recover.')
        if not apply:return {'mode':'PREVIEW_ONLY','lane_id':lane,'result':'RELEASE_READY','head':head}
        item['state']='RELEASING';save(repo,data)
        try:
            # Detach at the current exact commit. No reset, file clean or folder removal.
            r.git(path,'checkout','--no-overwrite-ignore','--detach',head)
            clean_for_switch(path,cfg)
            taskfile=r.task_path(repo,a['developer'],a['task_id'])
            candidate_after=None
            if taskfile.exists():
                task=r.read_json(taskfile)
                # A lane where nothing was committed has no candidate. v0.6 promoted the
                # lane head unconditionally, so a BUILD leased against the wrong base
                # recorded the *base* as the candidate, and the state machine had no way
                # out: re-leasing was refused as a duplicate and FIX presumes real work.
                # Recovery meant hand-editing kit state. See `abort` below.
                built=head!=a['expected_sha']
                if a['role'] in ('BUILD','FIX') and built:
                    previous=task.get('candidate_sha')
                    task.update(candidate_sha=head,state='CANDIDATE_SAVED')
                    if task.get('schema_version')==3:
                        for gate in task.get('assurance',{}).get('pre_merge',{}).values():
                            if gate.get('binding_class')!='CANDIDATE':
                                continue
                            bound=gate.get('binding',{}).get('candidate_sha')
                            if bound is None:
                                gate['binding']['candidate_sha']=head
                            elif bound!=head:
                                if gate.get('status') in ('SATISFIED','WAIVED'):
                                    # Only previously closed candidate-bound assurance is revalidated.
                                    gate['status']='NEEDS_REVALIDATION'
                                    gate['revalidation'].update(disposition='NOT_EVALUATED',from_binding=bound,to_binding=head,assessor_class=None,assessor_ref=None)
                                else:
                                    # Open/failed/in-progress work has no valid closure to reuse. Rebind
                                    # cleanly to the new candidate instead of creating a false revalidation gate.
                                    gate['binding']['candidate_sha']=head
                                    gate['status']='PENDING'
                                    gate['evidence_refs']=[]
                                    gate['attestor_ref']=None
                                    gate['observed_at']=None
                                    gate['waiver_ref']=None
                                    gate['revalidation'].update(disposition='NOT_EVALUATED',from_binding=None,to_binding=None,assessor_class=None,assessor_ref=None)
                        r.validate_task_schema3(task)
                for allocation in task.get('allocations',[]):
                    if allocation.get('token')==token:allocation.update(released_at=r.now(),result_sha=head)
                r.atomic_json(taskfile,task);candidate_after=task.get('candidate_sha')
            item.update(state='IDLE',lease=None,idle_sha=head,last_task=a['task_id']);save(repo,data)
            out={'result':'LANE_RELEASED','lane_id':lane,'folder':'PRESERVED','idle_sha':head,'source_branch':'PRESERVED','notice':'Lane release is not a merge or branch cleanup.'}
            if a['role'] in ('BUILD','FIX') and head==a['expected_sha']:
                # Say which of the two situations this is. A FIX that produced nothing
                # still has the candidate it started from; a BUILD that produced nothing
                # has none, and that is the state `abort` exists for.
                if candidate_after is None:
                    out.update(candidate='NOT_RECORDED',
                               notice='No commit was made on this lane, so no candidate was recorded and '
                                      'the Story did not advance. Resume with FIX, or discard the start '
                                      'with "pool.py abort" if it was leased against the wrong base.')
                else:
                    out.update(candidate='UNCHANGED',
                               notice='No commit was made on this lane, so the previously recorded '
                                      'candidate stands unchanged. Lane release is not a merge or branch '
                                      'cleanup.')
            return out
        except Exception:
            item['state']='RECOVERY_REQUIRED';save(repo,data);raise

def abort_record_path(repo,developer,story):
    return Path(repo)/'.agentic/local/aborts'/f'{r.ident(developer)}-{r.ident(story)}.json'

def abort(repo,developer,story,lane,token,reason_ref,apply=False):
    """Discard a Story that was started wrongly, but only while nothing has happened yet.

    STORY-002's first BUILD lease in the v0.6 pilot used the coordinator's records branch
    as its base instead of the target branch. Nothing could be done with it: releasing
    recorded the base as the candidate, re-leasing was refused as a duplicate, and FIX
    presumes forward progress from real work. Recovery meant deleting the task manifest
    and the branch by hand -- exactly the state surgery every other control exists to
    prevent. It was safe only because it was possible to verify first that nothing had
    been committed, pushed or merged.

    Those verifications are the design here, not a formality. Abort refuses unless **all**
    of them hold, and each refusal says which one failed:

    * no commit exists on the work branch beyond its base;
    * the branch was never published to the forge;
    * no pull request is recorded or open for it;
    * no assurance gate has been satisfied or waived, and no traceability record exists.

    If any is false the Story has real history, and the correct path is FIX or the
    recovery flow. **This must never become a way to discard a real candidate, built work
    that was not pushed, or a Story whose attestation was already collected.**
    """
    repo=r.require_coordinator(repo,developer);cfg=r.config(repo);r.config_ready(cfg);r.ident(story)
    r.need(r.text(reason_ref),'An abort needs a reason reference; an abandoned Story is still history.')
    with r.local_lock(repo):
        data,item=validate_lease(repo,lane,token);a=item['lease']
        r.need(a['task_id']==story and a['developer']==developer,'This lease belongs to another Story or developer.')
        r.need(a['role'] in ('BUILD','FIX'),'Only a writing lane can be aborted; release a reader normally.')
        branch=a['branch'];r.need(r.text(branch),'The lease records no work branch.')
        taskfile=r.task_path(repo,developer,story)
        r.need(taskfile.exists(),'No task manifest for this Story; there is nothing to abort.')
        task=r.read_json(taskfile)
        base=task.get('base_sha');r.commit(repo,base)

        # 1. nothing committed
        head=r.local_ref(repo,'refs/heads/'+branch)
        r.need(head==base,
               f'This Story has real work on {branch} beyond its base. Abort is only for a start that '
               f'produced nothing; continue with FIX, or use the recovery flow.')
        r.need(task.get('candidate_sha') is None,'A candidate is already recorded; this Story is not an empty start.')

        # 2. never published
        published=r.remote_head(repo,cfg['remote'],branch)
        r.need(published is None,
               f'{branch} exists on the forge. Published work is not discarded by this helper.')

        # 3. no pull request. The branch was never published, so one cannot exist -- but a
        #    recorded iid would mean the manifest disagrees with Git, which is worth
        #    stopping for rather than explaining away.
        r.need(task.get('mr_iid') is None,'A pull request is recorded for this Story; resolve it first.')
        forge_checked=False;existing=None
        try:
            adapter=r.forge_adapter(cfg)
            existing=r.forge_call(cfg,adapter.find_open_pull_request,repo,cfg,branch)
            forge_checked=True
        except (r.AEError,OSError,ValueError,TypeError,KeyError):
            # Unreachable forge does not block the abort, because condition 2 already
            # proves the branch was never published and a request cannot exist without it.
            forge_checked=False
        # Outside the try, deliberately: a refusal raised inside it would be swallowed by
        # the very clause that exists to tolerate an unreachable forge, turning "a request
        # is open" into "the forge could not be asked" and letting the abort proceed.
        r.need(existing is None,f'An open pull request exists for {branch}; resolve it first.')

        # 4. no assurance closed, nothing traced
        closed=[g for g,v in (task.get('assurance',{}).get('pre_merge') or {}).items()
                if v.get('status') in ('SATISFIED','WAIVED')]
        r.need(not closed,f'Assurance has already been closed for: {", ".join(sorted(closed))}. Attestation is never discarded.')
        r.need(not (task.get('traceability') or {}).get('record_ref'),'A traceability record exists for this Story.')

        checks={'no_commits':True,'never_published':True,'no_pull_request':True,
                'forge_confirmed_no_open_request':forge_checked,'no_closed_assurance':True}
        if not apply:
            return {'mode':'PREVIEW_ONLY','result':'ABORT_READY','lane_id':lane,'task_id':story,'branch':branch,
                    'base_sha':base,'checks':checks,
                    'notice':'Nothing was changed. Re-run with --apply to discard this start.'}

        item['state']='RELEASING';save(repo,data)
        try:
            path=lane_path(repo,lane,cfg)
            r.git(path,'checkout','--no-overwrite-ignore','--detach',base)
            clean_for_switch(path,cfg)
            record={'schema_version':1,'result':'STORY_START_ABORTED','task_id':story,'developer':developer,
                    'branch':branch,'base_sha':base,'lane_id':lane,'role':a['role'],
                    'invocation_id':a['invocation_id'],'reason_ref':reason_ref,'checks':checks,
                    'aborted_at':r.now(),
                    'notice':'A Story that was started and abandoned is history. This record is local, as '
                             'the task manifest was; nothing was published and nothing was merged.'}
            recordfile=abort_record_path(repo,developer,story)
            recordfile.parent.mkdir(parents=True,exist_ok=True)
            r.ensure_no_links(recordfile.parent,Path(repo));r.atomic_json(recordfile,record)
            # Only now remove state, and only the state this Story created.
            r.git(repo,'update-ref','-d','refs/heads/'+branch,base)
            taskfile.unlink()
            history=data['contributors'].get(story)
            if history is not None:data['contributors'].pop(story,None)
            item.update(state='IDLE',lease=None,idle_sha=base,last_task=story);save(repo,data)
            return {'result':'STORY_START_ABORTED','lane_id':lane,'task_id':story,'branch':'DELETED',
                    'task_manifest':'DELETED','folder':'PRESERVED','idle_sha':base,
                    'record_ref':recordfile.resolve().relative_to(repo).as_posix(),'checks':checks,
                    'notice':'The Story may now be leased again. Nothing was published, merged or attested.'}
        except Exception:
            item['state']='RECOVERY_REQUIRED';save(repo,data);raise

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    for name in ('init','status','lease','release','abort'):
        q=s.add_parser(name);q.add_argument('--repo',type=Path,default=Path.cwd())
        if name!='status':q.add_argument('--apply',action='store_true')
        if name in ('init','lease','abort'):q.add_argument('--developer',required=True)
        if name=='init':q.add_argument('--base',required=True)
        if name=='lease':
            q.add_argument('--story',required=True);q.add_argument('--lane',required=True);q.add_argument('--role',choices=r.ROLES,required=True);q.add_argument('--sha',required=True);q.add_argument('--invocation',required=True)
        if name=='release':
            q.add_argument('--lane',required=True);q.add_argument('--token',required=True);q.add_argument('--head',required=True);q.add_argument('--checks',type=Path,required=True)
        if name=='abort':
            q.add_argument('--story',required=True);q.add_argument('--lane',required=True);q.add_argument('--token',required=True);q.add_argument('--reason-ref',required=True)
    a=p.parse_args()
    try:
        repo=r.root(a.repo);r.need(repo==Path(__file__).resolve().parents[2],'Run the installed helper from the coordinator root.')
        if a.command=='init':v=init(repo,a.developer,a.base,a.apply)
        elif a.command=='status':v=status(repo)
        elif a.command=='lease':v=lease(repo,a.developer,a.story,a.lane,a.role,a.sha,a.invocation,a.apply)
        elif a.command=='abort':v=abort(repo,a.developer,a.story,a.lane,a.token,a.reason_ref,a.apply)
        else:v=release(repo,a.lane,a.token,a.head,r.read_json(a.checks),a.apply)
        print(json.dumps(v,indent=2));return 0
    except (r.AEError,OSError,ValueError,TypeError,KeyError) as e:return r.cli_error(e)
if __name__=='__main__':raise SystemExit(main())
