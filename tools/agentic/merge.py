#!/usr/bin/env python3
"""Merge one exact reviewed MR under Control Plane v0.5 project policy. Preview by default.
AGENT_MERGE executes only after exact technical, CI/check, and required PRE_MERGE assurance closure.
DEVELOPER_REVIEW never calls the merge API and preserves pending assurance for humans.
A shared Git ref serializes cooperating clients; it is not a lock on human UI actions.
"""
from __future__ import annotations
import argparse
import json
import uuid
from pathlib import Path
import runtime as r
import pool
import assurance

LOCK_BRANCH='ae/locks/integration'

def _source_branch_setting(cfg):
    """Where the project's delete-source-branch default lives, in the provider's words."""
    import ae
    return ae.SOURCE_BRANCH_SETTING.get((cfg.get('forge') or {}).get('provider'),
                                        "the project's delete-source-branch-on-merge setting")

def mode(cfg,task):
    m=cfg.get('merge_mode')
    r.need(m in ('AGENT_MERGE','DEVELOPER_REVIEW'),'Unknown merge mode.')
    override=task.get('merge_mode_override')
    r.need(override in (None,'DEVELOPER_REVIEW'),'A task may only narrow merge authority.')
    return override or m

def effective_checks(repo,cfg,task):
    """Which named checks this Story must satisfy, and why each one does or does not apply.

    Project policy is read **current**, so a project that tightens its mandatory checks
    takes effect immediately. The Story's acceptance evidence is read from the registered
    contract, whose digest the task snapshot pins -- so what a Story is required to prove
    cannot drift after it was bound, while what the project demands of everyone can.

    A legacy schema-2 task has no contract and therefore no evidence, so only mandatory
    checks apply to it. That is safe precisely because the v0.6 -> v0.7 upgrade moves
    every previously required check into mandatory_checks.
    """
    import contract as c
    evidence=[]
    if task.get('schema_version')==3:
        snapshot=task.get('contract')
        r.need(isinstance(snapshot,dict),'Schema-3 task carries no contract snapshot; reconcile before merging.')
        evidence=snapshot.get('evidence_expectations') or []
    return c.effective_required_checks(cfg,{'evidence_expectations':evidence})

def quality(repo,cfg,task):
    r.mr_task(task);data=pool.load(repo)
    r.need(not any(x.get('lease') and x['lease']['task_id']==task['task_id'] for x in data['lanes'].values()),'Finish and release this Story writers/readers before merge.')
    r.need(r.local_ref(repo,'refs/heads/'+task['branch'])==task['candidate_sha'],'Local Story branch differs from candidate.')
    q=task.get('merge_checks',{});r.fresh(q.get('checked_at'))
    r.need(q.get('candidate_sha')==task['candidate_sha'],'Merge checks belong to another candidate.')
    for flag in ('scope_valid','dependencies_ready','no_unresolved_blockers','remote_effects_safe'):
        r.need(q.get(flag) is True and r.text(q.get(flag+'_ref')),f'Confirm {flag} with a reference.')
    effective=effective_checks(repo,cfg,task)
    r.need(not effective['configuration_gaps'],
           f"These default checks have no evidence-kind mapping, so whether they apply to this Story "
           f"cannot be derived: {', '.join(effective['configuration_gaps'])}. Map them in "
           f"merge_policy.check_evidence_kinds, or move them to merge_policy.mandatory_checks. "
           f"An unmapped check is a question nobody answered; it is not skipped.")
    r.need(effective['required'],'Configure required named checks.')
    checks=q.get('checks',{})
    for item in effective['resolution']:
        if item['status']!='APPLIED':
            continue
        name=item['check'];check=checks.get(name,{})
        r.need(check.get('status')=='PASS' and check.get('candidate_sha')==task['candidate_sha'] and r.text(check.get('evidence_ref')),
               f"Required check missing or not PASS: {name}. It applies because {item['detail']}")
    review=task.get('review',{})
    r.need(review.get('verdict')=='ACCEPT' and review.get('candidate_sha')==task['candidate_sha'] and r.text(review.get('report_ref')),'Exact technical ACCEPT required.')
    r.ident(review.get('invocation_id'));lane=review.get('lane_id');r.need(r.text(lane),'Review lane identity required.')
    contributors=data.get('contributors',{}).get(task['task_id'],[])
    r.need(contributors,'Builder provenance missing.')
    r.need(not any(x['invocation_id']==review['invocation_id'] or x['lane_id']==lane for x in contributors),'Reviewer is not independent from this Story authors.')
    if lane == 'control-plane':
        r.need(task.get('risk_tier') == 1, 'Control-plane-only review is limited to Tier 1.')
    else:
        r.need(any(a.get('role')=='REVIEW' and a.get('lane_id')==lane and a.get('invocation_id')==review['invocation_id'] and a.get('sha')==task['candidate_sha'] and a.get('released_at') for a in task.get('allocations',[])), 'A completed exact-SHA reviewer assignment is required.')
    return q

def human_gate(repo,cfg,task):
    p=cfg['merge_policy']
    r.need(type(p.get('human_review_required')) is bool,'Explicit human_review_required setting required.')
    if not p['human_review_required']:return
    allowed=p.get('human_approvers',[])
    r.need(isinstance(allowed,list) and allowed and all(r.text(x) for x in allowed),'Configure allowed human reviewers.')
    note_id=task.get('human_approval_note_id');r.need(type(note_id)==int and note_id>0,'A current human approval note ID is required.')
    # project/MR identity was already verified by preflight; reuse the generic server-backed
    # note primitive while preserving merge approval semantics as a separate evidence type.
    r.verify_forge_comment(repo,cfg,task,note_id,'APPROVE '+task['candidate_sha'],allowed,verify_mr=False)

def legacy_factual_assurance(cfg,task):
    """Report current v0.5 obligations for an already-integrated schema-2 Story without inventing closure."""
    extra=r.legacy_effective_additional_assurance(cfg,task)
    return {
        'result':'LEGACY_V04_COMPATIBLE' if not extra else 'LEGACY_V04_ASSURANCE_INCOMPLETE',
        'contract_status':'LEGACY_V04_CONTRACT',
        'task_schema_version':2,
        'effective_additional_assurance':extra,
        'blocking':[] if not extra else [{'gate_id':x.get('gate_id'),'stage':x.get('stage'),'mode':x.get('mode'),'status':'NOT_PROVEN'} for x in extra],
        'notice':'No v0.5 contract/assurance was synthesized for this legacy Story.' if not extra else 'Story is already integrated, but current v0.5 project/Tier assurance is not represented/proven by the legacy schema-2 task.',
    }

def preflight(repo,cfg,task,for_execution=False):
    r.config_ready(cfg,True);q=quality(repo,cfg,task)
    project,mr=r.project_pull(repo,cfg,task)
    r.need(mr['state']=='open' and mr['draft'] is False,'Pull request must be open and not draft.')
    r.need(r.remote_head(repo,cfg['remote'],task['branch'])==task['candidate_sha'],'Published source changed.')
    target=r.fetch_target(repo,cfg)
    r.need(q.get('target_sha')==target,'Target changed since integration checks; reassess and refresh the evidence.')
    r.need(mr['has_conflicts'] is False,'Merge conflict or unknown conflict state.')
    # Naming the setting, not just the symptom. In the v0.6 pilot this refusal arrived at
    # the first merge and the obvious fix -- unticking the box on the merge request --
    # does not work, because GitLab seeds the per-request value from the project setting.
    # ae.py doctor now reports the same thing at setup time.
    r.need(mr['deletes_source_on_merge'] is not True,
           f"The forge would delete the source branch at merge time, before the integration proof and "
           f"the traceability record are written against it. Two steps, in order: turn off "
           f"{_source_branch_setting(cfg)}, then clear the option on this existing merge request -- "
           f"changing the project setting alone does not update requests already open. Do not disable "
           f"this check instead; controlled cleanup deletes the branch once integration is proven.")
    if cfg.get('ci_required') is True:
        ci=mr['ci'] or {}
        r.need(ci.get('status')=='success' and ci.get('sha')==task['candidate_sha'],'Successful source-SHA CI required. Other CI provenance needs explicit project adaptation.')
    r.need(mr['auto_merge_queued'] is False,'A delayed auto-merge is already queued; resolve it before using this helper.')
    policy=cfg.get('merge_policy',{})
    r.need(cfg.get('merge_method') in ('merge_commit','fast_forward','squash'),'Configure a supported merge method.')
    # Asserted only for the two methods GitLab expresses as a project setting, which is
    # exactly what v0.5 checked. Squash is a separate flag there, so demanding it from
    # the project would change behaviour for existing GitLab projects.
    if cfg['merge_method'] in ('merge_commit','fast_forward'):
        r.need(cfg['merge_method'] in project['merge_methods'],
               f"Project does not permit the selected {cfg['merge_method']} merge method.")
    selected=mode(cfg,task)
    assurance_state=None
    if task.get('schema_version')==3:
        # Contract/policy drift is invalid in either merge mode. Developer review may
        # carry open assurance, while agent merge requires exact closure and current
        # server-backed human/waiver proof.
        assurance_state=assurance.validate_for_merge(
            repo,cfg,task,require_closed=(selected=='AGENT_MERGE'),verify_server=(selected=='AGENT_MERGE'))
    elif task.get('schema_version')==2:
        # Legacy compatibility is explicit and fail-closed. A schema-2 Story may only
        # continue/merge when current project+Tier policy adds no v0.5 assurance.
        assurance_state=r.validate_legacy_task_compatibility(cfg,task)
    if selected=='DEVELOPER_REVIEW':
        # Record how much the forge itself is enforcing. On a forge that declares no
        # policy the human reviewer is the only gate, and that belongs in the record
        # rather than being left for someone to infer later.
        _a=r.forge_adapter(cfg)
        forge_policy=r.forge_call(cfg,_a.policy_state,repo,cfg)
        return {'result':'MR_READY_FOR_HUMAN_REVIEW','mode':selected,'target_sha':target,'candidate_sha':task['candidate_sha'],'mr_iid':task['mr_iid'],
                'assurance':assurance_state,'pending_assurance':[] if assurance_state is None else assurance_state.get('blocking',[]),
                'forge_policy':forge_policy,'checks':effective_checks(repo,cfg,task),
                'notice':'Agent did not merge. Pending assurance remains mandatory where project/Story policy requires it.'}
    # The forge's own declared policy, read and checked by the adapter. Where a forge
    # declares none -- a private GitHub repository on a free plan, for instance -- the
    # adapter refuses here, and DEVELOPER_REVIEW above remains the supported path.
    a=r.forge_adapter(cfg)
    # Refuse an unprovable method before merging rather than after. GitHub rewrites
    # history for squash and rebase and does not report which it used, so the merge
    # would succeed and integration_proof would then be unable to tie the result back
    # to the candidate -- an irreversible step with no proof at the end of it.
    r.need(cfg['merge_method'] in a.provable_merge_methods,
           f"Agent merge on {cfg['forge']['provider']} cannot prove a {cfg['merge_method']} result "
           f"afterwards; it supports {', '.join(a.provable_merge_methods)}. Change merge_method, or "
           f"keep merge_mode at DEVELOPER_REVIEW.")
    r.forge_call(cfg,a.assert_policy_mergeable,repo,cfg,task,project,mr)
    human_gate(repo,cfg,task)
    if for_execution:
        r.need(policy.get('approved') is True and r.text(policy.get('approval_ref')),'Standing agent-merge policy needs maintainer approval.')
        r.need(policy.get('lock_branch')==LOCK_BRANCH and policy.get('remote_lock_enabled') is True,'Shared integration lock must be configured.')
    return {'result':'MERGE_READY','mode':selected,'target_sha':target,'candidate_sha':task['candidate_sha'],'mr_iid':task['mr_iid'],'assurance':assurance_state,'checks':effective_checks(repo,cfg,task)}

def intent_path(repo,task):return Path(repo)/'.agentic/local/merge'/f'{task["developer"]}-{task["task_id"]}.json'

def traceability_after_integration(repo,cfg,task,mr,proof,assurance_state,apply):
    try:
        if task.get('schema_version')==3:
            return r.write_traceability_index(repo,cfg,task,mr,proof,assurance_state,apply=apply)
        if task.get('schema_version')==2:
            return r.write_legacy_traceability_index(repo,cfg,task,mr,proof,assurance_state or legacy_factual_assurance(cfg,task),apply=apply)
        return None
    except (r.AEError,OSError,ValueError,TypeError,KeyError) as e:
        return {'result':'TRACEABILITY_PENDING','record_ref':r.traceability_record_ref(task),'publication_required':True,'error':str(e),'notice':'Integration is factual. Repair/publish traceability separately; do not retry the merge request.'}

def capability_after_integration(repo,cfg,task,proof,apply):
    if task.get('schema_version')!=3 or not apply:return None
    try:
        return assurance.register_capability_integration(repo,cfg,task,proof,apply=True)
    except (r.AEError,OSError,ValueError,TypeError,KeyError) as e:
        cap=task.get('contract',{}).get('capability_id')
        return {'result':'CAPABILITY_ASSURANCE_PENDING','capability_id':cap,'error':str(e),'notice':'Integration/Story traceability remain factual. Repair capability assurance state separately; do not retry the merge request.'}

def take_remote_lock(repo,cfg,task,target):
    p=intent_path(repo,task)
    if p.exists():
        old=r.read_json(p)
        r.need(old.get('state') in ('COMPLETE','ABORTED'),'Previous merge intent needs recovery before another request.')
    r.need(r.remote_head(repo,cfg['remote'],LOCK_BRANCH) is None,'Another integration holds the remote lease. Wait; never steal it.')
    nonce=uuid.uuid4().hex;tree=r.git(repo,'rev-parse',target+'^{tree}')[1]
    lock_sha=r.git(repo,'commit-tree',tree,'-p',target,stdin=f'Agentic integration lease {nonce}\n')[1]
    intent={'state':'LOCK_REQUESTED','nonce':nonce,'lock_sha':lock_sha,'task_id':task['task_id'],'mr_iid':task['mr_iid'],'candidate_sha':task['candidate_sha'],'target_sha':target,'created_at':r.now()}
    r.atomic_json(p,intent)
    ref='refs/heads/'+LOCK_BRANCH
    # Exact empty lease means create only if absent. No target branch push.
    r.git(repo,'push','--porcelain',f'--force-with-lease={ref}:',cfg['remote'],f'{lock_sha}:{ref}')
    r.need(r.remote_head(repo,cfg['remote'],LOCK_BRANCH)==lock_sha,'Remote lease ownership uncertain. Recover, do not resend.')
    intent['state']='LOCKED';r.atomic_json(p,intent);return intent

def release_remote_lock(repo,cfg,task,intent,state):
    ref='refs/heads/'+LOCK_BRANCH
    actual=r.remote_head(repo,cfg['remote'],LOCK_BRANCH)
    r.need(actual in (None,intent['lock_sha']),'Remote integration lease was replaced; do not delete another owner lock.')
    if actual:
        r.git(repo,'push','--porcelain',f'--force-with-lease={ref}:{actual}',cfg['remote'],':'+ref)
        r.need(r.remote_head(repo,cfg['remote'],LOCK_BRANCH) is None,'Lease release not confirmed.')
    intent['state']=state;intent['closed_at']=r.now();r.atomic_json(intent_path(repo,task),intent)

def execute(repo,task,cfg,apply=False,recover=False):
    r.require_coordinator(repo,task['developer']);r.mr_task(task)
    with r.local_lock(repo):
        if recover:
            r.config_ready(cfg,True);intent=r.read_json(intent_path(repo,task))
            r.need(intent.get('mr_iid')==task['mr_iid'] and intent.get('candidate_sha')==task['candidate_sha'],'Recovery must use the original intent/candidate.')
            _,mr=r.project_pull(repo,cfg,task);target=r.fetch_target(repo,cfg)
            proof=r.integration_proof(repo,task,mr,target)
            factual_assurance=None
            if task.get('schema_version')==3:
                try:factual_assurance=assurance.validate_for_merge(repo,cfg,task,require_closed=False,verify_server=apply)
                except r.AEError as e:factual_assurance={'result':'ASSURANCE_INVALID_OR_STALE','error':str(e),'blocking':[{'gate_id':'contract_or_policy','status':'INVALID'}]}
            elif task.get('schema_version')==2:
                factual_assurance=legacy_factual_assurance(cfg,task)
            if apply:release_remote_lock(repo,cfg,task,intent,'COMPLETE')
            trace=traceability_after_integration(repo,cfg,task,mr,proof,factual_assurance,apply)
            capability=capability_after_integration(repo,cfg,task,proof,apply)
            return {'result':'INTEGRATED','proof':proof,'mode':'RECOVERY_APPLIED' if apply else 'RECOVERY_PREVIEW','cleanup':'CLEANUP_PENDING','assurance':factual_assurance,'traceability':trace,'capability':capability,'build_complete_eligible':False}
        # A repeat after a successful merge verifies, rather than merging again.
        r.config_ready(cfg,True);_,mr=r.project_pull(repo,cfg,task)
        if mr['state']=='merged':
            proof=r.integration_proof(repo,task,mr,r.fetch_target(repo,cfg))
            factual_assurance=None
            if task.get('schema_version')==3:
                try:
                    factual_assurance=assurance.validate_for_merge(repo,cfg,task,require_closed=False,verify_server=apply)
                except r.AEError as e:
                    factual_assurance={'result':'ASSURANCE_INVALID_OR_STALE','error':str(e),'blocking':[{'gate_id':'contract_or_policy','status':'INVALID'}]}
            elif task.get('schema_version')==2:
                factual_assurance=legacy_factual_assurance(cfg,task)
            trace=traceability_after_integration(repo,cfg,task,mr,proof,factual_assurance,apply)
            capability=capability_after_integration(repo,cfg,task,proof,apply)
            p=intent_path(repo,task)
            base={'result':'INTEGRATED','proof':proof,'assurance':factual_assurance,'traceability':trace,'capability':capability,
                  'build_complete_eligible':False}
            if p.exists() and r.read_json(p).get('state') not in ('COMPLETE','ABORTED'):
                base.update(lease='RECOVERY_REQUIRED',notice='Run --recover to verify and release the original integration lease. Integration does not erase pending assurance.')
                return base
            base.update(cleanup='CLEANUP_PENDING',notice='No duplicate merge request was executed. Integration does not imply BUILD COMPLETE or release.')
            return base
        result=preflight(repo,cfg,task,for_execution=apply)
        if not apply or result['mode']=='DEVELOPER_REVIEW':
            result['action']='NO_MERGE_EXECUTED';return result
        intent=take_remote_lock(repo,cfg,task,result['target_sha']);request_started=False
        try:
            # Re-evaluate after acquiring the shared lease. GitLab still enforces its rules.
            result=preflight(repo,cfg,task,True)
            r.need(result['target_sha']==intent['target_sha'],'Target changed during lock acquisition.')
            r.need(r.remote_head(repo,cfg['remote'],LOCK_BRANCH)==intent['lock_sha'],'Lost integration lease.')
            intent['state']='REQUESTING';r.atomic_json(intent_path(repo,task),intent);request_started=True
            _a=r.forge_adapter(cfg)
            r.forge_call(cfg,_a.merge,repo,cfg,task,cfg['merge_method'])
            _,merged=r.project_pull(repo,cfg,task);target=r.fetch_target(repo,cfg)
            proof=r.integration_proof(repo,task,merged,target)
            # Validate target parents for the newly performed normal merge when available.
            if cfg['merge_method']=='merge_commit':
                parents=r.git(repo,'show','-s','--format=%P',proof['result_sha'])[1].split()
                r.need(parents==[intent['target_sha'],task['candidate_sha']],'Unexpected merge parents; preserve evidence and recover.')
            release_remote_lock(repo,cfg,task,intent,'COMPLETE')
            trace=traceability_after_integration(repo,cfg,task,merged,proof,result.get('assurance'),True)
            capability=capability_after_integration(repo,cfg,task,proof,True)
            return {'result':'INTEGRATED','proof':proof,'executor':'AGENT_MERGE','cleanup':'CLEANUP_PENDING','assurance':result.get('assurance'),'traceability':trace,'capability':capability,'build_complete_eligible':False,'notice':'No deployment or release claim performed. Capability BUILD COMPLETE, when applicable, is decided separately after product + POST_INTEGRATION assurance.'}
        except Exception:
            if not request_started:release_remote_lock(repo,cfg,task,intent,'ABORTED')
            # Once a request could have reached GitLab, retain the lease until recovery.
            raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path.cwd());p.add_argument('--task',type=Path,required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--recover',action='store_true');a=p.parse_args()
    try:
        repo=r.root(a.repo);r.need(repo==Path(__file__).resolve().parents[2],'Run the coordinator-installed helper.')
        _,task=r.load_task(repo,a.task);result=execute(repo,task,r.config(repo),a.apply,a.recover)
        print(json.dumps(result,indent=2));return 0
    except (r.AEError,OSError,ValueError,TypeError,KeyError) as e:return r.cli_error(e)
if __name__=='__main__':raise SystemExit(main())
