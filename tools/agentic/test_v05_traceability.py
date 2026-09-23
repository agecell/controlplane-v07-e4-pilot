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
import forge
import merge
from test_v04 import RepoCase


class TraceabilityIndexCase(RepoCase):
    def record_path(self, story='S1'):
        return self.repo / f'docs/agentic/records/dev-a/{story}/traceability.json'

    def task_path(self, story='S1'):
        return r.task_path(self.repo,'dev-a',story)

    def external_human_merge(self, task):
        endpoint=f"{forge.GitLabForge(r)._prefix(self.cfg)}/merge_requests/{task['mr_iid']}/merge"
        return self.api(self.repo,self.cfg,endpoint,method='PUT',fields={
            'sha':task['candidate_sha'],'auto_merge':False,'should_remove_source_branch':False,'squash':False})

    def test_agent_merge_writes_traceability_index_and_task_pointer(self):
        task=self.build();out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('INTEGRATED',out['result'])
        self.assertEqual('TRACEABILITY_WRITTEN',out['traceability']['result'])
        path=self.record_path();self.assertTrue(path.is_file())
        record=r.read_json(path);self.assertEqual(1,record['schema_version']);self.assertEqual('STORY_TRACEABILITY',record['record_type'])
        self.assertEqual(['REQ-FIXTURE'],record['traceability_root']);self.assertEqual(['AC-01'],record['story']['ac_ids'])
        self.assertEqual(task['candidate_sha'],record['candidate_sha']);self.assertEqual('ACCEPT',record['technical_review']['verdict'])
        self.assertEqual(task['mr_iid'],record['mr']['iid']);self.assertEqual('group/product',record['mr']['project'])
        self.assertEqual(out['proof']['result_sha'],record['merge']['result_sha'])
        persisted=r.read_json(self.task_path());self.assertEqual('docs/agentic/records/dev-a/S1/traceability.json',persisted['traceability']['record_ref'])

    def test_record_is_index_and_does_not_publish_local_contract_path(self):
        task=self.build();merge.execute(self.repo,task,self.cfg,True);record=r.read_json(self.record_path())
        raw=json.dumps(record,sort_keys=True)
        self.assertNotIn('.agentic/local/contracts',raw)
        self.assertEqual({'digest'},set(record['authority']['contract']))
        self.assertIn('docs/review',raw)

    def test_fast_path_assurance_index_is_empty_and_validated(self):
        task=self.build();out=merge.execute(self.repo,task,self.cfg,True);record=r.read_json(self.record_path())
        self.assertEqual([],record['assurance']['pre_merge'])
        self.assertEqual('ASSURANCE_PRE_MERGE_CLOSED',record['assurance']['validation_result'])
        r.validate_traceability_index(record)

    def test_downstream_obligation_is_indexed_without_claiming_complete(self):
        cp=self.ensure_contract('S1');data=r.read_json(cp)
        data['story_required_assurance']=[{'gate_id':'traceability_completion','stage':'POST_INTEGRATION','mode':'MECHANICAL','evidence_plan_ref':'backlog/features/FIXTURE.md#trace'}]
        r.atomic_json(cp,data)
        task=self.build();merge.execute(self.repo,task,self.cfg,True);record=r.read_json(self.record_path())
        self.assertEqual('traceability_completion',record['assurance']['downstream_obligations'][0]['gate_id'])
        self.assertFalse(record['build_complete_eligible'])
        self.assertIsNone(record['downstream']['capability_record_ref'])

    def test_repeat_merge_is_idempotent_and_does_not_duplicate_merge(self):
        task=self.build();merge.execute(self.repo,task,self.cfg,True);before=self.record_path().read_bytes();count=self.put_count
        out=merge.execute(self.repo,task,self.cfg,True);after=self.record_path().read_bytes()
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual('TRACEABILITY_CURRENT',out['traceability']['result'])
        self.assertEqual(count,self.put_count);self.assertEqual(before,after)

    def test_preview_after_external_merge_does_not_write_index(self):
        task=self.build();self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        ready=merge.execute(self.repo,task,self.cfg,True);self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',ready['result'])
        self.external_human_merge(task)
        out=merge.execute(self.repo,task,self.cfg,False)
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual('TRACEABILITY_PREVIEW',out['traceability']['result'])
        self.assertFalse(self.record_path().exists())

    def test_external_human_merge_apply_writes_factual_index_with_pending_assurance(self):
        cp=self.ensure_contract('S1');data=r.read_json(cp)
        data['story_required_assurance']=[{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FIXTURE.md#nfr'}]
        r.atomic_json(cp,data)
        task=self.build();self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        ready=merge.execute(self.repo,task,self.cfg,True);self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',ready['result'])
        self.external_human_merge(task)
        out=merge.execute(self.repo,task,self.cfg,True);record=r.read_json(self.record_path())
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual('ASSURANCE_PRE_MERGE_PENDING',record['assurance']['validation_result'])
        self.assertEqual('PENDING',record['assurance']['pre_merge'][0]['status']);self.assertFalse(record['build_complete_eligible'])

    def test_traceability_record_immutable_identity_mismatch_is_not_overwritten(self):
        task=self.build();merge.execute(self.repo,task,self.cfg,True);path=self.record_path();record=r.read_json(path)
        record['authority']['contract']['digest']='0'*64;r.atomic_json(path,record)
        out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('TRACEABILITY_PENDING',out['traceability']['result'])
        self.assertIn('different immutable',out['traceability']['error'])
        self.assertEqual('0'*64,r.read_json(path)['authority']['contract']['digest'])

    def test_task_traceability_pointer_cannot_be_redirected(self):
        task=self.build();tp=self.task_path();data=r.read_json(tp);data['traceability']['record_ref']='docs/agentic/records/dev-a/OTHER/traceability.json';r.atomic_json(tp,data)
        with self.assertRaises(r.AEError):r.load_task(self.repo,tp)

    def test_traceability_record_rejects_absolute_or_untrusted_core_refs(self):
        task=self.build();out=merge.execute(self.repo,task,self.cfg,True);record=copy.deepcopy(out['traceability']['record'])
        record['authority']['canonical_backlog']['path']='/tmp/backlog.md'
        with self.assertRaises(r.AEError):r.validate_traceability_index(record)

    def test_recovery_apply_writes_traceability_after_verified_merge(self):
        task=self.build()
        # Simulate a merge request that reached the server but local request handling became uncertain:
        # create the helper's lock/intent, merge externally, then recover.
        result=merge.preflight(self.repo,self.cfg,task,for_execution=True)
        intent=merge.take_remote_lock(self.repo,self.cfg,task,result['target_sha'])
        self.external_human_merge(task)
        out=merge.execute(self.repo,task,self.cfg,True,recover=True)
        self.assertEqual('INTEGRATED',out['result']);self.assertEqual('TRACEABILITY_WRITTEN',out['traceability']['result']);self.assertTrue(self.record_path().exists())

    def test_traceability_output_requires_publication_but_does_not_commit_automatically(self):
        task=self.build();out=merge.execute(self.repo,task,self.cfg,True)
        self.assertTrue(out['traceability']['publication_required'])
        status=self.g('status','--porcelain=v1','--','docs/agentic/records/dev-a/S1/traceability.json')
        self.assertTrue(status.startswith('??'))


if __name__=='__main__':unittest.main(verbosity=2)
