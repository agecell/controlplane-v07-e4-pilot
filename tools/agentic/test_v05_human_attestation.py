#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))

import runtime as r
import forge
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


class HumanAttestationCase(unittest.TestCase):
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
        self.notes={}
    def tearDown(self):self.tmp.cleanup()
    def g(self,*args,repo=None):return r.git(repo or self.repo,*args)[1]
    def write_cfg(self):(self.repo/'.agentic/project.json').write_text(json.dumps(self.cfg))
    def contract_data(self, obligations, risk=3, owner=True):
        impact={k:'ROUTINE' for k in contract.IMPACT_KEYS}
        impact['human_ownership']='MATERIAL';impact['security_privacy_data']='MATERIAL';impact['architecture']='MATERIAL'
        eng_owner={'owner_ref':'team:dev-a','attestation_provider':'gitlab','attestation_identity':'dev-a-user'} if owner else None
        return {
          'contract_schema_version':2,'evidence_expectations':[],'story_id':'STORY-101','assigned_developer':'dev-a','sprint':'sprint-01','risk_tier':risk,
          'publication':{'canonical_backlog':{'path':'backlog/features/FEAT-101.md','revision':'REV-03','publication_sha':self.base,'blob_sha':self.backlog_blob},'story_card':{'path':'backlog/stories/STORY-101.md','publication_sha':self.base,'blob_sha':self.story_blob}},
          'traceability_root':['REQ-101'],'ac_ids':['AC-FUNC-01'],'scope_ref':'backlog/features/FEAT-101.md#scope','preserve_ref':'backlog/features/FEAT-101.md#preserve','dependency_ref':'backlog/features/FEAT-101.md#dependency',
          'engineering_owner':eng_owner,'engineering_impact':impact,'story_required_assurance':obligations,'merge_mode_override':None,
          'build_start_ref':'backlog/features/FEAT-101.md#build-start','final_acceptance_ref':'backlog/features/FEAT-101.md#final','capability_id':'FEAT-101'}
    def register(self,data):
        p=contract.registered_contract_path(self.repo,'dev-a','STORY-101');p.parent.mkdir(parents=True,exist_ok=True);r.atomic_json(p,data);return p
    def make_task(self, obligations, *, owner=True):
        self.register(self.contract_data(obligations,owner=owner));a=pool.lease(self.repo,'dev-a','STORY-101','lane-01','BUILD',self.base,'build-101',True)
        w=Path(a['workspace']);(w/'candidate.txt').write_text('candidate');self.g('add','candidate.txt',repo=w);self.g('commit','-m','candidate',repo=w);head=self.g('rev-parse','HEAD',repo=w)
        checks={'checked_at':r.now(),'lease_token':a['lease']['token'],'processes_stopped':True,'processes_stopped_ref':'fixture/process','records_saved':True,'records_saved_ref':'fixture/report'}
        pool.release(self.repo,a['lane_id'],a['lease']['token'],head,checks,True)
        tp=r.task_path(self.repo,'dev-a','STORY-101');t=r.read_json(tp);t['mr_iid']=42;r.atomic_json(tp,t)
        return tp,head
    def human_ob(self):
        return [{'gate_id':'human_understanding','stage':'PRE_MERGE','mode':'STORY_EXPLAIN_BACK','evidence_plan_ref':'backlog/features/FEAT-101.md#owner'}]
    def arch_ob(self,mode='CONFORMANCE'):
        if mode=='DECISION':
            self.cfg['assurance_policy']['human_attestors']={'architecture_review':{'DECISION':['architect-a']}};self.write_cfg()
        return [{'gate_id':'architecture_review','stage':'PRE_MERGE','mode':mode,'evidence_plan_ref':'backlog/features/FEAT-101.md#architecture'}]
    def security_ob(self,mode='BOUNDED_MATERIAL'):
        mapping={'BOUNDED_MATERIAL':['security-champion'],'CRITICAL_OR_MAJOR':['security-lead']}
        self.cfg['assurance_policy']['human_attestors']={'security_review':mapping};self.write_cfg()
        return [{'gate_id':'security_review','stage':'PRE_MERGE','mode':mode,'evidence_plan_ref':'backlog/features/FEAT-101.md#security'}]
    def fake_project_pull(self,task):
        # The normalized shape project_pull returns in v0.6, not GitLab's own fields.
        return ({'id':1,'full_name':'group/product','merge_methods':{'merge_commit'}},
                {'number':42,'state':'open','draft':False,'source_branch':task['branch'],'target_branch':'main',
                 'head_sha':task['candidate_sha'],'same_project':True})
    def note_api(self, note):
        def api(repo,cfg,endpoint,method='GET',fields=None):
            if '/notes/' in endpoint:return copy.deepcopy(note)
            raise AssertionError(f'unexpected API endpoint {endpoint}')
        return api
    def satisfy_with_note(self,tp,gate,user,refs=None,note_body=None,note_id=99,apply=True,system=False):
        req=assurance.attestation_request(self.repo,tp,gate)
        body=note_body if note_body is not None else req['expected_note']
        note={'system':system,'author':{'username':user},'body':body,'created_at':'2026-09-13T12:00:00+00:00'}
        task=r.read_json(tp)
        with patch.object(r,'project_pull',return_value=self.fake_project_pull(task)),patch.object(forge.GitLabForge,'api',side_effect=self.note_api(note)):
            return assurance.satisfy_human(self.repo,tp,gate,note_id,refs or [],apply)

    def test_request_is_exact_and_owner_scoped(self):
        tp,_=self.make_task(self.human_ob());out=assurance.attestation_request(self.repo,tp,'human_understanding')
        self.assertEqual(['dev-a-user'],out['allowed_forge_users']);self.assertTrue(out['expected_note'].startswith('AE-ASSURE human_understanding STORY-101 '));self.assertEqual(64,len(out['binding_digest']))

    def test_binding_digest_deterministic(self):
        tp,_=self.make_task(self.human_ob());a=assurance.attestation_request(self.repo,tp,'human_understanding');b=assurance.attestation_request(self.repo,tp,'human_understanding');self.assertEqual(a['binding_digest'],b['binding_digest'])

    def test_human_understanding_owner_can_satisfy_without_extra_file(self):
        tp,_=self.make_task(self.human_ob());out=self.satisfy_with_note(tp,'human_understanding','dev-a-user');self.assertEqual('ASSURANCE_SATISFIED',out['result']);g=r.read_json(tp)['assurance']['pre_merge']['human_understanding'];self.assertEqual('SATISFIED',g['status']);self.assertTrue(g['attestor_ref'].endswith('user:dev-a-user'));self.assertTrue(any(x.startswith('human-attestation:gitlab:mr:42:note:99') for x in g['evidence_refs']))

    def test_wrong_engineering_owner_rejected(self):
        tp,_=self.make_task(self.human_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'human_understanding','other-user')

    def test_system_note_rejected(self):
        tp,_=self.make_task(self.human_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'human_understanding','dev-a-user',system=True)

    def test_wrong_digest_body_rejected(self):
        tp,_=self.make_task(self.human_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'human_understanding','dev-a-user',note_body='AE-ASSURE human_understanding STORY-101 '+'0'*64)

    def test_merge_approval_text_does_not_satisfy_assurance(self):
        tp,head=self.make_task(self.human_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'human_understanding','dev-a-user',note_body='APPROVE '+head)

    def test_missing_mr_blocks_human_satisfaction(self):
        tp,_=self.make_task(self.human_ob());t=r.read_json(tp);t['mr_iid']=None;r.atomic_json(tp,t)
        with self.assertRaises(r.AEError):assurance.satisfy_human(self.repo,tp,'human_understanding',99,[],True)

    def test_preview_verifies_server_but_does_not_write(self):
        tp,_=self.make_task(self.human_ob());before=tp.read_bytes();out=self.satisfy_with_note(tp,'human_understanding','dev-a-user',apply=False);self.assertEqual('PREVIEW_ONLY',out['mode']);self.assertEqual(before,tp.read_bytes())

    def test_architecture_conformance_requires_supporting_evidence(self):
        tp,_=self.make_task(self.arch_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'architecture_review','dev-a-user')
        out=self.satisfy_with_note(tp,'architecture_review','dev-a-user',['records/architecture-conformance.md']);self.assertEqual('SATISFIED',out['status'])

    def test_architecture_decision_uses_mode_allowlist(self):
        tp,_=self.make_task(self.arch_ob('DECISION'))
        out=self.satisfy_with_note(tp,'architecture_review','architect-a',['records/architecture-decision.md']);self.assertEqual('architect-a',out['attestor_identity'])

    def test_architecture_decision_owner_not_automatically_authorized(self):
        tp,_=self.make_task(self.arch_ob('DECISION'))
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'architecture_review','dev-a-user',['records/architecture-decision.md'])

    def test_security_composite_requires_tool_or_review_evidence(self):
        tp,_=self.make_task(self.security_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'security_review','security-champion')
        out=self.satisfy_with_note(tp,'security_review','security-champion',['evidence/focused-security.json']);self.assertEqual('SATISFIED',out['status'])

    def test_security_wrong_human_rejected(self):
        tp,_=self.make_task(self.security_ob())
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'security_review','security-lead',['evidence/focused-security.json'])

    def test_critical_security_requires_stronger_mode_allowlist(self):
        tp,_=self.make_task(self.security_ob('CRITICAL_OR_MAJOR'))
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,'security_review','security-champion',['evidence/security.json'])
        out=self.satisfy_with_note(tp,'security_review','security-lead',['evidence/security.json']);self.assertEqual('security-lead',out['attestor_identity'])

    def test_custom_authorized_human_gate(self):
        gid='project_defined:privacy_review';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/privacy.md','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],'attestor_class':'AUTHORIZED_HUMAN','binding_class':'CANDIDATE','minimum_evidence':1,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.cfg['assurance_policy']['human_attestors']={gid:['privacy-a']};self.write_cfg()
        ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#privacy'}];tp,_=self.make_task(ob)
        out=self.satisfy_with_note(tp,gid,'privacy-a',['evidence/privacy.md']);self.assertEqual('SATISFIED',out['status'])

    def test_custom_composite_gate_requires_external_evidence(self):
        gid='project_defined:privacy_review';self.cfg['assurance_policy']['custom_gates']={gid:{'policy_ref':'docs/privacy.md','allowed_stages':['PRE_MERGE'],'allowed_modes':['CHECK'],'attestor_class':'COMPOSITE','binding_class':'CANDIDATE','minimum_evidence':2,'freshness_rule':'IMMUTABLE_BINDING','failure_behavior':'BLOCK_STAGE'}};self.cfg['assurance_policy']['human_attestors']={gid:['privacy-a']};self.write_cfg()
        ob=[{'gate_id':gid,'stage':'PRE_MERGE','mode':'CHECK','evidence_plan_ref':'backlog/features/FEAT-101.md#privacy'}];tp,_=self.make_task(ob)
        with self.assertRaises(r.AEError):self.satisfy_with_note(tp,gid,'privacy-a',['evidence/one.md'])
        out=self.satisfy_with_note(tp,gid,'privacy-a',['evidence/one.md','evidence/two.md']);self.assertEqual('SATISFIED',out['status'])

    def test_tool_gate_rejects_human_attestation_request(self):
        ob=[{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FEAT-101.md#nfr'}];tp,_=self.make_task(ob)
        with self.assertRaises(r.AEError):assurance.attestation_request(self.repo,tp,'nfr_performance')

    def test_needs_revalidation_rejects_new_attestation_until_phase7(self):
        tp,head=self.make_task(self.human_ob());t=r.read_json(tp);g=t['assurance']['pre_merge']['human_understanding'];g['status']='NEEDS_REVALIDATION';g['revalidation'].update(from_binding=head,to_binding=head,disposition='NOT_EVALUATED');r.atomic_json(tp,t)
        with self.assertRaises(r.AEError):assurance.attestation_request(self.repo,tp,'human_understanding')

    def test_tampered_local_human_satisfied_state_rejected_by_status(self):
        tp,_=self.make_task(self.human_ob());t=r.read_json(tp);g=t['assurance']['pre_merge']['human_understanding'];g['status']='SATISFIED';g['observed_at']=r.now();g['attestor_ref']='local:fake';g['evidence_refs']=['human-attestation:gitlab:mr:42:note:99'];r.atomic_json(tp,t)
        with self.assertRaises(r.AEError):assurance.status(self.repo,tp)

    def test_premerge_closure_closes_after_server_human_attestation(self):
        tp,_=self.make_task(self.human_ob());self.satisfy_with_note(tp,'human_understanding','dev-a-user');out=assurance.check_pre_merge(self.repo,tp);self.assertEqual('ASSURANCE_PRE_MERGE_CLOSED',out['result'])

    def test_server_provenance_includes_note_and_user(self):
        tp,_=self.make_task(self.human_ob());out=self.satisfy_with_note(tp,'human_understanding','dev-a-user',note_id=123);self.assertEqual('gitlab:mr:42:note:123:user:dev-a-user',out['attestor_ref'])

    def enable_human_waiver(self):
        self.cfg['assurance_policy']['waiver_policy']['gates']={'human_understanding':{'waivable':True,'risk_owners':['eng-manager']}};self.write_cfg()

    def waiver_request(self,tp,apply=True,risk_owner='eng-manager'):
        return assurance.create_waiver_request(self.repo,tp,'human_understanding','WV-101',risk_owner,'temporary exception','owner walkthrough not yet completed','knowledge continuity risk accepted',['follow-up walkthrough'],'2026-10-01T00:00:00+00:00','before release',apply)

    def apply_waiver_note(self,tp,body,user='eng-manager',note_id=201,apply=True):
        note={'system':False,'author':{'username':user},'body':body,'created_at':'2026-09-13T13:00:00+00:00'};task=r.read_json(tp)
        with patch.object(r,'project_pull',return_value=self.fake_project_pull(task)),patch.object(forge.GitLabForge,'api',side_effect=self.note_api(note)):
            return assurance.apply_waiver(self.repo,tp,'human_understanding','WV-101',note_id,apply)

    def test_waiver_nonwaivable_by_default(self):
        tp,_=self.make_task(self.human_ob())
        with self.assertRaises(r.AEError):self.waiver_request(tp)

    def test_waiver_request_requires_authorized_risk_owner(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver()
        with self.assertRaises(r.AEError):self.waiver_request(tp,risk_owner='other-manager')

    def test_waiver_request_persists_bounded_draft(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True);self.assertTrue((self.repo/req['waiver_ref']).exists());self.assertTrue(req['expected_note'].startswith('AE-WAIVE human_understanding STORY-101 '));self.assertEqual(64,len(req['waiver_digest']))

    def test_exact_risk_owner_note_marks_waived_not_satisfied(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True);out=self.apply_waiver_note(tp,req['expected_note']);self.assertEqual('ASSURANCE_WAIVED',out['result']);g=r.read_json(tp)['assurance']['pre_merge']['human_understanding'];self.assertEqual('WAIVED',g['status']);self.assertNotEqual('SATISFIED',g['status']);self.assertTrue(g['waiver_ref'].endswith('WV-101.json'));wr=r.read_json(self.repo/g['waiver_ref']);self.assertTrue(wr['approval_ref'].startswith('gitlab:mr:42:note:201:user:eng-manager'))

    def test_wrong_waiver_note_rejected(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True)
        with self.assertRaises(r.AEError):self.apply_waiver_note(tp,req['expected_note']+' tampered')

    def test_wrong_waiver_risk_owner_rejected(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True)
        with self.assertRaises(r.AEError):self.apply_waiver_note(tp,req['expected_note'],user='dev-a-user')

    def test_waiver_preview_does_not_mutate_task_or_draft(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True);before_task=tp.read_bytes();before_waiver=(self.repo/req['waiver_ref']).read_bytes();out=self.apply_waiver_note(tp,req['expected_note'],apply=False);self.assertEqual('PREVIEW_ONLY',out['mode']);self.assertEqual(before_task,tp.read_bytes());self.assertEqual(before_waiver,(self.repo/req['waiver_ref']).read_bytes())

    def test_waived_gate_remains_visible_as_exception_in_closure_report(self):
        tp,_=self.make_task(self.human_ob());self.enable_human_waiver();req=self.waiver_request(tp,apply=True);self.apply_waiver_note(tp,req['expected_note']);out=assurance.check_pre_merge(self.repo,tp);self.assertEqual('ASSURANCE_PRE_MERGE_PENDING',out['result']);self.assertEqual('WAIVED',out['blocking'][0]['status'])


if __name__=='__main__': unittest.main(verbosity=2)
