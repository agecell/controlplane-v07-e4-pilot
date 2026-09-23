"""v0.4 regression tests. Real temporary Git/worktrees/bare remotes; forge REST simulated.

The stub is installed on GitLabForge.api rather than on runtime, because v0.6 moved
provider REST behind the adapter. It keeps speaking GitLab shapes on purpose: these are
v0.4 regression tests, and the point is that the pre-existing GitLab behaviour survived
the rewire unchanged. Provider-neutral coverage lives in test_v06_forge.py.
"""
from __future__ import annotations
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parent))
import runtime as r
import forge
import pool
import merge
import cleanup
import guard
import ae
import contract

class RepoCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.top=Path(self.temp.name);self.repo=self.top/'repo';self.repo.mkdir()
        self.g('init','-b','main');self.g('config','user.name','Fixture');self.g('config','user.email','fixture@example.invalid')
        (self.repo/'.gitignore').write_text('.agentic/local/\n.claude/worktrees/\nnode_modules/\n.env\ncache/\n')
        (self.repo/'src').mkdir();(self.repo/'src/a.txt').write_text('base\n')
        p=self.repo/'docs/agentic/AGENT_RULES.md';p.parent.mkdir(parents=True);p.write_text('fixture rules')
        (self.repo/'backlog/features').mkdir(parents=True);(self.repo/'backlog/stories').mkdir(parents=True)
        (self.repo/'backlog/features/FIXTURE.md').write_text('# Fixture feature\n\nAC-01 fixture.\n')
        (self.repo/'backlog/stories/FIXTURE.md').write_text('# Fixture Story authority\n')
        template=Path(__file__).resolve().parents[2]/'.agentic/project.json';self.cfg=json.loads(template.read_text())
        self.cfg.update(project_name='Fixture',target_branch='main',forge={'provider':'gitlab','host':'gitlab.example.invalid','repo':'group/product'},configuration_approved=True,remote_actions_ready=True,remote_actions_review_ref='setup/remote',team_coordination_ref='team/board',merge_method='merge_commit',commands={'test':'fixture'},ci_policy='source SHA')
        self.cfg['pool']['size']=3
        self.cfg['merge_policy'].update(approved=True,approval_ref='setup/merge')
        self.cfg['cleanup'].update(approved=True,approval_ref='setup/cleanup')
        self.cfg['assurance_policy'].update(approved=True,approval_ref='setup/assurance')
        self.write_cfg();self.g('add','.');self.g('commit','-m','base');self.base=self.g('rev-parse','HEAD')
        self.remote=self.top/'remote.git';r.run(['git','init','--bare',str(self.remote)])
        self.g('remote','add','origin',str(self.remote));self.g('push','origin','main')
        self.g('switch','-c','ae/records/dev-a/sprint-01')
        pool.init(self.repo,'dev-a',self.base,True)
        self.mrs={};self.calls=[];self.put_count=0
        # Which account names the fake forge knows. None means "every name exists",
        # which is what these fixtures assumed before v0.7 asked the question at all.
        # A test that needs an unresolvable identity sets this to an explicit set.
        self.forge_users=None
        self.patches=[patch.object(r,'verify_remote',lambda *a:None),patch.object(forge.GitLabForge,'api',self.api)]
        for x in self.patches:x.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def write_cfg(self):
        p=self.repo/'.agentic/project.json';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(self.cfg))
    def g(self,*args,repo=None):return r.git(repo or self.repo,*args)[1]
    def ensure_contract(self,story):
        path=contract.registered_contract_path(self.repo,'dev-a',story)
        if path.exists():return path
        data={
          'contract_schema_version':2,'evidence_expectations':[],'story_id':story,'assigned_developer':'dev-a','sprint':'sprint-01','risk_tier':2,
          'publication':{
            'canonical_backlog':{'path':'backlog/features/FIXTURE.md','revision':'FIXTURE-1','publication_sha':self.base,'blob_sha':self.g('rev-parse',self.base+':backlog/features/FIXTURE.md')},
            'story_card':{'path':'backlog/stories/FIXTURE.md','publication_sha':self.base,'blob_sha':self.g('rev-parse',self.base+':backlog/stories/FIXTURE.md')}},
          'traceability_root':['REQ-FIXTURE'],'ac_ids':['AC-01'],
          'scope_ref':'backlog/features/FIXTURE.md#scope','preserve_ref':'backlog/features/FIXTURE.md#preserve','dependency_ref':'backlog/features/FIXTURE.md#dependency',
          'engineering_owner':None,
          'engineering_impact':{k:'ROUTINE' for k in contract.IMPACT_KEYS},
          'story_required_assurance':[],'merge_mode_override':None,
          'build_start_ref':'backlog/features/FIXTURE.md#build-start','final_acceptance_ref':'backlog/features/FIXTURE.md#final','capability_id':'FIXTURE'}
        path.parent.mkdir(parents=True,exist_ok=True);r.atomic_json(path,data);return path
    def assign(self,story='S1',lane='lane-01',role='BUILD',sha=None,invocation=None):
        if role=='BUILD':self.ensure_contract(story)
        return pool.lease(self.repo,'dev-a',story,lane,role,sha or self.base,invocation or role+'-'+story+'-'+lane,True)
    def release(self,assigned,head=None,checks=None,apply=True):
        l=assigned['lease'];path=Path(assigned['workspace']);head=head or self.g('rev-parse','HEAD',repo=path)
        c={'checked_at':r.now(),'lease_token':l['token'],'processes_stopped':True,'processes_stopped_ref':'fixture/quiescent','records_saved':True,'records_saved_ref':'fixture/report'}
        if checks:c.update(checks)
        return pool.release(self.repo,assigned['lane_id'],l['token'],head,c,apply)
    def build(self,story='S1'):
        a=self.assign(story);path=Path(a['workspace']);(path/'src/a.txt').write_text(story+'\n')
        self.g('add','src/a.txt',repo=path);self.g('commit','-m',story,repo=path);head=self.g('rev-parse','HEAD',repo=path)
        self.g('push','origin',head+':refs/heads/work/dev-a/'+story);self.release(a,head)
        rev=self.assign(story,'lane-02','REVIEW',head,'review-'+story);self.release(rev,head)
        p=r.task_path(self.repo,'dev-a',story);t=r.read_json(p);t.update(mr_iid=len(self.mrs)+1,risk_tier=2)
        t['review']={'verdict':'ACCEPT','candidate_sha':head,'report_ref':'docs/review','invocation_id':'review-'+story,'lane_id':'lane-02'}
        t['merge_checks']={'candidate_sha':head,'target_sha':self.base,'checked_at':r.now(),
          'checks':{k:{'status':'PASS','candidate_sha':head,'evidence_ref':'fixtures/'+k} for k in self.cfg['merge_policy']['mandatory_checks']}}
        for f in ('scope_valid','dependencies_ready','no_unresolved_blockers','remote_effects_safe'):t['merge_checks'].update({f:True,f+'_ref':'fixture/'+f})
        t['cleanup_checks']={'checked_at':r.now(),'retention_required':False}
        for f in ('dependencies_clear','processes_stopped','records_published'):t['cleanup_checks'].update({f:True,f+'_ref':'fixture/'+f})
        r.atomic_json(p,t)
        self.mrs[t['mr_iid']]={'iid':t['mr_iid'],'source_project_id':1,'target_project_id':1,'source_branch':t['branch'],'target_branch':'main','diff_refs':{'head_sha':head,'base_sha':self.base},'state':'opened','draft':False,'has_conflicts':False,'detailed_merge_status':'mergeable','head_pipeline':{'sha':head,'status':'success'}}
        return t
    def api(self,repo,cfg,endpoint,method='GET',fields=None):
        self.calls.append((method,endpoint,fields));prefix=forge.GitLabForge(r)._prefix(cfg)
        # Before the '?' branch below: the identity lookup is a top-level path and must
        # not fall through into the merge request listing.
        if endpoint.startswith('users?username='):
            from urllib.parse import unquote
            name=unquote(endpoint.split('=',1)[1])
            return [{'username':name}] if self.forge_users is None or name in self.forge_users else []
        if endpoint==prefix:return {'id':1,'path_with_namespace':cfg['forge']['repo'],'merge_method':'merge'}
        if '/notes/' in endpoint:return {'system':False,'author':{'username':'human'},'body':'APPROVE '+self.mrs[1]['diff_refs']['head_sha']}
        if '/repository/branches/' in endpoint:
            from urllib.parse import unquote
            name=unquote(endpoint.split('/repository/branches/')[1]);return {'name':name,'commit':{'id':r.remote_head(repo,cfg['remote'],name)},'protected':False,'default':False}
        if '?' in endpoint:return [copy.deepcopy(x) for x in self.mrs.values() if x['state']=='opened']
        if method=='POST' and endpoint==prefix+'/merge_requests':
            iid=max(self.mrs,default=0)+1;src=fields['source_branch']
            self.mrs[iid]={'iid':iid,'source_project_id':1,'target_project_id':1,'source_branch':src,
              'target_branch':fields['target_branch'],
              'diff_refs':{'head_sha':r.remote_head(repo,cfg['remote'],src),'base_sha':self.base},
              'state':'opened','draft':False,'has_conflicts':False,'detailed_merge_status':'mergeable',
              'head_pipeline':None,'force_remove_source_branch':fields['remove_source_branch'] is True}
            return copy.deepcopy(self.mrs[iid])
        mid=int(endpoint.split('/merge_requests/')[1].split('/')[0]);m=self.mrs[mid]
        if method=='PUT':
            self.put_count+=1;self.assertEqual(fields['sha'],m['diff_refs']['head_sha']);self.assertFalse(fields['auto_merge']);self.assertFalse(fields['should_remove_source_branch'])
            server=self.top/('server'+str(mid));r.run(['git','clone',str(self.remote),str(server)])
            self.g('config','user.name','Server',repo=server);self.g('config','user.email','server@example.invalid',repo=server);self.g('checkout','main',repo=server)
            if fields.get('squash'):
                self.g('merge','--squash','origin/'+m['source_branch'],repo=server);self.g('commit','-m','squash',repo=server)
                m['squash_commit_sha']=self.g('rev-parse','HEAD',repo=server);m['merge_commit_sha']=m['squash_commit_sha']
            else:
                self.g('merge','--no-ff','origin/'+m['source_branch'],'-m','merge',repo=server);m['merge_commit_sha']=self.g('rev-parse','HEAD',repo=server)
            self.g('push','origin','main',repo=server);m.update(state='merged',merged_at=r.now());return copy.deepcopy(m)
        return copy.deepcopy(m)

class PoolTests(RepoCase):
    def test_fixed_pool_count(self):self.assertEqual(4,len(r.worktrees(self.repo)))
    def test_init_idempotent(self):pool.init(self.repo,'dev-a',self.base,True);self.assertEqual(4,len(r.worktrees(self.repo)))
    def test_lease_preview_does_not_checkout(self):
        self.ensure_contract('S1');v=pool.lease(self.repo,'dev-a','S1','lane-01','BUILD',self.base,'inv');self.assertEqual('PREVIEW_ONLY',v['mode']);self.assertIsNone(r.local_ref(self.repo,'refs/heads/work/dev-a/S1'))
    def test_reuse_same_folder_different_stories(self):
        a=self.assign('S1');self.release(a);b=self.assign('S2');self.assertEqual(a['workspace'],b['workspace']);self.assertEqual(4,len(r.worktrees(self.repo)))
    def test_busy_lane_refused(self):
        self.assign()
        with self.assertRaises(r.AEError):self.assign('S2')
    def test_builder_cap_refused(self):
        self.assign()
        with self.assertRaises(r.AEError):self.assign('S2','lane-02')
    def test_no_final_review_while_writer_active(self):
        self.assign()
        with self.assertRaises(r.AEError):self.assign('S1','lane-02','REVIEW')
    def test_no_fix_while_reader_active(self):
        t=self.build();self.assign('S1','lane-03','QA',t['candidate_sha'])
        with self.assertRaises(r.AEError):self.assign('S1','lane-01','FIX',t['candidate_sha'])
    def test_author_lane_cannot_review(self):
        a=self.assign();self.release(a)
        with self.assertRaises(r.AEError):self.assign('S1','lane-01','REVIEW')
    def test_stale_lease_token(self):
        a=self.assign();self.release(a);self.assign('S2')
        with self.assertRaises(r.AEError):self.release(a)
    def test_dirty_lane_not_released(self):
        a=self.assign();(Path(a['workspace'])/'src/a.txt').write_text('dirty')
        with self.assertRaises(r.AEError):self.release(a)
    def test_untracked_not_released(self):
        a=self.assign();(Path(a['workspace'])/'untracked.txt').write_text('important')
        with self.assertRaises(r.AEError):self.release(a)
    def test_ignored_secret_blocks_reuse(self):
        a=self.assign();(Path(a['workspace'])/'.env').write_text('not-real-secret')
        with self.assertRaises(r.AEError):self.release(a)
    def test_allowed_cache_preserved(self):
        self.cfg['pool']['reusable_ignored_directories']=['node_modules/'];self.write_cfg();a=self.assign()
        p=Path(a['workspace'])/'node_modules';p.mkdir();(p/'fixture').write_text('cached');self.release(a);b=self.assign('S2')
        self.assertTrue((Path(b['workspace'])/'node_modules/fixture').exists())
    def test_release_requires_stopped_processes(self):
        a=self.assign()
        with self.assertRaises(r.AEError):self.release(a,checks={'processes_stopped':False})
    def test_release_expired_check(self):
        a=self.assign()
        with self.assertRaises(r.AEError):self.release(a,checks={'checked_at':'2020-01-01T00:00:00+00:00'})
    def test_reader_detached_commit_refused(self):
        t=self.build();a=self.assign('S1','lane-03','QA',t['candidate_sha']);p=Path(a['workspace'])
        self.g('commit','--allow-empty','-m','unexpected',repo=p)
        with self.assertRaises(r.AEError):self.release(a)
    def test_duplicate_story_refused(self):
        a=self.assign();self.release(a)
        with self.assertRaises(r.AEError):self.assign()
    def test_fix_reopens_exact_branch(self):
        t=self.build();a=self.assign('S1','lane-01','FIX',t['candidate_sha']);self.assertEqual('work/dev-a/S1',self.g('branch','--show-current',repo=Path(a['workspace'])))
    def test_fix_wrong_sha_refused(self):
        self.build()
        with self.assertRaises(r.AEError):self.assign('S1','lane-01','FIX',self.base)
    def test_status_no_branch_change(self):
        before=self.g('show-ref');pool.status(self.repo);self.assertEqual(before,self.g('show-ref'))
    def test_no_auto_steal_local_lock(self):
        with r.local_lock(self.repo):
            with self.assertRaises(r.AEError):self.assign()
    def test_wrong_developer_refused(self):
        with self.assertRaises(r.AEError):pool.lease(self.repo,'other','S1','lane-01','BUILD',self.base,'inv',True)
    def test_worktree_locked_refused(self):
        self.g('worktree','lock',str(self.repo/'.claude/worktrees/lane-01'))
        with self.assertRaises(r.AEError):self.assign()
    def test_legacy_creator_retired(self):
        with self.assertRaises(ae.AEError):ae.create_task(self.repo,'dev-a','S1',self.base)
    def test_no_more_than_pool_size(self):
        with self.assertRaises(r.AEError):self.assign(lane='lane-04')
    def test_native_cache_collision_not_overwritten(self):
        self.cfg['pool']['reusable_ignored_directories']=['node_modules/'];self.write_cfg()
        a=self.assign();p=Path(a['workspace'])/'node_modules';p.mkdir();(p/'fixture').write_text('valuable');self.release(a)
        other=self.assign('S2','lane-02');w=Path(other['workspace']);q=w/'node_modules';q.mkdir();(q/'fixture').write_text('tracked')
        self.g('add','-f','node_modules/fixture',repo=w);self.g('commit','-m','tracked collision',repo=w);new=self.g('rev-parse','HEAD',repo=w);self.release(other)
        with self.assertRaises(r.AEError):self.assign('S3','lane-01','BUILD',new)
        self.assertEqual('valuable',(p/'fixture').read_text())

class MergeTests(RepoCase):
    def test_agent_merge_executes_and_releases_lock(self):
        t=self.build();v=merge.execute(self.repo,t,self.cfg,True);self.assertEqual('INTEGRATED',v['result']);self.assertEqual(1,self.put_count);self.assertIsNone(r.remote_head(self.repo,'origin',merge.LOCK_BRANCH))
    def test_preview_never_merges(self):
        t=self.build();self.assertEqual('MERGE_READY',merge.execute(self.repo,t,self.cfg)['result']);self.assertEqual(0,self.put_count)
    def test_developer_mode_never_merges(self):
        t=self.build();self.cfg['merge_mode']='DEVELOPER_REVIEW';v=merge.execute(self.repo,t,self.cfg,True);self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',v['result']);self.assertEqual(0,self.put_count)
    def test_task_can_narrow_to_developer(self):
        # In v0.5 the narrowing override is part of the bound Story contract;
        # the authority remains the same behavior, but local post-bind mutation is not allowed.
        cp=self.ensure_contract('S1');data=r.read_json(cp);data['merge_mode_override']='DEVELOPER_REVIEW';r.atomic_json(cp,data)
        t=self.build();self.assertEqual('DEVELOPER_REVIEW',t['merge_mode_override']);self.assertEqual('MR_READY_FOR_HUMAN_REVIEW',merge.execute(self.repo,t,self.cfg,True)['result'])
    def test_task_cannot_broaden(self):
        t=self.build();self.cfg['merge_mode']='DEVELOPER_REVIEW';t['merge_mode_override']='AGENT_MERGE'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_missing_required_test_blocks(self):
        t=self.build();t['merge_checks']['checks']['focused_tests']['status']='NOT_RUN'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_wrong_test_sha_blocks(self):
        t=self.build();t['merge_checks']['checks']['build']['candidate_sha']=self.base
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_self_review_blocks(self):
        t=self.build();t['review']['lane_id']='lane-01'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_old_review_blocks(self):
        t=self.build();t['review']['candidate_sha']=self.base
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_draft_blocks(self):
        t=self.build();self.mrs[1]['draft']=True
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_ci_failure_blocks(self):
        t=self.build();self.mrs[1]['head_pipeline']['status']='failed'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_ci_other_sha_blocks(self):
        t=self.build();self.mrs[1]['head_pipeline']['sha']=self.base
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_server_approval_required_blocks(self):
        t=self.build();self.mrs[1]['detailed_merge_status']='not_approved'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_unapproved_merge_policy_blocks(self):
        t=self.build();self.cfg['merge_policy']['approved']=False
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_target_drift_blocks(self):
        t=self.build();t['merge_checks']['target_sha']=t['candidate_sha']
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_other_remote_merge_lease_blocks(self):
        t=self.build();self.g('push','origin',self.base+':refs/heads/'+merge.LOCK_BRANCH)
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
        self.assertEqual(0,self.put_count)
    def test_repeat_is_verification_not_second_merge(self):
        t=self.build();merge.execute(self.repo,t,self.cfg,True);v=merge.execute(self.repo,t,self.cfg,True);self.assertEqual('INTEGRATED',v['result']);self.assertEqual(1,self.put_count)
    def test_squash_proof(self):
        t=self.build();self.cfg['merge_method']='squash';v=merge.execute(self.repo,t,self.cfg,True);self.assertEqual('squash_exact_delta',v['proof']['method'])
    def test_human_gate_when_configured(self):
        t=self.build();self.cfg['merge_policy'].update(human_review_required=True,human_approvers=['human']);t['human_approval_note_id']=99
        self.assertEqual('INTEGRATED',merge.execute(self.repo,t,self.cfg,True)['result'])
    def test_human_gate_missing_blocks(self):
        t=self.build();self.cfg['merge_policy']['human_review_required']=True
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_unknown_request_preserves_lock(self):
        t=self.build();original=self.api
        # Takes the adapter as its first argument, unlike the setUp stub. A plain function
        # set as a class attribute is a descriptor, so `self.api(...)` binds the adapter
        # into it; the setUp stub escapes that only because a bound method is not
        # re-bound. Getting this wrong shifts every argument by one and surfaces far away
        # as a TypeError inside _prefix.
        def lose_response(_adapter,repo,cfg,endpoint,method='GET',fields=None):
            if method=='PUT':original(repo,cfg,endpoint,method,fields);raise r.AEError('lost response')
            return original(repo,cfg,endpoint,method,fields)
        with patch.object(forge.GitLabForge,'api',lose_response):
            with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
        self.assertIsNotNone(r.remote_head(self.repo,'origin',merge.LOCK_BRANCH))
        v=merge.execute(self.repo,t,self.cfg,True,True);self.assertEqual('INTEGRATED',v['result']);self.assertIsNone(r.remote_head(self.repo,'origin',merge.LOCK_BRANCH));self.assertEqual(1,self.put_count)

class CleanupTests(RepoCase):
    def merged(self):
        t=self.build();merge.execute(self.repo,t,self.cfg,True);return t
    def test_cleanup_keeps_all_pool_folders(self):
        t=self.merged();v=cleanup.execute(self.repo,t,self.cfg,True)
        self.assertEqual('CLEANUP_COMPLETE',v['cleanup']);self.assertEqual(4,len(r.worktrees(self.repo)));self.assertTrue((self.repo/'.claude/worktrees/lane-01').is_dir())
    def test_cleanup_preview_deletes_nothing(self):
        t=self.merged();cleanup.execute(self.repo,t,self.cfg);self.assertIsNotNone(r.remote_head(self.repo,'origin',t['branch']))
    def test_cleanup_idempotent(self):
        t=self.merged();cleanup.execute(self.repo,t,self.cfg,True);self.assertEqual('CLEANUP_COMPLETE',cleanup.execute(self.repo,t,self.cfg,True)['cleanup'])
    def test_cleanup_does_not_touch_reassigned_lane(self):
        t=self.merged();b=self.assign('S2');v=cleanup.execute(self.repo,t,self.cfg,True)
        self.assertEqual('CLEANUP_COMPLETE',v['cleanup']);self.assertEqual('work/dev-a/S2',self.g('branch','--show-current',repo=Path(b['workspace'])))
    def test_tracking_only_cleanup_remains_pending_in_preview(self):
        t=self.merged();cleanup.execute(self.repo,t,self.cfg,True)
        tracking='refs/remotes/origin/'+t['branch'];self.g('update-ref',tracking,t['candidate_sha'])
        v=cleanup.execute(self.repo,t,self.cfg)
        self.assertEqual('CLEANUP_PENDING',v['cleanup']);self.assertEqual('SAFE_PRUNE',v['tracking_ref'])
        self.assertEqual(t['candidate_sha'],r.local_ref(self.repo,tracking))
    def test_tracking_only_cleanup_apply(self):
        t=self.merged();cleanup.execute(self.repo,t,self.cfg,True)
        tracking='refs/remotes/origin/'+t['branch'];self.g('update-ref',tracking,t['candidate_sha'])
        v=cleanup.execute(self.repo,t,self.cfg,True)
        self.assertEqual('CLEANUP_COMPLETE',v['cleanup']);self.assertIsNone(r.local_ref(self.repo,tracking))
    def test_changed_tracking_ref_preserved(self):
        t=self.merged();self.g('update-ref','refs/remotes/origin/'+t['branch'],self.base)
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
        self.assertEqual(self.base,r.local_ref(self.repo,'refs/remotes/origin/'+t['branch']))
    def test_active_same_story_blocks(self):
        t=self.merged();self.assign('S1','lane-03','QA',t['candidate_sha'])
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_not_merged_blocks(self):
        t=self.build()
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_dependency_blocks(self):
        t=self.merged();t['cleanup_checks']['dependencies_clear']=False
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_retention_blocks(self):
        t=self.merged();t['cleanup_checks']['retention_required']=True
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_unpublished_records_blocks(self):
        t=self.merged();t['cleanup_checks']['records_published']=False
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_branch_new_work_blocks(self):
        t=self.merged();a=self.assign('S1','lane-01','FIX',t['candidate_sha']);p=Path(a['workspace']);self.g('commit','--allow-empty','-m','new work',repo=p);self.release(a)
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_unapproved_cleanup_blocks(self):
        t=self.merged();self.cfg['cleanup']['approved']=False
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_squash_cleanup(self):
        self.cfg['merge_method']='squash';t=self.merged();self.assertEqual('CLEANUP_COMPLETE',cleanup.execute(self.repo,t,self.cfg,True)['cleanup'])

class PureTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'schema_version':5,'kit_version':'0.7','configuration_approved':True,'remote_actions_ready':True,'remote_actions_review_ref':'setup','target_branch':'main','remote':'origin','merge_mode':'AGENT_MERGE','forge':{'provider':'gitlab','host':'gitlab.example.invalid','repo':'group/product'},'merge_policy':{'approved':True,'approval_ref':'setup'},'cleanup':{'mode':'SAFE_MERGED','approved':True,'approval_ref':'setup'},'assurance_policy':{'approved':True,'approval_ref':'setup/assurance'}}
    def check(self,cmd):return guard.inspect({'tool_name':'Bash','tool_input':{'command':cmd},'cwd':'/repo'},self.cfg)[0]
    def test_direct_merge_denied(self):self.assertEqual('deny',self.check('glab mr merge 1 --sha '+'a'*40+' --auto-merge=false'))
    def test_guard_merge_helper_allowed(self):self.assertIsNone(self.check('python tools/agentic/merge.py --task .agentic/local/tasks/ae-dev-a-S1.json --apply'))
    def test_guard_human_mode_denies_apply(self):
        self.cfg['merge_mode']='DEVELOPER_REVIEW';self.assertEqual('deny',self.check('python tools/agentic/merge.py --task task.json --apply'))
    def test_guard_human_mode_allows_preview(self):
        self.cfg['merge_mode']='DEVELOPER_REVIEW';self.assertIsNone(self.check('python tools/agentic/merge.py --task task.json'))
    def test_guard_chained_helper_denied(self):self.assertEqual('deny',self.check('python tools/agentic/pool.py status; rm -rf example'))
    def test_guard_direct_switch_denied(self):self.assertEqual('deny',self.check('git -C lane checkout main'))
    def test_guard_direct_remove_denied(self):self.assertEqual('deny',self.check('git worktree remove lane-01'))
    def test_guard_direct_target_push_denied(self):self.assertEqual('deny',self.check('git push origin HEAD:refs/heads/main'))
    def test_guard_scoped_push(self):self.assertIsNone(self.check('git push origin HEAD:refs/heads/work/dev-a/S1'))
    def test_guard_direct_lock_push_denied(self):self.assertEqual('deny',self.check('git push origin HEAD:refs/heads/ae/locks/integration'))
    def test_guard_api_mutation_denied(self):self.assertEqual('deny',self.check('glab api --method PUT projects/1/merge_requests/1/merge'))
    def test_guard_approve_denied(self):self.assertEqual('deny',self.check('glab mr approve 1'))
    # The gh equivalents. Without these, GitHub write operations were not blocked at all.
    def test_guard_gh_review_denied(self):self.assertEqual('deny',self.check('gh pr review 1 --approve'))
    def test_guard_gh_merge_denied(self):self.assertEqual('deny',self.check('gh pr merge 1 --merge'))
    def test_guard_gh_api_mutation_denied(self):self.assertEqual('deny',self.check('gh api -X PUT repos/o/n/pulls/1/merge'))
    def test_guard_cli_pull_request_creation_denied_on_both(self):
        # v0.6 allowed these under a pattern check. v0.7 creates the request through the
        # forge seam instead, so the CLI forms are refused outright -- including the one
        # v0.6 accepted with fully explicit refs.
        for command in ('gh pr create --fill',
                        'gh pr create --head work/dev-a/S1 --base release',
                        'gh pr create --head work/dev-a/S1 --base main --title t --body b',
                        'glab mr create --source-branch work/dev-a/S1 --target-branch main',
                        'glab mr new --source-branch work/dev-a/S1 --target-branch main'):
            decision,reason=guard.inspect({'tool_name':'Bash','cwd':'/repo','tool_input':{'command':command}},self.cfg)
            self.assertEqual('deny',decision,command)
            self.assertIn('pull_request.py',reason,command)
    def test_guard_gh_pr_read_allowed(self):self.assertIsNone(self.check('gh pr list --state open'))
    def test_guard_superseded_schema_blocks_remote_effects(self):
        # remote_ready gates on the current schema/kit. A configuration left on v0.5
        # must not be treated as approved for push/pull-request effects.
        self.cfg.update(schema_version=3,kit_version='0.5')
        self.assertEqual('deny',self.check('git push origin HEAD:refs/heads/work/dev-a/S1'))
        self.assertEqual('deny',self.check('python tools/agentic/pull_request.py --task t.json --title x --apply'))
    def test_guard_force_reset_denied(self):self.assertEqual('deny',self.check('git reset --hard'))
    def test_guard_policy_edit_denied(self):self.assertEqual('deny',guard.inspect({'tool_name':'Edit','cwd':'/repo','tool_input':{'file_path':'docs/agentic/POOL.md'}},self.cfg)[0])
    def test_full_sha_no_short(self):
        with self.assertRaises(r.AEError):r.full('abcdef')
    def test_timestamp_invalid(self):
        with self.assertRaises(r.AEError):r.fresh('yesterday')
    def test_ident_no_path(self):
        with self.assertRaises(r.AEError):r.ident('../other')
    def test_remote_password_refused(self):
        with self.assertRaises(r.AEError):r.parse_remote('https://user:pass@example.invalid/g/r.git')
    def test_remote_ssh_supported(self):self.assertEqual(('example.invalid','g/r'),r.parse_remote('git@example.invalid:g/r.git'))
    def test_cache_root_refused(self):
        with self.assertRaises(r.AEError):pool.pool_config({'pool':{'model':'REUSABLE_LANE_POOL','size':7,'root':'.claude/worktrees','reusable_ignored_directories':['./']}})
    def test_retired_config_mode_refused(self):
        with self.assertRaises(r.AEError):merge.mode({'merge_mode':'STOP_MR_READY'}, {})



class AdditionalTests(RepoCase):
    def test_snapshot_clean_and_scoped(self):
        t=self.build();a=self.assign('S1','lane-03','QA',t['candidate_sha']);p=Path(a['workspace'])
        v=ae.snapshot(p,self.base,self.top/'evidence');self.assertTrue(v['evidence_eligible'])
        scope=self.top/'scope.json';scope.write_text('{"allowed_paths":["src/"]}');self.assertTrue(ae.scope_check(p,self.base,scope)['passed'])
    def test_snapshot_dirty_is_not_evidence(self):
        a=self.assign();p=Path(a['workspace']);(p/'src/a.txt').write_text('dirty');v=ae.snapshot(p,self.base,self.top/'evidence');self.assertFalse(v['evidence_eligible'])
    def test_snapshot_does_not_overwrite(self):
        ae.snapshot(self.repo,self.base,self.top/'evidence')
        with self.assertRaises(FileExistsError):ae.snapshot(self.repo,self.base,self.top/'evidence')
    def test_scope_rejects_other_files(self):
        t=self.build();a=self.assign('S1','lane-03','QA',t['candidate_sha']);p=self.top/'scope.json';p.write_text('{"allowed_paths":["docs/"]}');self.assertFalse(ae.scope_check(Path(a['workspace']),self.base,p)['passed'])
    def test_fictitious_reviewer_assignment_refused(self):
        t=self.build();t['review']['invocation_id']='never-dispatched'
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_forced_source_delete_policy_refused(self):
        t=self.build();self.mrs[1]['force_remove_source_branch']=True
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_ci_setting_not_implicit(self):
        t=self.build();self.cfg.pop('ci_required')
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_release_preview_keeps_lease(self):
        a=self.assign();self.release(a,apply=False);self.assertEqual('LEASED',pool.load(self.repo)['lanes']['lane-01']['state'])
    def test_changed_remote_source_refused(self):
        t=self.build();self.g('push','origin',self.base+':refs/heads/work/dev-a/other')
        # Deliberately create a new remote commit to simulate a second actor, not normal workflow.
        a=self.assign('S1','lane-01','FIX',t['candidate_sha']);p=Path(a['workspace']);self.g('commit','--allow-empty','-m','extra',repo=p);h=self.g('rev-parse','HEAD',repo=p);self.g('push','origin',h+':refs/heads/'+t['branch']);self.release(a)
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_tier_two_cannot_use_main_only_review(self):
        t=self.build();t['review'].update(lane_id='control-plane',invocation_id='main-review')
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_tier_one_main_review_allowed(self):
        cp=self.ensure_contract('S1');data=r.read_json(cp);data['risk_tier']=1;r.atomic_json(cp,data)
        t=self.build();t['risk_tier']=1;t['review'].update(lane_id='control-plane',invocation_id='main-review')
        self.assertEqual('MERGE_READY',merge.execute(self.repo,t,self.cfg)['result'])
    def test_unexpected_checked_out_branch_blocks_cleanup(self):
        t=self.build();merge.execute(self.repo,t,self.cfg,True);self.g('checkout',t['branch'],repo=self.repo/'.claude/worktrees/lane-01')
        with self.assertRaises(r.AEError):cleanup.execute(self.repo,t,self.cfg,True)
    def test_legacy_task_manifest_rejected(self):
        t=self.build();p=r.task_path(self.repo,'dev-a','S1');t['workspace_model']='PER_STORY';r.atomic_json(p,t)
        with self.assertRaises(r.AEError):r.load_task(self.repo,p)
    def test_human_approval_wrong_identity(self):
        t=self.build();self.cfg['merge_policy'].update(human_review_required=True,human_approvers=['other']);t['human_approval_note_id']=99
        with self.assertRaises(r.AEError):merge.execute(self.repo,t,self.cfg,True)
    def test_pool_status_marks_external_drift(self):
        self.g('checkout','main',repo=self.repo/'.claude/worktrees/lane-01')
        self.assertEqual('RECOVERY_REQUIRED',pool.status(self.repo)['lanes'][0]['state'])

if __name__ == "__main__":
    unittest.main()
