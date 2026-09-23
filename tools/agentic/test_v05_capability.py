#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
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
import merge
from test_v04 import RepoCase


class CapabilityAssuranceCase(RepoCase):
    def prepare_contract(self, story, obligations, *, owner=True, revision='FIXTURE-1'):
        p=self.ensure_contract(story);data=r.read_json(p)
        data['publication']['canonical_backlog']['revision']=revision
        data['story_required_assurance']=copy.deepcopy(obligations)
        data['engineering_owner']={'owner_ref':'team:dev-a','attestation_provider':'gitlab','attestation_identity':'dev-a-user'} if owner else None
        if owner:data['engineering_impact']['human_ownership']='MATERIAL'
        for ob in obligations:
            if ob['gate_id']=='operability_recovery':data['engineering_impact']['operability_recovery']='MATERIAL'
            if ob['gate_id']=='nfr_performance':data['engineering_impact']['scalability_nfr']='MATERIAL'
            if ob['gate_id']=='traceability_completion':data['engineering_impact']['traceability_audit']='MATERIAL'
        r.atomic_json(p,data);return p

    def human_post(self):
        return [{'gate_id':'human_understanding','stage':'POST_INTEGRATION','mode':'ASYNC_CAPABILITY','evidence_plan_ref':'backlog/features/FIXTURE.md#owner'}]

    def trace_post(self):
        return [{'gate_id':'traceability_completion','stage':'POST_INTEGRATION','mode':'MECHANICAL','evidence_plan_ref':'backlog/features/FIXTURE.md#trace'}]

    def nfr_post(self):
        return [{'gate_id':'nfr_performance','stage':'POST_INTEGRATION','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FIXTURE.md#nfr'}]

    def build_on(self,story,base_sha,filename):
        a=pool.lease(self.repo,'dev-a',story,'lane-01','BUILD',base_sha,'BUILD-'+story,True);w=Path(a['workspace'])
        p=w/'src'/filename;p.write_text(story+'\n');self.g('add',f'src/{filename}',repo=w);self.g('commit','-m',story,repo=w);head=self.g('rev-parse','HEAD',repo=w)
        self.g('push','origin',head+':refs/heads/work/dev-a/'+story);self.release(a,head)
        rev=pool.lease(self.repo,'dev-a',story,'lane-02','REVIEW',head,'review-'+story,True);self.release(rev,head)
        tp=r.task_path(self.repo,'dev-a',story);t=r.read_json(tp);t.update(mr_iid=len(self.mrs)+1)
        t['review']={'verdict':'ACCEPT','candidate_sha':head,'report_ref':'docs/review/'+story,'invocation_id':'review-'+story,'lane_id':'lane-02'}
        t['merge_checks']={'candidate_sha':head,'target_sha':base_sha,'checked_at':r.now(),'checks':{k:{'status':'PASS','candidate_sha':head,'evidence_ref':'fixtures/'+k} for k in self.cfg['merge_policy']['mandatory_checks']}}
        for f in ('scope_valid','dependencies_ready','no_unresolved_blockers','remote_effects_safe'):t['merge_checks'].update({f:True,f+'_ref':'fixture/'+f})
        t['cleanup_checks']={'checked_at':r.now(),'retention_required':False}
        for f in ('dependencies_clear','processes_stopped','records_published'):t['cleanup_checks'].update({f:True,f+'_ref':'fixture/'+f})
        r.atomic_json(tp,t)
        self.mrs[t['mr_iid']]={'iid':t['mr_iid'],'source_project_id':1,'target_project_id':1,'source_branch':t['branch'],'target_branch':'main','diff_refs':{'head_sha':head,'base_sha':base_sha},'state':'opened','draft':False,'has_conflicts':False,'detailed_merge_status':'mergeable','head_pipeline':{'sha':head,'status':'success'}}
        return t

    def integrate(self,story='S1',obligations=None,*,owner=True,base=None,filename=None):
        self.prepare_contract(story,obligations or [],owner=owner)
        task=self.build_on(story,base or self.base,filename or (story+'.txt'))
        return task,merge.execute(self.repo,task,self.cfg,True)

    def cap_path(self):return self.repo/'.agentic/local/capabilities/FIXTURE.json'
    def cap_record(self):return self.repo/'docs/agentic/records/team/FIXTURE/assurance.json'

    def note_for(self,expected,user='dev-a-user'):
        # Installed on GitLabForge.api, over the RepoCase stub: v0.6 moved provider REST
        # behind the adapter, so runtime no longer has an `api` attribute to patch.
        def api(repo,cfg,endpoint,method='GET',fields=None):
            if '/notes/' in endpoint:return {'system':False,'author':{'username':user},'body':expected,'created_at':'2026-09-14T01:00:00+00:00'}
            return self.api(repo,cfg,endpoint,method,fields)
        return api

    def test_fast_path_creates_no_capability_registry(self):
        _task,out=self.integrate('S1',[])
        self.assertIsNone(out['capability']);self.assertFalse(self.cap_path().exists())

    def test_post_integration_gate_creates_local_capability_state(self):
        _task,out=self.integrate('S1',self.human_post())
        self.assertEqual('CAPABILITY_ASSURANCE_CREATED',out['capability']['result']);state=r.read_json(self.cap_path());assurance.validate_capability_state(state)
        self.assertEqual('PENDING',state['gates']['human_understanding']['status']);self.assertFalse(state['build_complete']);self.assertEqual(1,len(state['story_inputs']))

    def test_capability_human_request_is_owner_scoped_and_server_satisfiable(self):
        self.integrate('S1',self.human_post());req=assurance.capability_attestation_request(self.repo,'FIXTURE','human_understanding')
        self.assertEqual(['dev-a-user'],req['allowed_forge_users']);self.assertTrue(req['expected_note'].startswith('AE-ASSURE human_understanding FIXTURE '))
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'])):
            out=assurance.capability_satisfy_human(self.repo,'FIXTURE','human_understanding',301,[],True)
        self.assertEqual('SATISFIED',out['status']);self.assertEqual('SATISFIED',r.read_json(self.cap_path())['gates']['human_understanding']['status'])

    def test_capability_finalize_reverifies_human_attestation_on_server(self):
        self.integrate('S1',self.human_post());req=assurance.capability_attestation_request(self.repo,'FIXTURE','human_understanding')
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'])):assurance.capability_satisfy_human(self.repo,'FIXTURE','human_understanding',305,[],True)
        assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:FIXTURE','product-owner:fixture',True)
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note']+' changed')):
            with self.assertRaises(r.AEError):assurance.capability_finalize(self.repo,'FIXTURE',True)

    def test_capability_tool_gate_can_be_satisfied(self):
        self.integrate('S1',self.trace_post());out=assurance.capability_satisfy_tool(self.repo,'FIXTURE','traceability_completion','control-plane:traceability',['traceability:story-index-verified'],True)
        self.assertEqual('CAPABILITY_ASSURANCE_SATISFIED',out['result']);self.assertEqual('SATISFIED',r.read_json(self.cap_path())['gates']['traceability_completion']['status'])

    def test_build_complete_requires_product_acceptance_and_engineering_gate(self):
        self.integrate('S1',self.trace_post())
        with self.assertRaises(r.AEError):assurance.capability_finalize(self.repo,'FIXTURE',True)
        assurance.capability_satisfy_tool(self.repo,'FIXTURE','traceability_completion','control-plane:traceability',['traceability:verified'],True)
        with self.assertRaises(r.AEError):assurance.capability_finalize(self.repo,'FIXTURE',True)
        assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:FIXTURE:target','product-owner:fixture',True)
        out=assurance.capability_finalize(self.repo,'FIXTURE',True);self.assertEqual('BUILD_COMPLETE',out['result']);self.assertTrue(self.cap_record().is_file())

    def test_finalize_updates_story_task_and_traceability_pointer(self):
        task,_=self.integrate('S1',self.trace_post());assurance.capability_satisfy_tool(self.repo,'FIXTURE','traceability_completion','control-plane:traceability',['traceability:verified'],True);assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:FIXTURE','product-owner:fixture',True);out=assurance.capability_finalize(self.repo,'FIXTURE',True)
        rel=out['record_ref'];persisted=r.read_json(r.task_path(self.repo,'dev-a','S1'));self.assertEqual(rel,persisted['traceability']['capability_record_ref'])
        tr=r.read_json(self.repo/persisted['traceability']['record_ref']);self.assertEqual(rel,tr['downstream']['capability_record_ref'])

    def test_two_stories_aggregate_one_human_understanding_gate(self):
        self.integrate('S1',self.human_post(),filename='one.txt');latest=r.fetch_target(self.repo,self.cfg)
        self.integrate('S2',self.human_post(),base=latest,filename='two.txt');state=r.read_json(self.cap_path());self.assertEqual(2,len(state['story_inputs']));self.assertEqual(['human_understanding'],list(state['gates']))
        req=assurance.capability_attestation_request(self.repo,'FIXTURE','human_understanding');self.assertEqual(2,req['representative_mr_iid'])
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'])):assurance.capability_satisfy_human(self.repo,'FIXTURE','human_understanding',302,[],True)
        self.assertEqual('SATISFIED',r.read_json(self.cap_path())['gates']['human_understanding']['status'])

    def test_new_story_reopens_previously_satisfied_capability_gate_and_product_acceptance(self):
        self.integrate('S1',self.human_post(),filename='one.txt');req=assurance.capability_attestation_request(self.repo,'FIXTURE','human_understanding')
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'])):assurance.capability_satisfy_human(self.repo,'FIXTURE','human_understanding',303,[],True)
        assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:v1','product-owner:fixture',True)
        latest=r.fetch_target(self.repo,self.cfg);self.integrate('S2',self.human_post(),base=latest,filename='two.txt');state=r.read_json(self.cap_path())
        self.assertEqual('NEEDS_REVALIDATION',state['gates']['human_understanding']['status']);self.assertEqual('PENDING',state['product_acceptance']['status']);self.assertFalse(state['build_complete'])

    def test_capability_revision_mismatch_is_not_silently_aggregated(self):
        self.integrate('S1',self.human_post(),filename='one.txt');latest=r.fetch_target(self.repo,self.cfg);self.prepare_contract('S2',self.human_post(),revision='FIXTURE-2');task=self.build_on('S2',latest,'two.txt');out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('CAPABILITY_ASSURANCE_PENDING',out['capability']['result']);self.assertIn('different canonical backlog/revision',out['capability']['error'])

    def test_capability_waiver_is_human_only_and_remains_waived(self):
        self.cfg['assurance_policy']['waiver_policy']['gates']={'traceability_completion':{'waivable':True,'risk_owners':['eng-manager']}};self.write_cfg();self.integrate('S1',self.trace_post())
        req=assurance.capability_create_waiver_request(self.repo,'FIXTURE','traceability_completion','CWV-1','eng-manager','temporary exception','combined trace proof pending','audit gap accepted',['manual follow-up'],None,'before release',True)
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'],user='eng-manager')):
            out=assurance.capability_apply_waiver(self.repo,'FIXTURE','traceability_completion','CWV-1',401,True)
        self.assertEqual('WAIVED',out['status']);assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:waived','product-owner:fixture',True)
        with patch.object(forge.GitLabForge,'api',side_effect=self.note_for(req['expected_note'],user='eng-manager')):
            final=assurance.capability_finalize(self.repo,'FIXTURE',True)
        self.assertEqual('BUILD_COMPLETE',final['result']);record=r.read_json(self.cap_record());self.assertEqual('WAIVED',record['gates']['traceability_completion']['status']);self.assertTrue(record['gates']['traceability_completion']['waiver_ref'].startswith('waiver:'))

    def test_capability_finalize_is_previewable_without_mutation(self):
        self.integrate('S1',self.trace_post());assurance.capability_satisfy_tool(self.repo,'FIXTURE','traceability_completion','control-plane:traceability',['traceability:verified'],True);assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:FIXTURE','product-owner:fixture',True)
        before=self.cap_path().read_bytes();out=assurance.capability_finalize(self.repo,'FIXTURE',False);self.assertEqual('CAPABILITY_BUILD_COMPLETE_READY',out['result']);self.assertEqual(before,self.cap_path().read_bytes());self.assertFalse(self.cap_record().exists())

    def test_release_pointer_is_carried_when_capability_exists(self):
        obs=self.trace_post()+[{'gate_id':'project_defined:release_review','stage':'RELEASE_POINTER','mode':'SIGNOFF','evidence_plan_ref':'backlog/features/FIXTURE.md#release'}]
        self.cfg['assurance_policy']['custom_gates']={'project_defined:release_review':{'policy_ref':'policy/release','allowed_stages':['RELEASE_POINTER'],'allowed_modes':['SIGNOFF'],'attestor_class':'AUTHORIZED_HUMAN','binding_class':'INTEGRATED_CAPABILITY','minimum_evidence':1,'freshness_rule':'MANUAL','failure_behavior':'BLOCK_STAGE'}};self.cfg['assurance_policy']['human_attestors']={'project_defined:release_review':['release-owner']};self.write_cfg()
        self.integrate('S1',obs);state=r.read_json(self.cap_path());self.assertIn('backlog/features/FIXTURE.md#release',state['release_pointer_refs'])

    def test_build_complete_record_does_not_claim_deployment_or_release(self):
        self.integrate('S1',self.trace_post());assurance.capability_satisfy_tool(self.repo,'FIXTURE','traceability_completion','control-plane:traceability',['traceability:verified'],True);assurance.capability_product_accept(self.repo,'FIXTURE','product-acceptance:FIXTURE','product-owner:fixture',True);out=assurance.capability_finalize(self.repo,'FIXTURE',True)
        self.assertIn('publication_required',out);self.assertNotIn('deployed',out);self.assertNotIn('released',out)


if __name__=='__main__':unittest.main(verbosity=2)
