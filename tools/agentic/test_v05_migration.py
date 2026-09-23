#!/usr/bin/env python3
from __future__ import annotations
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))

import runtime as r
import forge
import contract
import pool
import merge
from test_v04 import RepoCase
from test_v05_config import v04_config


class LegacyRuntimeCompatibilityCase(RepoCase):
    def legacy_task(self, story='S1'):
        super().build(story)
        p=r.task_path(self.repo,'dev-a',story)
        task=r.read_json(p)
        task['schema_version']=2
        task.pop('contract',None);task.pop('assurance',None);task.pop('traceability',None)
        r.atomic_json(p,task)
        return p,r.read_json(p)

    def add_project_gate(self, gate='nfr_performance', stage='PRE_MERGE', mode='OBJECTIVE_TOOL'):
        self.cfg['assurance_policy']['project_required_gates']=[{'gate_id':gate,'stage':stage,'mode':mode}]
        self.write_cfg()

    def test_legacy_task_load_is_explicitly_recognized(self):
        p,task=self.legacy_task()
        _,loaded=r.load_task(self.repo,p)
        self.assertEqual(2,loaded['schema_version'])
        self.assertEqual('LEGACY_V04_CONTRACT',r.validate_legacy_task_compatibility(self.cfg,loaded)['contract_status'])

    def test_legacy_fast_path_can_agent_merge_under_v04_semantics(self):
        p,task=self.legacy_task()
        out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('INTEGRATED',out['result'])
        self.assertEqual('LEGACY_V04_COMPATIBLE',out['assurance']['result'])
        self.assertEqual(1,self.put_count)

    def test_project_additional_assurance_blocks_legacy_agent_merge(self):
        p,task=self.legacy_task();self.add_project_gate()
        with self.assertRaises(r.AEError):merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual(0,self.put_count)

    def test_tier_additional_assurance_blocks_legacy_agent_merge(self):
        p,task=self.legacy_task()
        self.cfg['assurance_policy']['tier_required_gates']['2']=[{'gate_id':'traceability_completion','stage':'POST_INTEGRATION','mode':'MECHANICAL'}]
        self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,task,self.cfg,True)

    def test_developer_review_does_not_bypass_legacy_compatibility_gate(self):
        p,task=self.legacy_task();self.add_project_gate();self.cfg['merge_mode']='DEVELOPER_REVIEW';self.write_cfg()
        with self.assertRaises(r.AEError):merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual(0,self.put_count)

    def test_legacy_merge_writes_factual_marker_not_fake_contract(self):
        p,task=self.legacy_task();out=merge.execute(self.repo,task,self.cfg,True)
        tr=out['traceability'];self.assertEqual('LEGACY_TRACEABILITY_WRITTEN',tr['result'])
        rec=r.read_json(self.repo/tr['record_ref'])
        self.assertEqual('LEGACY_V04_TRACEABILITY',rec['record_type'])
        self.assertEqual('LEGACY_V04_CONTRACT',rec['contract_status'])
        self.assertEqual('NO_V05_ASSURANCE_SYNTHESIZED',rec['assurance']['claim'])
        self.assertFalse(rec['build_complete_eligible'])

    def test_already_human_merged_legacy_story_records_new_policy_as_incomplete_not_pass(self):
        p,task=self.legacy_task()
        self.api(self.repo,self.cfg,f'{forge.GitLabForge(r)._prefix(self.cfg)}/merge_requests/{task["mr_iid"]}/merge',method='PUT',fields={
            'sha':task['candidate_sha'],'auto_merge':False,'should_remove_source_branch':False,'squash':False})
        self.add_project_gate()
        out=merge.execute(self.repo,task,self.cfg,True)
        self.assertEqual('INTEGRATED',out['result'])
        self.assertEqual('LEGACY_V04_ASSURANCE_INCOMPLETE',out['assurance']['result'])
        self.assertFalse(out['build_complete_eligible'])
        rec=r.read_json(self.repo/out['traceability']['record_ref'])
        self.assertEqual('ASSURANCE_INCOMPLETE_AFTER_INTEGRATION',rec['assurance']['claim'])
        self.assertEqual('nfr_performance',rec['assurance']['effective_additional_assurance'][0]['gate_id'])

    def test_legacy_fix_allowed_when_current_additional_assurance_empty(self):
        p,task=self.legacy_task();candidate=task['candidate_sha']
        a=pool.lease(self.repo,'dev-a','S1','lane-01','FIX',candidate,'legacy-fix',True)
        self.assertEqual('LANE_PREPARED',a['result'])

    def test_legacy_fix_blocked_when_project_adds_assurance(self):
        p,task=self.legacy_task();self.add_project_gate()
        with self.assertRaises(r.AEError):pool.lease(self.repo,'dev-a','S1','lane-01','FIX',task['candidate_sha'],'legacy-fix',True)


class LegacyTaskUpgradeCase(RepoCase):
    def legacy_task(self,story='S1'):
        super().build(story);p=r.task_path(self.repo,'dev-a',story);task=r.read_json(p)
        task['schema_version']=2;task.pop('contract',None);task.pop('assurance',None);task.pop('traceability',None);r.atomic_json(p,task)
        return p,r.read_json(p)

    def test_upgrade_preview_is_no_write(self):
        p,before=self.legacy_task();raw=p.read_bytes()
        out=contract.upgrade_legacy_task_v04(self.repo,p,apply=False)
        self.assertEqual('LEGACY_TASK_UPGRADE_PREVIEW',out['result'])
        self.assertEqual(raw,p.read_bytes())
        self.assertFalse((self.repo/out['backup_ref']).exists())

    def test_upgrade_apply_preserves_legacy_evidence_and_backs_up_schema2(self):
        p,before=self.legacy_task();review=copy.deepcopy(before['review']);checks=copy.deepcopy(before['merge_checks'])
        out=contract.upgrade_legacy_task_v04(self.repo,p,apply=True);after=r.read_json(p)
        self.assertEqual('LEGACY_TASK_UPGRADED',out['result']);self.assertEqual(3,after['schema_version'])
        self.assertEqual(review,after['review']);self.assertEqual(checks,after['merge_checks'])
        backup=r.read_json(self.repo/out['backup_ref']);self.assertEqual(2,backup['schema_version']);self.assertEqual(review,backup['review'])
        r.validate_task_schema3(after)

    def test_upgrade_with_new_assurance_starts_pending_not_pass(self):
        p,before=self.legacy_task();cp=contract.registered_contract_path(self.repo,'dev-a','S1');data=r.read_json(cp)
        data['story_required_assurance']=[{'gate_id':'nfr_performance','stage':'PRE_MERGE','mode':'OBJECTIVE_TOOL','evidence_plan_ref':'backlog/features/FIXTURE.md#nfr'}]
        r.atomic_json(cp,data)
        out=contract.upgrade_legacy_task_v04(self.repo,p,apply=True);after=r.read_json(p)
        self.assertEqual(['nfr_performance'],out['pre_merge_assurance'])
        self.assertEqual('PENDING',after['assurance']['pre_merge']['nfr_performance']['status'])
        self.assertEqual(after['candidate_sha'],after['assurance']['pre_merge']['nfr_performance']['binding']['candidate_sha'])

    def test_upgrade_blocks_while_story_has_active_lease(self):
        p,task=self.legacy_task();a=pool.lease(self.repo,'dev-a','S1','lane-01','FIX',task['candidate_sha'],'legacy-active',True)
        with self.assertRaises(r.AEError):contract.upgrade_legacy_task_v04(self.repo,p,apply=True)

    def test_upgrade_blocks_candidate_risk_tier_change(self):
        p,task=self.legacy_task();cp=contract.registered_contract_path(self.repo,'dev-a','S1');data=r.read_json(cp);data['risk_tier']=3;r.atomic_json(cp,data)
        with self.assertRaises(r.AEError):contract.upgrade_legacy_task_v04(self.repo,p,apply=False)

    def test_upgrade_cannot_broaden_legacy_developer_review_override(self):
        p,task=self.legacy_task();task['merge_mode_override']='DEVELOPER_REVIEW';r.atomic_json(p,task)
        cp=contract.registered_contract_path(self.repo,'dev-a','S1');data=r.read_json(cp);data['merge_mode_override']=None;r.atomic_json(cp,data)
        with self.assertRaises(r.AEError):contract.upgrade_legacy_task_v04(self.repo,p,apply=False)


class ProjectConfigMigrationApplyCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.repo=Path(self.tmp.name)/'repo';self.repo.mkdir()
        subprocess.run(['git','-C',str(self.repo),'init','-b','main'],check=True,capture_output=True,text=True)
        (self.repo/'.agentic').mkdir();self.path=self.repo/'.agentic/project.json';self.path.write_text(json.dumps(v04_config()),encoding='utf-8')
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Fixture'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','fixture@example.invalid'],check=True)
        subprocess.run(['git','-C',str(self.repo),'add','.agentic/project.json'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-m','v04 config'],check=True,capture_output=True,text=True)
    def tearDown(self):self.tmp.cleanup()

    def test_project_migration_preview_does_not_write(self):
        raw=self.path.read_bytes();out=contract.migrate_project_config_file_v04(self.path,apply=False)
        self.assertEqual('PROJECT_MIGRATION_PREVIEW',out['result']);self.assertEqual(raw,self.path.read_bytes())
        self.assertFalse((self.repo/out['backup_ref']).exists())

    def test_project_migration_apply_preserves_standing_authority_but_not_assurance_approval(self):
        old=r.read_json(self.path);out=contract.migrate_project_config_file_v04(self.path,apply=True);new=r.read_json(self.path)
        self.assertEqual(5,new['schema_version']);self.assertEqual('0.7',new['kit_version'])
        self.assertEqual(old['merge_policy'],new['merge_policy']);self.assertEqual(old['cleanup'],new['cleanup'])
        self.assertEqual(old['remote_actions_ready'],new['remote_actions_ready'])
        self.assertFalse(new['assurance_policy']['approved'])
        self.assertEqual({'provider':'gitlab','host':old['gitlab_host'],'repo':old['gitlab_repo']},new['forge'])
        backup=r.read_json(self.repo/out['backup_ref']);self.assertEqual(2,backup['schema_version'])

    def test_project_migration_apply_requires_clean_repository(self):
        (self.repo/'dirty.txt').write_text('work')
        with self.assertRaises(r.AEError):contract.migrate_project_config_file_v04(self.path,apply=True)
        self.assertEqual(2,r.read_json(self.path)['schema_version'])

    def test_project_migration_apply_is_not_implicit_or_repeatable_on_schema4(self):
        contract.migrate_project_config_file_v04(self.path,apply=True)
        with self.assertRaises(r.AEError):contract.migrate_project_config_file_v04(self.path,apply=True)


if __name__=='__main__':unittest.main(verbosity=2)
