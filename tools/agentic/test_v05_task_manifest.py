#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))

import runtime as r
import contract
import pool
import merge


def config_fixture():
    cfg=json.loads((HERE.parents[1]/'.agentic/project.json').read_text())
    cfg.update(project_name='Fixture',target_branch='main',forge={'provider':'gitlab','host':'gitlab.example.invalid','repo':'group/product'},configuration_approved=True,remote_actions_ready=True,remote_actions_review_ref='setup/remote',team_coordination_ref='team/board',merge_method='merge_commit',commands={'test':'fixture'},ci_policy='source SHA')
    cfg['pool']['size']=3
    cfg['merge_policy'].update(approved=True,approval_ref='setup/merge')
    cfg['cleanup'].update(approved=True,approval_ref='setup/cleanup')
    cfg['assurance_policy'].update(approved=True,approval_ref='setup/assurance')
    return cfg


class TaskManifestCase(unittest.TestCase):
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
    def make_contract(self, *, assurance=None, material=False, override=None):
        impact={k:'ROUTINE' for k in contract.IMPACT_KEYS};owner=None
        if material:
            impact['architecture']='MATERIAL';impact['security_privacy_data']='MATERIAL';impact['human_ownership']='MATERIAL'
            owner={'owner_ref':'team:dev-a','attestation_provider':'gitlab','attestation_identity':'dev-a-user'}
        return {
          'contract_schema_version':2,'evidence_expectations':[],'story_id':'STORY-101','assigned_developer':'dev-a','sprint':'sprint-01','risk_tier':3 if material else 1,
          'publication':{'canonical_backlog':{'path':'backlog/features/FEAT-101.md','revision':'REV-03','publication_sha':self.base,'blob_sha':self.backlog_blob},'story_card':{'path':'backlog/stories/STORY-101.md','publication_sha':self.base,'blob_sha':self.story_blob}},
          'traceability_root':['REQ-101'],'ac_ids':['AC-FUNC-01'],'scope_ref':'backlog/features/FEAT-101.md#scope','preserve_ref':'backlog/features/FEAT-101.md#preserve','dependency_ref':'backlog/features/FEAT-101.md#dependency',
          'engineering_owner':owner,'engineering_impact':impact,'story_required_assurance':assurance or [],'merge_mode_override':override,
          'build_start_ref':'backlog/features/FEAT-101.md#build-start','final_acceptance_ref':'backlog/features/FEAT-101.md#final','capability_id':'FEAT-101'}
    def write_contract(self,data):
        path=contract.registered_contract_path(self.repo,'dev-a','STORY-101');path.parent.mkdir(parents=True,exist_ok=True);r.atomic_json(path,data);return path
    def lease(self,apply=True):return pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',apply)
    def release(self,a,head=None):
        path=Path(a['workspace']);head=head or self.g('rev-parse','HEAD',repo=path);l=a['lease']
        checks={'checked_at':r.now(),'lease_token':l['token'],'processes_stopped':True,'processes_stopped_ref':'fixture/process','records_saved':True,'records_saved_ref':'fixture/report'}
        return pool.release(self.repo,a['lane_id'],l['token'],head,checks,True)

    def test_build_requires_registered_contract(self):
        with self.assertRaises(r.AEError):self.lease()

    def test_preview_validates_but_does_not_create_task(self):
        self.write_contract(self.make_contract())
        out=self.lease(False);self.assertEqual('PREVIEW_ONLY',out['mode']);self.assertRegex(out['contract_digest'],r'^[0-9a-f]{64}$')
        self.assertFalse(r.task_path(self.repo,'dev-a','STORY-101').exists())

    def test_build_creates_schema3_manifest(self):
        c=self.make_contract();path=self.write_contract(c);a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertEqual(3,t['schema_version']);self.assertEqual(2,t['contract']['schema_version']);self.assertEqual(contract.contract_digest(c),t['contract']['contract_digest'])
        self.assertEqual(path.resolve().relative_to(self.repo).as_posix(),t['contract']['contract_ref']);self.assertEqual(1,t['risk_tier']);self.assertIsNone(t['candidate_sha'])
        r.validate_task_schema3(t);self.release(a)

    def test_contract_snapshot_is_bounded_not_full_copy(self):
        self.write_contract(self.make_contract());a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertNotIn('engineering_impact',t['contract']);self.assertIn('material_impact',t['contract']);self.assertNotIn('story_required_assurance',t['contract']);self.release(a)

    def test_fast_path_assurance_registry_is_empty(self):
        self.write_contract(self.make_contract());a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertEqual({},t['assurance']['pre_merge']);self.assertEqual([],t['assurance']['downstream_obligations']);self.release(a)

    def test_premerge_gate_initialized_only_when_required(self):
        obligation=[{'gate_id':'security_review','stage':'PRE_MERGE','mode':'BOUNDED_MATERIAL','evidence_plan_ref':'backlog/features/FEAT-101.md#security'}]
        self.cfg['assurance_policy']['human_attestors']={'security_review':{'BOUNDED_MATERIAL':['security-champion']}};self.write_cfg();self.write_contract(self.make_contract(assurance=obligation,material=True));a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        g=t['assurance']['pre_merge']['security_review'];self.assertEqual('PENDING',g['status']);self.assertEqual('COMPOSITE',g['attestor_class']);self.assertEqual('CANDIDATE',g['binding_class']);self.assertIsNone(g['binding']['candidate_sha']);self.release(a)

    def test_technical_review_not_duplicated(self):
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':'technical_review','stage':'PRE_MERGE','mode':None}];self.write_cfg();self.write_contract(self.make_contract());a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertNotIn('technical_review',t['assurance']['pre_merge']);self.assertIn('technical_review',t['assurance']['policy_snapshot']['project_required']);self.release(a)

    def test_downstream_obligation_carried_without_story_gate_state(self):
        obligation=[{'gate_id':'human_understanding','stage':'POST_INTEGRATION','mode':'ASYNC_CAPABILITY','evidence_plan_ref':'backlog/features/FEAT-101.md#ownership'}]
        self.write_contract(self.make_contract(assurance=obligation,material=True));a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertEqual({},t['assurance']['pre_merge']);d=t['assurance']['downstream_obligations'][0];self.assertEqual('human_understanding',d['gate_id']);self.assertEqual('FEAT-101',d['capability_id']);self.release(a)

    def test_release_pointer_is_downstream_only(self):
        obligation=[{'gate_id':'traceability_completion','stage':'RELEASE_POINTER','mode':'MECHANICAL','evidence_plan_ref':'backlog/features/FEAT-101.md#release'}]
        self.write_contract(self.make_contract(assurance=obligation));a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertEqual('RELEASE_POINTER',t['assurance']['downstream_obligations'][0]['stage']);self.release(a)

    def test_task_inherits_contract_risk_and_merge_override(self):
        c=self.make_contract(material=True,override='DEVELOPER_REVIEW');self.write_contract(c);a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        self.assertEqual(3,t['risk_tier']);self.assertEqual('DEVELOPER_REVIEW',t['merge_mode_override']);self.release(a)

    def test_first_candidate_binds_pending_candidate_gate(self):
        obligation=[{'gate_id':'security_review','stage':'PRE_MERGE','mode':'BOUNDED_MATERIAL','evidence_plan_ref':'backlog/features/FEAT-101.md#security'}]
        self.cfg['assurance_policy']['human_attestors']={'security_review':{'BOUNDED_MATERIAL':['security-champion']}};self.write_cfg();self.write_contract(self.make_contract(assurance=obligation,material=True));a=self.lease();w=Path(a['workspace']);(w/'new.txt').write_text('x');self.g('add','new.txt',repo=w);self.g('commit','-m','candidate',repo=w);head=self.g('rev-parse','HEAD',repo=w);self.release(a,head)
        t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'));self.assertEqual(head,t['candidate_sha']);self.assertEqual(head,t['assurance']['pre_merge']['security_review']['binding']['candidate_sha']);r.validate_task_schema3(t)

    def test_fix_new_candidate_marks_closed_candidate_gate_needs_revalidation(self):
        obligation=[{'gate_id':'security_review','stage':'PRE_MERGE','mode':'BOUNDED_MATERIAL','evidence_plan_ref':'backlog/features/FEAT-101.md#security'}]
        self.cfg['assurance_policy']['human_attestors']={'security_review':{'BOUNDED_MATERIAL':['security-champion']}};self.write_cfg();self.write_contract(self.make_contract(assurance=obligation,material=True));a=self.lease();w=Path(a['workspace']);(w/'new.txt').write_text('x');self.g('add','new.txt',repo=w);self.g('commit','-m','candidate',repo=w);head=self.g('rev-parse','HEAD',repo=w);self.release(a,head)
        tp=r.task_path(self.repo,'dev-a','STORY-101');t=r.read_json(tp);g=t['assurance']['pre_merge']['security_review'];g['status']='SATISFIED';g['observed_at']=r.now();g['attestor_ref']='gitlab:note:1';g['evidence_refs']=['evidence/security'];r.atomic_json(tp,t)
        f=pool.lease(self.repo,'dev-a','STORY-101','lane-01','FIX',head,'fix-101',True);wf=Path(f['workspace']);(wf/'new.txt').write_text('y');self.g('add','new.txt',repo=wf);self.g('commit','-m','fix',repo=wf);head2=self.g('rev-parse','HEAD',repo=wf);self.release(f,head2)
        t2=r.read_json(tp);g2=t2['assurance']['pre_merge']['security_review'];self.assertEqual('NEEDS_REVALIDATION',g2['status']);self.assertEqual(head,g2['revalidation']['from_binding']);self.assertEqual(head2,g2['revalidation']['to_binding'])

    def test_contract_change_after_binding_blocks_fix(self):
        c=self.make_contract();self.write_contract(c);a=self.lease();self.release(a);c['ac_ids']=['AC-CHANGED'];self.write_contract(c)
        t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'))
        with self.assertRaises(r.AEError):pool.lease(self.repo,'dev-a','STORY-101','lane-01','FIX',t['candidate_sha'],'fix-101',True)

    def test_wrong_registered_story_contract_blocks_build(self):
        c=self.make_contract();c['story_id']='STORY-OTHER';path=contract.registered_contract_path(self.repo,'dev-a','STORY-101');path.parent.mkdir(parents=True,exist_ok=True);r.atomic_json(path,c)
        with self.assertRaises(r.AEError):self.lease()

    def test_wrong_publication_blob_blocks_before_lane_mutation(self):
        c=self.make_contract();c['publication']['story_card']['blob_sha']='a'*40;self.write_contract(c)
        with self.assertRaises(r.AEError):self.lease()
        self.assertEqual('IDLE',pool.load(self.repo)['lanes']['lane-01']['state'])

    def test_material_contract_requires_owner_before_task_creation(self):
        c=self.make_contract(material=True);c['engineering_owner']=None;self.write_contract(c)
        with self.assertRaises(r.AEError):self.lease()
        self.assertFalse(r.task_path(self.repo,'dev-a','STORY-101').exists())

    def test_schema3_validator_rejects_virtual_technical_review_duplicate(self):
        self.write_contract(self.make_contract());a=self.lease();tp=r.task_path(self.repo,'dev-a','STORY-101');t=r.read_json(tp);t['assurance']['pre_merge']['technical_review']={'bad':True}
        with self.assertRaises(r.AEError):r.validate_task_schema3(t);self.release(a)

    def test_custom_premerge_gate_creates_generic_state_when_runtime_semantics_complete(self):
        self.cfg['assurance_policy']['custom_gates']={'project_defined:data_retention':{
            'policy_ref':'docs/policies/data.md#retention','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],
            'attestor_class':'TOOL','binding_class':'CANDIDATE','minimum_evidence':1,
            'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.write_cfg()
        obligation=[{'gate_id':'project_defined:data_retention','stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#data'}];self.write_contract(self.make_contract(assurance=obligation))
        a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'));g=t['assurance']['pre_merge']['project_defined:data_retention']
        self.assertEqual('TOOL',g['attestor_class']);self.assertEqual('CANDIDATE',g['binding_class']);self.assertEqual('PENDING',g['status']);self.release(a)

    def test_traceability_block_initialized(self):
        self.write_contract(self.make_contract());a=self.lease();t=r.read_json(r.task_path(self.repo,'dev-a','STORY-101'));self.assertEqual({'record_ref':None,'capability_record_ref':None,'release_pointer_refs':[]},t['traceability']);self.release(a)


class InterimMergeGuardTests(unittest.TestCase):
    def fake(self, mode='AGENT_MERGE'):
        cfg=config_fixture();cfg['merge_mode']=mode
        candidate='a'*40;target='b'*40
        task={'schema_version':3,'task_id':'STORY-101','developer':'dev-a','branch':'work/dev-a/STORY-101','candidate_sha':candidate,'mr_iid':1,'merge_mode_override':None,'contract':{'evidence_expectations':[]},'assurance':{'pre_merge':{'security_review':{'status':'PENDING'}}}}
        q={'target_sha':target}
        # The normalized pull preflight consumes, with the GitLab raw payload kept in
        # _raw because that is where the adapter reads its own mergeable verdict.
        mr={'number':1,'state':'open','draft':False,'source_branch':task['branch'],'target_branch':'main',
            'head_sha':candidate,'same_project':True,'has_conflicts':False,'auto_merge_queued':False,
            'deletes_source_on_merge':False,'ci':{'status':'success','sha':candidate},
            '_raw':{'detailed_merge_status':'mergeable'}}
        return cfg,task,q,mr,target
    def patches(self,task,q,mr,target):
        def assurance_result(*a,**k):
            if k.get('require_closed'):
                raise r.AEError('Mandatory PRE_MERGE assurance is not closed: security_review=PENDING')
            return {'result':'ASSURANCE_PRE_MERGE_PENDING','blocking':[{'gate_id':'security_review','status':'PENDING','mode':None}],'closed':[],'waived_exceptions':[]}
        return (
          patch.object(r,'config_ready',lambda *a,**k:None),
          patch.object(merge,'quality',lambda *a,**k:q),
          patch.object(r,'project_pull',lambda *a,**k:({'id':1,'full_name':'group/product','merge_methods':{'merge_commit'}},mr)),
          patch.object(r,'remote_head',lambda *a,**k:task['candidate_sha']),
          patch.object(r,'fetch_target',lambda *a,**k:target),
          patch.object(merge.assurance,'validate_for_merge',assurance_result),
        )
    def test_agent_merge_fails_closed_until_phase8_for_pending_premerge_assurance(self):
        cfg,task,q,mr,target=self.fake('AGENT_MERGE')
        ps=self.patches(task,q,mr,target)
        with ps[0],ps[1],ps[2],ps[3],ps[4],ps[5]:
            with self.assertRaises(r.AEError):merge.preflight(Path('.'),cfg,task)
    def test_developer_review_can_surface_mr_ready_without_erasing_pending_assurance(self):
        cfg,task,q,mr,target=self.fake('DEVELOPER_REVIEW')
        ps=self.patches(task,q,mr,target)
        with ps[0],ps[1],ps[2],ps[3],ps[4],ps[5]:
            out=merge.preflight(Path('.'),cfg,task)
        self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',out['result'])
        self.assertIn('security_review',task['assurance']['pre_merge'])


if __name__=='__main__':unittest.main(verbosity=2)
