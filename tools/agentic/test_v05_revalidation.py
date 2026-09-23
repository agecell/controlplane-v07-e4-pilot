#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as r
import forge
import contract
import pool
import assurance


def config_fixture():
    cfg = json.loads((HERE.parents[1] / '.agentic/project.json').read_text())
    cfg.update(
        project_name='Fixture', target_branch='main',
        forge={'provider': 'gitlab', 'host': 'gitlab.example.invalid', 'repo': 'group/product'},
        configuration_approved=True,
        remote_actions_ready=True, remote_actions_review_ref='setup/remote',
        team_coordination_ref='team/board', merge_method='merge_commit',
        commands={'test': 'fixture'}, ci_policy='source SHA', merge_mode='DEVELOPER_REVIEW')
    cfg['pool']['size'] = 3
    cfg['merge_policy'].update(approved=True, approval_ref='setup/merge')
    cfg['cleanup'].update(approved=True, approval_ref='setup/cleanup')
    cfg['assurance_policy'].update(approved=True, approval_ref='setup/assurance')
    return cfg


class GateLocalRevalidationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.top = Path(self.tmp.name); self.repo = self.top / 'repo'; self.repo.mkdir()
        self.g('init', '-b', 'main'); self.g('config', 'user.name', 'Fixture'); self.g('config', 'user.email', 'fixture@example.invalid')
        (self.repo / '.gitignore').write_text('.agentic/local/\n.claude/worktrees/\n')
        (self.repo / '.agentic').mkdir()
        p = self.repo / 'docs/agentic/AGENT_RULES.md'; p.parent.mkdir(parents=True); p.write_text('rules')
        (self.repo / 'backlog/features').mkdir(parents=True); (self.repo / 'backlog/stories').mkdir(parents=True)
        (self.repo / 'backlog/features/FEAT-101.md').write_text('# FEAT-101\n')
        (self.repo / 'backlog/stories/STORY-101.md').write_text('# STORY-101\n')
        self.cfg = config_fixture(); self.write_cfg(); self.g('add', '.'); self.g('commit', '-m', 'base'); self.base = self.g('rev-parse', 'HEAD')
        self.backlog_blob = self.g('rev-parse', self.base + ':backlog/features/FEAT-101.md')
        self.story_blob = self.g('rev-parse', self.base + ':backlog/stories/STORY-101.md')
        self.remote = self.top / 'remote.git'; r.run(['git', 'init', '--bare', str(self.remote)])
        self.g('remote', 'add', 'origin', str(self.remote)); self.g('push', 'origin', 'main'); self.g('switch', '-c', 'ae/records/dev-a/sprint-01')
        pool.init(self.repo, 'dev-a', self.base, True)

    def tearDown(self): self.tmp.cleanup()
    def g(self, *args, repo=None): return r.git(repo or self.repo, *args)[1]
    def write_cfg(self): (self.repo / '.agentic/project.json').write_text(json.dumps(self.cfg))

    def contract_data(self, obligations, *, material=False):
        impact = {k: 'ROUTINE' for k in contract.IMPACT_KEYS}
        owner = None
        if material:
            impact['human_ownership'] = 'MATERIAL'; impact['architecture'] = 'MATERIAL'; impact['security_privacy_data'] = 'MATERIAL'
            owner = {'owner_ref': 'team:dev-a', 'attestation_provider': 'gitlab', 'attestation_identity': 'dev-a-user'}
        return {
            'contract_schema_version': 2, 'evidence_expectations': [], 'story_id': 'STORY-101', 'assigned_developer': 'dev-a', 'sprint': 'sprint-01', 'risk_tier': 3 if material else 2,
            'publication': {'canonical_backlog': {'path': 'backlog/features/FEAT-101.md', 'revision': 'REV-03', 'publication_sha': self.base, 'blob_sha': self.backlog_blob}, 'story_card': {'path': 'backlog/stories/STORY-101.md', 'publication_sha': self.base, 'blob_sha': self.story_blob}},
            'traceability_root': ['REQ-101'], 'ac_ids': ['AC-01'], 'scope_ref': 'backlog/features/FEAT-101.md#scope', 'preserve_ref': 'backlog/features/FEAT-101.md#preserve', 'dependency_ref': 'backlog/features/FEAT-101.md#dependency',
            'engineering_owner': owner, 'engineering_impact': impact, 'story_required_assurance': obligations,
            'merge_mode_override': None, 'build_start_ref': 'backlog/features/FEAT-101.md#build-start', 'final_acceptance_ref': 'backlog/features/FEAT-101.md#final', 'capability_id': 'FEAT-101'}

    def register(self, data):
        p = contract.registered_contract_path(self.repo, 'dev-a', 'STORY-101'); p.parent.mkdir(parents=True, exist_ok=True); r.atomic_json(p, data); return p

    def make_task(self, obligations, *, material=False):
        self.register(self.contract_data(obligations, material=material))
        a = pool.lease(self.repo, 'dev-a', 'STORY-101', 'lane-01', 'BUILD', self.base, 'build-101', True)
        w = Path(a['workspace']); (w / 'candidate.txt').write_text('candidate-v1\n'); self.g('add', 'candidate.txt', repo=w); self.g('commit', '-m', 'candidate-1', repo=w); head = self.g('rev-parse', 'HEAD', repo=w)
        self.release(a, head)
        tp = r.task_path(self.repo, 'dev-a', 'STORY-101'); t = r.read_json(tp); t['mr_iid'] = 42; r.atomic_json(tp, t)
        return tp, head

    def release(self, lease_result, head):
        checks = {'checked_at': r.now(), 'lease_token': lease_result['lease']['token'], 'processes_stopped': True, 'processes_stopped_ref': 'fixture/process', 'records_saved': True, 'records_saved_ref': 'fixture/report'}
        return pool.release(self.repo, lease_result['lane_id'], lease_result['lease']['token'], head, checks, True)

    def fix(self, tp, *, content=None, empty=False):
        t = r.read_json(tp); old = t['candidate_sha']
        a = pool.lease(self.repo, 'dev-a', 'STORY-101', 'lane-01', 'FIX', old, 'fix-' + old[:8], True)
        w = Path(a['workspace'])
        if empty:
            self.g('commit', '--allow-empty', '-m', 'empty-fix', repo=w)
        else:
            (w / 'candidate.txt').write_text(content or 'candidate-v2\n'); self.g('add', 'candidate.txt', repo=w); self.g('commit', '-m', 'candidate-2', repo=w)
        head = self.g('rev-parse', 'HEAD', repo=w); self.release(a, head); return head

    @staticmethod
    def nfr_ob():
        return [{'gate_id': 'nfr_performance', 'stage': 'PRE_MERGE', 'mode': 'OBJECTIVE_TOOL', 'evidence_plan_ref': 'backlog/features/FEAT-101.md#nfr'}]

    @staticmethod
    def human_ob():
        return [{'gate_id': 'human_understanding', 'stage': 'PRE_MERGE', 'mode': 'STORY_EXPLAIN_BACK', 'evidence_plan_ref': 'backlog/features/FEAT-101.md#owner'}]

    @staticmethod
    def security_ob():
        return [{'gate_id': 'security_review', 'stage': 'PRE_MERGE', 'mode': 'BOUNDED_MATERIAL', 'evidence_plan_ref': 'backlog/features/FEAT-101.md#security'}]

    def satisfy_tool(self, tp):
        return assurance.satisfy_tool(self.repo, tp, 'nfr_performance', 'tool:perf', ['evidence/perf.json'], True)

    def fake_project_pull(self, task):
        # The normalized shape project_pull returns in v0.6, not GitLab's own fields.
        return ({'id': 1, 'full_name': 'group/product', 'merge_methods': {'merge_commit'}},
                {'number': 42, 'state': 'open', 'draft': False, 'source_branch': task['branch'],
                 'target_branch': 'main', 'head_sha': task['candidate_sha'], 'same_project': True})

    @staticmethod
    def note_api(note):
        def api(repo, cfg, endpoint, method='GET', fields=None):
            if '/notes/' in endpoint: return copy.deepcopy(note)
            raise AssertionError(endpoint)
        return api

    def satisfy_human(self, tp, gate='human_understanding', user='dev-a-user', refs=None, note_id=99):
        req = assurance.attestation_request(self.repo, tp, gate); task = r.read_json(tp)
        note = {'system': False, 'author': {'username': user}, 'body': req['expected_note'], 'created_at': '2026-09-13T15:00:00+00:00'}
        with patch.object(r, 'project_pull', return_value=self.fake_project_pull(task)), patch.object(forge.GitLabForge, 'api', side_effect=self.note_api(note)):
            return assurance.satisfy_human(self.repo, tp, gate, note_id, refs or [], True)

    def test_closed_candidate_gate_moves_to_needs_revalidation_on_fix(self):
        tp, old = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); new = self.fix(tp, content='changed\n')
        g = r.read_json(tp)['assurance']['pre_merge']['nfr_performance']
        self.assertEqual('NEEDS_REVALIDATION', g['status']); self.assertEqual(old, g['revalidation']['from_binding']); self.assertEqual(new, g['revalidation']['to_binding'])

    def test_open_candidate_gate_rebinds_without_false_revalidation(self):
        tp, _ = self.make_task(self.nfr_ob()); new = self.fix(tp, content='changed\n')
        g = r.read_json(tp)['assurance']['pre_merge']['nfr_performance']
        self.assertEqual('PENDING', g['status']); self.assertEqual(new, g['binding']['candidate_sha']); self.assertEqual('NOT_EVALUATED', g['revalidation']['disposition'])

    def test_auto_equivalent_empty_commit_reuses_satisfied_gate(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); new = self.fix(tp, empty=True)
        out = assurance.revalidate_auto(self.repo, tp, 'nfr_performance', True); g = r.read_json(tp)['assurance']['pre_merge']['nfr_performance']
        self.assertEqual('EQUIVALENT', out['disposition']); self.assertEqual('SATISFIED', g['status']); self.assertEqual(new, g['binding']['candidate_sha']); self.assertEqual('EQUIVALENT', g['revalidation']['disposition'])

    def test_auto_tree_change_is_unknown_and_fail_closed(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n')
        out = assurance.revalidate_auto(self.repo, tp, 'nfr_performance', True); g = r.read_json(tp)['assurance']['pre_merge']['nfr_performance']
        self.assertEqual('UNKNOWN', out['disposition']); self.assertEqual('NEEDS_REVALIDATION', g['status']); self.assertGreater(out['delta']['changed_path_count'], 0)

    def test_manual_non_material_reuses_evidence(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); new = self.fix(tp, content='changed\n')
        before = list(r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['evidence_refs'])
        out = assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'NON_MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:delta-101', 'records/delta-101.md', True)
        g = r.read_json(tp)['assurance']['pre_merge']['nfr_performance']; self.assertEqual('SATISFIED', g['status']); self.assertEqual(before, g['evidence_refs']); self.assertEqual(new, g['binding']['candidate_sha']); self.assertTrue(out['revalidation_record_ref'].startswith('revalidation:'))

    def test_manual_material_reopens_only_that_gate_and_clears_stale_evidence(self):
        obs = self.nfr_ob() + self.human_ob(); tp, _ = self.make_task(obs, material=True); self.satisfy_tool(tp); self.satisfy_human(tp); self.fix(tp, content='changed\n')
        assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:delta', 'records/perf-delta.md', True)
        t = r.read_json(tp); perf = t['assurance']['pre_merge']['nfr_performance']; human = t['assurance']['pre_merge']['human_understanding']
        self.assertEqual('PENDING', perf['status']); self.assertEqual([], perf['evidence_refs']); self.assertIsNone(perf['attestor_ref']); self.assertEqual('NEEDS_REVALIDATION', human['status'])

    def test_manual_unknown_keeps_gate_needing_decision(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n')
        out = assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'UNKNOWN', 'INDEPENDENT_REVIEWER', 'reviewer:delta', 'records/unknown.md', True)
        self.assertEqual('ASSURANCE_REVALIDATION_DECISION_REQUIRED', out['result']); self.assertEqual('NEEDS_REVALIDATION', r.read_json(tp)['assurance']['pre_merge']['nfr_performance']['status'])

    def test_material_revalidation_allows_fresh_tool_satisfaction(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n')
        assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:delta', 'records/material.md', True)
        out = assurance.satisfy_tool(self.repo, tp, 'nfr_performance', 'tool:perf-v2', ['evidence/perf-v2.json'], True); self.assertEqual('SATISFIED', out['status'])

    def test_non_material_human_gate_reuses_server_attestation_without_new_note(self):
        tp, _ = self.make_task(self.human_ob(), material=True); self.satisfy_human(tp); old_ref = r.read_json(tp)['assurance']['pre_merge']['human_understanding']['attestor_ref']; self.fix(tp, content='copy-only\n')
        assurance.revalidate_assess(self.repo, tp, 'human_understanding', 'NON_MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:human-delta', 'records/human-nonmaterial.md', True)
        g = r.read_json(tp)['assurance']['pre_merge']['human_understanding']; self.assertEqual('SATISFIED', g['status']); self.assertEqual(old_ref, g['attestor_ref'])

    def test_material_human_gate_requires_new_attestation(self):
        tp, _ = self.make_task(self.human_ob(), material=True); self.satisfy_human(tp); self.fix(tp, content='material-human-change\n')
        assurance.revalidate_assess(self.repo, tp, 'human_understanding', 'MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:human-delta', 'records/human-material.md', True)
        g = r.read_json(tp)['assurance']['pre_merge']['human_understanding']; self.assertEqual('PENDING', g['status']); self.assertIsNone(g['attestor_ref'])
        req = assurance.attestation_request(self.repo, tp, 'human_understanding'); self.assertTrue(req['expected_note'].startswith('AE-ASSURE human_understanding STORY-101 '))

    def test_material_security_revalidation_requires_fresh_composite_evidence(self):
        self.cfg['assurance_policy']['human_attestors'] = {'security_review': {'BOUNDED_MATERIAL': ['security-champion']}}; self.write_cfg()
        tp, _ = self.make_task(self.security_ob(), material=True); self.satisfy_human(tp, 'security_review', 'security-champion', ['evidence/security-v1.json']); self.fix(tp, content='security-change\n')
        assurance.revalidate_assess(self.repo, tp, 'security_review', 'MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:security-delta', 'records/security-material.md', True)
        with self.assertRaises(r.AEError): self.satisfy_human(tp, 'security_review', 'security-champion', [])
        out = self.satisfy_human(tp, 'security_review', 'security-champion', ['evidence/security-v2.json'], note_id=100); self.assertEqual('SATISFIED', out['status'])

    def test_revalidation_record_preserves_previous_state(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n')
        out = assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:delta', 'records/material.md', True)
        rel = out['revalidation_record_ref'].removeprefix('revalidation:'); record = r.read_json(self.repo / rel)
        self.assertEqual('SATISFIED', record['previous_status']); self.assertIn('evidence/perf.json', record['previous_evidence_refs']); self.assertEqual('records/material.md', record['rationale_ref'])

    def test_preview_does_not_write_task_or_record(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n'); before = tp.read_bytes()
        out = assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'NON_MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:delta', 'records/nonmaterial.md', False)
        self.assertEqual('PREVIEW_ONLY', out['mode']); self.assertEqual(before, tp.read_bytes()); rel = out['revalidation_record_ref'].removeprefix('revalidation:'); self.assertFalse((self.repo / rel).exists())

    def test_auto_preview_does_not_write_record(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, empty=True); before = tp.read_bytes()
        out = assurance.revalidate_auto(self.repo, tp, 'nfr_performance', False); self.assertEqual('PREVIEW_ONLY', out['mode']); self.assertEqual(before, tp.read_bytes()); self.assertFalse((self.repo / out['revalidation_record_ref'].removeprefix('revalidation:')).exists())

    def test_stale_to_binding_is_rejected(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n'); t = r.read_json(tp); t['assurance']['pre_merge']['nfr_performance']['revalidation']['to_binding'] = self.base; r.atomic_json(tp, t)
        with self.assertRaises(r.AEError): assurance.revalidate_auto(self.repo, tp, 'nfr_performance', True)

    def test_invalid_assessor_class_rejected(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n')
        with self.assertRaises(r.AEError): assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'NON_MATERIAL_TO_GATE', 'COMPOSITE', 'bad', 'records/x.md', True)

    def test_manual_equivalent_is_allowed_with_rationale(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, empty=True)
        out = assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'EQUIVALENT', 'INDEPENDENT_REVIEWER', 'reviewer:tree', 'records/equivalent.md', True); self.assertEqual('SATISFIED', out['status'])

    def test_revalidation_is_gate_local_with_two_closed_gates(self):
        obs = self.nfr_ob() + self.human_ob(); tp, _ = self.make_task(obs, material=True); self.satisfy_tool(tp); self.satisfy_human(tp); self.fix(tp, content='changed\n')
        assurance.revalidate_assess(self.repo, tp, 'nfr_performance', 'NON_MATERIAL_TO_GATE', 'CONTROL_PLANE', 'cp:perf', 'records/perf.md', True)
        t = r.read_json(tp); self.assertEqual('SATISFIED', t['assurance']['pre_merge']['nfr_performance']['status']); self.assertEqual('NEEDS_REVALIDATION', t['assurance']['pre_merge']['human_understanding']['status'])

    def test_technical_review_exact_candidate_still_becomes_pending_after_fix(self):
        self.cfg['assurance_policy']['tier_required_gates']['2'] = [{'gate_id': 'technical_review', 'stage': 'PRE_MERGE', 'mode': None}]; self.write_cfg()
        tp, old = self.make_task([]); t = r.read_json(tp); t['review'] = {'verdict': 'ACCEPT', 'candidate_sha': old, 'invocation_id': 'review-1', 'lane_id': 'lane-02', 'report_ref': 'records/review.md'}; r.atomic_json(tp, t)
        self.assertEqual('SATISFIED', assurance.status(self.repo, tp)['technical_review']['status']); self.fix(tp, content='changed\n'); self.assertEqual('PENDING', assurance.status(self.repo, tp)['technical_review']['status'])

    def test_contract_bound_gate_not_marked_for_candidate_revalidation(self):
        gid = 'project_defined:contract_review'
        self.cfg['assurance_policy']['custom_gates'] = {gid: {'policy_ref': 'docs/contract.md', 'allowed_stages': ['PRE_MERGE'], 'allowed_modes': ['CHECK'], 'attestor_class': 'TOOL', 'binding_class': 'CONTRACT', 'minimum_evidence': 1, 'freshness_rule': 'IMMUTABLE_BINDING', 'failure_behavior': 'BLOCK_STAGE'}}; self.write_cfg()
        ob = [{'gate_id': gid, 'stage': 'PRE_MERGE', 'mode': 'CHECK', 'evidence_plan_ref': 'backlog/features/FEAT-101.md#contract'}]
        tp, _ = self.make_task(ob); assurance.satisfy_tool(self.repo, tp, gid, 'tool:contract', ['evidence/contract.json'], True); self.fix(tp, content='changed\n')
        g = r.read_json(tp)['assurance']['pre_merge'][gid]; self.assertEqual('SATISFIED', g['status']); self.assertEqual('CONTRACT', g['binding_class'])

    def test_waiver_is_not_silently_carried_to_new_candidate(self):
        self.cfg['assurance_policy']['waiver_policy']['gates'] = {'human_understanding': {'waivable': True, 'risk_owners': ['eng-manager']}}; self.write_cfg()
        tp, _ = self.make_task(self.human_ob(), material=True)
        req = assurance.create_waiver_request(self.repo, tp, 'human_understanding', 'WV-101', 'eng-manager', 'reason', 'unmet', 'residual', [], None, None, True)
        task = r.read_json(tp); note = {'system': False, 'author': {'username': 'eng-manager'}, 'body': req['expected_note'], 'created_at': '2026-09-13T15:10:00+00:00'}
        with patch.object(r, 'project_pull', return_value=self.fake_project_pull(task)), patch.object(forge.GitLabForge, 'api', side_effect=self.note_api(note)):
            assurance.apply_waiver(self.repo, tp, 'human_understanding', 'WV-101', 201, True)
        self.fix(tp, empty=True); out = assurance.revalidate_auto(self.repo, tp, 'human_understanding', True); g = r.read_json(tp)['assurance']['pre_merge']['human_understanding']
        self.assertEqual('ASSURANCE_REVALIDATED_WAIVER_REAPPROVAL_REQUIRED', out['result']); self.assertEqual('PENDING', g['status']); self.assertIsNone(g['waiver_ref'])

    def test_status_exposes_revalidation_disposition(self):
        tp, _ = self.make_task(self.nfr_ob()); self.satisfy_tool(tp); self.fix(tp, content='changed\n'); assurance.revalidate_auto(self.repo, tp, 'nfr_performance', True)
        st = assurance.status(self.repo, tp); self.assertEqual('UNKNOWN', st['pre_merge']['nfr_performance']['revalidation']['disposition'])

    def test_revalidate_requires_gate_to_be_needs_revalidation(self):
        tp, _ = self.make_task(self.nfr_ob())
        with self.assertRaises(r.AEError): assurance.revalidate_auto(self.repo, tp, 'nfr_performance', True)


if __name__ == '__main__': unittest.main(verbosity=2)
