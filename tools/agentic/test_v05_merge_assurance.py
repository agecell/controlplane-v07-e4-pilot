#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))

import runtime as r
import assurance
import merge
from test_v04 import RepoCase


class MergeAssuranceEnforcementCase(RepoCase):
    def setUp(self):
        super().setUp()
        self.story_obligations={}
        self.story_owner={}
        self.notes={}

    def api(self,repo,cfg,endpoint,method='GET',fields=None):
        if '/notes/' in endpoint:
            note_id=int(endpoint.rsplit('/notes/',1)[1])
            if note_id in self.notes:return copy.deepcopy(self.notes[note_id])
        return super().api(repo,cfg,endpoint,method,fields)

    def ensure_contract(self,story):
        path=super().ensure_contract(story)
        obligations=self.story_obligations.get(story)
        if obligations is not None:
            data=r.read_json(path)
            data['story_required_assurance']=copy.deepcopy(obligations)
            if obligations:
                data['engineering_impact']['security_privacy_data']='MATERIAL'
                data['engineering_owner']=self.story_owner.get(story,{
                    'owner_ref':'team:dev-a','attestation_provider':'gitlab','attestation_identity':'dev-a-user'})
                data['risk_tier']=3
            r.atomic_json(path,data)
        return path

    @staticmethod
    def nfr_ob():
        return [{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FIXTURE.md#nfr'}]

    @staticmethod
    def human_ob():
        return [{'gate_id':'human_understanding','stage':'PRE_MERGE','mode':'STORY_EXPLAIN_BACK','evidence_plan_ref':'backlog/features/FIXTURE.md#human'}]

    @staticmethod
    def security_ob():
        return [{'gate_id':'security_review','stage':'PRE_MERGE','mode':'BOUNDED_MATERIAL','evidence_plan_ref':'backlog/features/FIXTURE.md#security'}]

    def task_path(self,story='S1'):
        return r.task_path(self.repo,'dev-a',story)

    def build_with(self,obligations,story='S1'):
        self.story_obligations[story]=obligations
        self.cfg['assurance_policy']['human_attestors']={
            'security_review':{'BOUNDED_MATERIAL':['security-champion'],'CRITICAL_OR_MAJOR':['security-lead']},
            'architecture_review':{'CONFORMANCE':['dev-a-user'],'DECISION':['architect-a']},
        }
        self.write_cfg()
        super().build(story)
        tp=self.task_path(story)
        task=r.read_json(tp);contract_data=r.read_json(self.repo/task['contract']['contract_ref'])
        task['risk_tier']=contract_data['risk_tier'];r.atomic_json(tp,task)
        return tp

    def satisfy_human(self,tp,gate_id,user,note_id=91,refs=None):
        req=assurance.attestation_request(self.repo,tp,gate_id)
        self.notes[note_id]={'system':False,'author':{'username':user},'body':req['expected_note'],'created_at':'2026-09-13T20:00:00+00:00'}
        return assurance.satisfy_human(self.repo,tp,gate_id,note_id,refs or [],True)

    def test_fast_path_empty_registry_agent_merge_still_executes(self):
        tp=self.build_with([]);task=r.read_json(tp)
        self.assertEqual({},task['assurance']['pre_merge'])
        out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual(1,self.put_count)

    def test_pending_gate_blocks_agent_merge(self):
        tp=self.build_with(self.nfr_ob());task=r.read_json(tp)
        with self.assertRaises(r.AEError):merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual(0,self.put_count)

    def test_satisfied_tool_gate_allows_agent_merge(self):
        tp=self.build_with(self.nfr_ob());assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:perf',['evidence/perf.json'],True)
        out=merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual(1,self.put_count)

    def test_developer_review_carries_pending_assurance_without_merging(self):
        tp=self.build_with(self.nfr_ob());self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        out=merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',out['result']);self.assertEqual(0,self.put_count)
        self.assertEqual([{'gate_id':'nfr_performance','status':'PENDING','mode':'OBJECTIVE_TOOL'}],out['pending_assurance'])

    def test_needs_revalidation_blocks_agent_merge(self):
        tp=self.build_with(self.nfr_ob());assurance.satisfy_tool(self.repo,tp,'nfr_performance','tool:perf',['evidence/perf.json'],True)
        task=r.read_json(tp);g=task['assurance']['pre_merge']['nfr_performance'];g['status']='NEEDS_REVALIDATION';g['revalidation'].update(disposition='UNKNOWN',from_binding=task['candidate_sha'],to_binding=task['candidate_sha'],assessor_class='TOOL',assessor_ref='revalidation:fixture')
        r.atomic_json(tp,task)
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def test_policy_snapshot_drift_blocks_both_modes(self):
        tp=self.build_with([]);self.cfg['assurance_policy']['approval_ref']='setup/assurance-v2';self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def test_policy_content_drift_same_approval_ref_is_detected(self):
        tp=self.build_with([])
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL'}];self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def test_normalized_contract_drift_blocks_merge(self):
        tp=self.build_with([]);task=r.read_json(tp);cp=self.repo/task['contract']['contract_ref'];data=r.read_json(cp);data['ac_ids'].append('AC-DRIFT');r.atomic_json(cp,data)
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def test_human_attestation_is_reverified_on_agent_merge(self):
        tp=self.build_with(self.security_ob());self.satisfy_human(tp,'security_review','security-champion',refs=['evidence/security.json'])
        out=merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual('INTEGRATED',out['result'])

    def test_changed_server_human_note_blocks_merge(self):
        tp=self.build_with(self.security_ob());self.satisfy_human(tp,'security_review','security-champion',refs=['evidence/security.json'])
        self.notes[91]['body']+=' tampered'
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual(0,self.put_count)

    def test_attestor_removed_from_current_allowlist_blocks_merge(self):
        tp=self.build_with(self.security_ob());self.satisfy_human(tp,'security_review','security-champion',refs=['evidence/security.json'])
        # Keep the same policy approval_ref deliberately: merge still re-resolves current allowlist.
        self.cfg['assurance_policy']['human_attestors']['security_review']['BOUNDED_MATERIAL']=['security-other'];self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def _waive_human(self,tp,expiry='2030-01-01T00:00:00+00:00'):
        self.cfg['assurance_policy']['waiver_policy']['gates']={'human_understanding':{'waivable':True,'risk_owners':['eng-manager']}};self.write_cfg()
        req=assurance.create_waiver_request(self.repo,tp,'human_understanding','WV-S1','eng-manager','temporary exception','walkthrough pending','bounded continuity risk',['follow-up walkthrough'],expiry,'before release',True)
        self.notes[201]={'system':False,'author':{'username':'eng-manager'},'body':req['expected_note'],'created_at':'2026-09-13T20:00:00+00:00'}
        assurance.apply_waiver(self.repo,tp,'human_understanding','WV-S1',201,True)

    def test_valid_waiver_closes_agent_merge_readiness_but_remains_waived(self):
        tp=self.build_with(self.human_ob());self._waive_human(tp)
        out=merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual('WAIVED',out['assurance']['waived_exceptions'][0]['status'])

    def test_expired_waiver_blocks_agent_merge(self):
        tp=self.build_with(self.human_ob());self._waive_human(tp,expiry='2020-01-01T00:00:00+00:00')
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual(0,self.put_count)

    def test_waiver_record_tamper_after_approval_blocks_merge(self):
        tp=self.build_with(self.human_ob());self._waive_human(tp)
        task=r.read_json(tp);wp=self.repo/task['assurance']['pre_merge']['human_understanding']['waiver_ref'];wr=r.read_json(wp);wr['residual_risk']='tampered after approval';r.atomic_json(wp,wr)
        with self.assertRaises(r.AEError):merge.execute(self.repo,r.read_json(tp),self.cfg,True)

    def test_developer_review_reports_waived_as_exception_not_pass(self):
        tp=self.build_with(self.human_ob());self._waive_human(tp);self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        out=merge.execute(self.repo,r.read_json(tp),self.cfg,True)
        self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',out['result']);self.assertEqual([],out['pending_assurance'])
        self.assertEqual('WAIVED',out['assurance']['waived_exceptions'][0]['status'])


if __name__=='__main__':unittest.main(verbosity=2)
