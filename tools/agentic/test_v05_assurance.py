#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))

import runtime as r
import contract
import pool
import assurance


def config_fixture():
    cfg=json.loads((HERE.parents[1]/'.agentic/project.json').read_text())
    cfg.update(project_name='Fixture',target_branch='main',forge={'provider':'gitlab','host':'gitlab.example.invalid','repo':'group/product'},configuration_approved=True,remote_actions_ready=True,remote_actions_review_ref='setup/remote',team_coordination_ref='team/board',merge_method='merge_commit',commands={'test':'fixture'},ci_policy='source SHA')
    cfg['pool']['size']=3
    cfg['merge_mode']='DEVELOPER_REVIEW'
    cfg['merge_policy'].update(approved=True,approval_ref='setup/merge')
    cfg['cleanup'].update(approved=True,approval_ref='setup/cleanup')
    cfg['assurance_policy'].update(approved=True,approval_ref='setup/assurance')
    return cfg


class AssuranceRegistryCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.top=Path(self.tmp.name);self.repo=self.top/'repo';self.repo.mkdir()
        self.g('init','-b','main');self.g('config','user.name','Fixture');self.g('config','user.email','fixture@example.invalid')
        (self.repo/'.gitignore').write_text('.agentic/local/\n.claude/worktrees/\n')
        (self.repo/'.agentic').mkdir()
        p=self.repo/'docs/agentic/AGENT_RULES.md';p.parent.mkdir(parents=True);p.write_text('rules')
        (self.repo/'backlog/features').mkdir(parents=True);(self.repo/'backlog/stories').mkdir(parents=True)
        (self.repo/'backlog/features/FEAT-101.md').write_text('# FEAT-101\n')
        (self.repo/'backlog/stories/STORY-101.md').write_text('# STORY-101\n')
        self.cfg=config_fixture();self.write_cfg();self.g('add','.');self.g('commit','-m','base');self.base=self.g('rev-parse','HEAD')
        self.backlog_blob=self.g('rev-parse',self.base+':backlog/features/FEAT-101.md');self.story_blob=self.g('rev-parse',self.base+':backlog/stories/STORY-101.md')
        self.remote=self.top/'remote.git';r.run(['git','init','--bare',str(self.remote)]);self.g('remote','add','origin',str(self.remote));self.g('push','origin','main');self.g('switch','-c','ae/records/dev-a/sprint-01')
        pool.init(self.repo,'dev-a',self.base,True)
    def tearDown(self):self.tmp.cleanup()
    def g(self,*args,repo=None):return r.git(repo or self.repo,*args)[1]
    def write_cfg(self):(self.repo/'.agentic/project.json').write_text(json.dumps(self.cfg))
    def contract_data(self, obligations=None, material=False, risk=1):
        impact={k:'ROUTINE' for k in contract.IMPACT_KEYS};owner=None
        if material:
            impact['architecture']='MATERIAL';impact['security_privacy_data']='MATERIAL';impact['human_ownership']='MATERIAL'
            owner={'owner_ref':'team:dev-a','attestation_provider':'gitlab','attestation_identity':'dev-a-user'}
        return {
          'contract_schema_version':2,'evidence_expectations':[],'story_id':'STORY-101','assigned_developer':'dev-a','sprint':'sprint-01','risk_tier':risk,
          'publication':{'canonical_backlog':{'path':'backlog/features/FEAT-101.md','revision':'REV-03','publication_sha':self.base,'blob_sha':self.backlog_blob},'story_card':{'path':'backlog/stories/STORY-101.md','publication_sha':self.base,'blob_sha':self.story_blob}},
          'traceability_root':['REQ-101'],'ac_ids':['AC-FUNC-01'],'scope_ref':'backlog/features/FEAT-101.md#scope','preserve_ref':'backlog/features/FEAT-101.md#preserve','dependency_ref':'backlog/features/FEAT-101.md#dependency',
          'engineering_owner':owner,'engineering_impact':impact,'story_required_assurance':obligations or [],'merge_mode_override':None,
          'build_start_ref':'backlog/features/FEAT-101.md#build-start','final_acceptance_ref':'backlog/features/FEAT-101.md#final','capability_id':'FEAT-101'}
    def register(self,data):
        p=contract.registered_contract_path(self.repo,'dev-a','STORY-101');p.parent.mkdir(parents=True,exist_ok=True);r.atomic_json(p,data);return p
    def make_task(self, obligations=None, material=False, risk=1, candidate=True):
        self.register(self.contract_data(obligations,material,risk));a=pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',True)
        if candidate:
            w=Path(a['workspace']);(w/'candidate.txt').write_text('candidate');self.g('add','candidate.txt',repo=w);self.g('commit','-m','candidate',repo=w);head=self.g('rev-parse','HEAD',repo=w)
        else: head=self.base
        checks={'checked_at':r.now(),'lease_token':a['lease']['token'],'processes_stopped':True,'processes_stopped_ref':'fixture/process','records_saved':True,'records_saved_ref':'fixture/report'}
        pool.release(self.repo,a['lane_id'],a['lease']['token'],head,checks,True)
        return r.task_path(self.repo,'dev-a','STORY-101'), (head if candidate else None)
    def tool_obligation(self):
        return [{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FEAT-101.md#nfr'}]
    def security_obligation(self):
        self.cfg['assurance_policy']['human_attestors']={'security_review':{'BOUNDED_MATERIAL':['security-champion']}};self.write_cfg()
        return [{'gate_id':'security_review','stage':'PRE_MERGE','mode':'BOUNDED_MATERIAL','evidence_plan_ref':'backlog/features/FEAT-101.md#security'}]

    def test_fast_path_status_empty(self):
        tp,_=self.make_task(candidate=True);out=assurance.status(self.repo,tp);self.assertEqual({},out['pre_merge']);self.assertEqual([],out['downstream_obligations'])

    def test_evidence_preview_does_not_write(self):
        tp,_=self.make_task(self.tool_obligation());before=tp.read_bytes();out=assurance.add_evidence(self.repo,tp,'nfr_performance',['evidence/nfr.json'],False);self.assertEqual('PREVIEW_ONLY',out['mode']);self.assertEqual(before,tp.read_bytes())

    def test_evidence_apply(self):
        tp,_=self.make_task(self.tool_obligation());out=assurance.add_evidence(self.repo,tp,'nfr_performance',['evidence/nfr.json'],True);self.assertEqual('APPLIED',out['mode']);self.assertEqual(['evidence/nfr.json'],r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['evidence_refs'])

    def test_duplicate_evidence_is_idempotent(self):
        tp,_=self.make_task(self.tool_obligation());assurance.add_evidence(self.repo,tp,'nfr_performance',['evidence/nfr.json'],True);assurance.add_evidence(self.repo,tp,'nfr_performance',['evidence/nfr.json'],True);self.assertEqual(1,len(r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['evidence_refs']))

    def test_invalid_evidence_ref_rejected(self):
        tp,_=self.make_task(self.tool_obligation());
        with self.assertRaises(r.AEError):assurance.add_evidence(self.repo,tp,'nfr_performance',['bad\nref'],True)

    def test_begin_sets_in_progress(self):
        tp,_=self.make_task(self.tool_obligation());assurance.begin(self.repo,tp,'nfr_performance','tool:benchmark-runner',True);g=r.read_json(tp)['assurance']['pre_merge']['nfr_performance'];self.assertEqual('IN_PROGRESS',g['status']);self.assertEqual('tool:benchmark-runner',g['attestor_ref'])

    def test_failed_disposition_records_evidence(self):
        tp,_=self.make_task(self.tool_obligation());out=assurance.disposition(self.repo,tp,'nfr_performance','FAILED','tool:benchmark',['evidence/fail.json'],True);self.assertEqual('ASSURANCE_FAILED',out['result']);g=r.read_json(tp)['assurance']['pre_merge']['nfr_performance'];self.assertEqual('FAILED',g['status']);self.assertIn('evidence/fail.json',g['evidence_refs'])

    def test_blocked_disposition(self):
        tp,_=self.make_task(self.tool_obligation());assurance.disposition(self.repo,tp,'nfr_performance','BLOCKED','control-plane:missing-env',[],True);self.assertEqual('BLOCKED',r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['status'])

    def test_failed_gate_can_restart(self):
        tp,_=self.make_task(self.tool_obligation());assurance.disposition(self.repo,tp,'nfr_performance','FAILED','tool:benchmark',[],True);assurance.begin(self.repo,tp,'nfr_performance','tool:benchmark-2',True);self.assertEqual('IN_PROGRESS',r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['status'])

    def test_tool_gate_satisfied_with_exact_candidate_and_evidence(self):
        tp,head=self.make_task(self.tool_obligation());out=assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:benchmark',['evidence/nfr.json'],True);self.assertEqual('ASSURANCE_SATISFIED',out['result']);g=r.read_json(tp)['assurance']['pre_merge']['nfr_performance'];self.assertEqual('SATISFIED',g['status']);self.assertEqual(head,g['binding']['candidate_sha'])

    def test_satisfy_tool_preview_no_write(self):
        tp,_=self.make_task(self.tool_obligation());before=tp.read_bytes();out=assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:benchmark',['evidence/nfr.json'],False);self.assertEqual('PREVIEW_ONLY',out['mode']);self.assertEqual(before,tp.read_bytes())

    def test_human_gate_cannot_be_satisfied_by_phase5_tool(self):
        obligations=[{'gate_id':'human_understanding','stage':'PRE_MERGE','mode':'STORY_EXPLAIN_BACK','evidence_plan_ref':'backlog/features/FEAT-101.md#owner'}]
        tp,_=self.make_task(obligations,material=True,risk=3)
        with self.assertRaises(r.AEError):assurance.satisfy_tool(self.repo,tp,'human_understanding','tool:fake',['evidence/x'],True)

    def test_composite_security_gate_can_collect_evidence_but_not_satisfy(self):
        tp,_=self.make_task(self.security_obligation(),material=True,risk=3);assurance.add_evidence(self.repo,tp,'security_review',['evidence/security.json'],True)
        with self.assertRaises(r.AEError):assurance.satisfy_tool(self.repo,tp,'security_review','tool:sast',['evidence/sast.json'],True)
        self.assertEqual('PENDING',r.read_json(tp)['assurance']['pre_merge']['security_review']['status'])

    def test_premerge_closure_pending(self):
        tp,_=self.make_task(self.tool_obligation());out=assurance.check_pre_merge(self.repo,tp);self.assertEqual('ASSURANCE_PRE_MERGE_PENDING',out['result']);self.assertEqual('nfr_performance',out['blocking'][0]['gate_id'])

    def test_premerge_closure_closed_for_tool_gate(self):
        tp,_=self.make_task(self.tool_obligation());assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:benchmark',['evidence/nfr.json'],True);out=assurance.check_pre_merge(self.repo,tp);self.assertEqual('ASSURANCE_PRE_MERGE_CLOSED',out['result'])

    def test_virtual_technical_review_pending(self):
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':'technical_review','stage':'PRE_MERGE','mode':None}];self.write_cfg();tp,_=self.make_task();out=assurance.status(self.repo,tp);self.assertEqual('PENDING',out['technical_review']['status'])

    def test_virtual_technical_review_satisfied_exact_candidate(self):
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':'technical_review','stage':'PRE_MERGE','mode':None}];self.write_cfg();tp,head=self.make_task();t=r.read_json(tp);t['review']={'verdict':'ACCEPT','candidate_sha':head,'report_ref':'records/review.md'};r.atomic_json(tp,t);out=assurance.status(self.repo,tp);self.assertEqual('SATISFIED',out['technical_review']['status'])

    def test_virtual_technical_review_wrong_candidate_pending(self):
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':'technical_review','stage':'PRE_MERGE','mode':None}];self.write_cfg();tp,_=self.make_task();t=r.read_json(tp);t['review']={'verdict':'ACCEPT','candidate_sha':'a'*40,'report_ref':'records/review.md'};r.atomic_json(tp,t);out=assurance.status(self.repo,tp);self.assertEqual('PENDING',out['technical_review']['status'])

    def test_custom_tool_gate_full_semantics_can_satisfy(self):
        gid='project_defined:data_retention';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/policies/data.md#retention','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],'attestor_class':'TOOL','binding_class':'CANDIDATE','minimum_evidence':1,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.write_cfg()
        ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#data'}];tp,_=self.make_task(ob);assurance.satisfy_tool(self.repo,tp,gid,'tool:data-check',['evidence/data.json'],True);self.assertEqual('SATISFIED',r.read_json(tp)['assurance']['pre_merge'][gid]['status'])

    def test_custom_gate_minimum_evidence_enforced(self):
        gid='project_defined:data_retention';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/policies/data.md#retention','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],'attestor_class':'TOOL','binding_class':'CANDIDATE','minimum_evidence':2,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.write_cfg()
        ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#data'}];tp,_=self.make_task(ob)
        with self.assertRaises(r.AEError):assurance.satisfy_tool(self.repo,tp,gid,'tool:data-check',['evidence/one.json'],True)
        assurance.satisfy_tool(self.repo,tp,gid,'tool:data-check',['evidence/one.json','evidence/two.json'],True);self.assertEqual('SATISFIED',r.read_json(tp)['assurance']['pre_merge'][gid]['status'])

    def test_custom_gate_minimal_policy_ref_fails_when_effective(self):
        gid='project_defined:data_retention';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/policies/data.md#retention'}};self.write_cfg();ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#data'}];self.register(self.contract_data(ob))
        with self.assertRaises(r.AEError):pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',True)

    def test_custom_gate_wrong_stage_fails_precheck(self):
        gid='project_defined:data_retention';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/policies/data.md#retention','allowed_stages':['POST_INTEGRATION'],'allowed_modes':['CHECK'],'attestor_class':'TOOL','binding_class':'INTEGRATED_CAPABILITY','minimum_evidence':1,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.write_cfg();ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#data'}];self.register(self.contract_data(ob))
        with self.assertRaises(r.AEError):pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',True)

    def test_custom_human_gate_requires_allowlist(self):
        gid='project_defined:privacy_review';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/policies/privacy.md','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],'attestor_class':'AUTHORIZED_HUMAN','binding_class':'CANDIDATE','minimum_evidence':1,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.write_cfg();ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#privacy'}];self.register(self.contract_data(ob))
        with self.assertRaises(r.AEError):pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',True)

    def test_tampered_attestor_class_rejected_by_registry(self):
        tp,_=self.make_task(self.tool_obligation());t=r.read_json(tp);t['assurance']['pre_merge']['nfr_performance']['attestor_class']='AUTHORIZED_HUMAN';r.atomic_json(tp,t)
        with self.assertRaises(r.AEError):assurance.status(self.repo,tp)

    def test_runtime_validator_satisfied_requires_evidence(self):
        tp,_=self.make_task(self.tool_obligation());t=r.read_json(tp);g=t['assurance']['pre_merge']['nfr_performance'];g['status']='SATISFIED';g['observed_at']=r.now();g['attestor_ref']='tool:x';g['evidence_refs']=[]
        with self.assertRaises(r.AEError):r.validate_task_schema3(t)

    def test_closed_gate_evidence_is_immutable(self):
        tp,_=self.make_task(self.tool_obligation());assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:benchmark',['evidence/nfr.json'],True)
        with self.assertRaises(r.AEError):assurance.add_evidence(self.repo,tp,'nfr_performance',['evidence/late.json'],True)

    def test_needs_revalidation_cannot_be_closed_in_phase5(self):
        tp,head=self.make_task(self.tool_obligation());t=r.read_json(tp);g=t['assurance']['pre_merge']['nfr_performance'];g['status']='NEEDS_REVALIDATION';g['revalidation'].update(from_binding=head,to_binding=head,disposition='NOT_EVALUATED');r.atomic_json(tp,t)
        with self.assertRaises(r.AEError):assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:benchmark',['evidence/nfr.json'],True)

    def test_unknown_gate_not_required_rejected(self):
        tp,_=self.make_task();
        with self.assertRaises(r.AEError):assurance.add_evidence(self.repo,tp,'security_review',['evidence/x'],True)


if __name__=='__main__': unittest.main(verbosity=2)
