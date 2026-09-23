"""Phase 12 local final validation. No Claude Code or real GitLab calls."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PACKAGE = REPO.parent
sys.path.insert(0, str(HERE))

import guard


class FinalRuntimeAuditCase(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((REPO / '.agentic/project.json').read_text(encoding='utf-8'))

    def approved_cfg(self):
        cfg = copy.deepcopy(self.cfg)
        cfg.update(
            project_name='Fixture', target_branch='main',
            forge={'provider': 'gitlab', 'host': 'gitlab.example.invalid', 'repo': 'group/product'},
            team_coordination_ref='team/board', configuration_approved=True,
            remote_actions_ready=True, remote_actions_review_ref='setup/remote', merge_method='merge_commit',
        )
        cfg['merge_policy'].update(approved=True, approval_ref='setup/merge')
        cfg['cleanup'].update(approved=True, approval_ref='setup/cleanup')
        cfg['assurance_policy'].update(approved=True, approval_ref='setup/assurance')
        return cfg

    def test_project_template_is_v07_schema5_and_fail_closed(self):
        self.assertEqual(5, self.cfg['schema_version'])
        self.assertEqual('0.7', self.cfg['kit_version'])
        self.assertFalse(self.cfg['configuration_approved'])
        self.assertFalse(self.cfg['remote_actions_ready'])
        self.assertFalse(self.cfg['merge_policy']['approved'])
        self.assertFalse(self.cfg['assurance_policy']['approved'])

    def test_settings_allow_contract_and_assurance_helpers(self):
        settings = json.loads((REPO / '.claude/settings.json').read_text(encoding='utf-8'))
        allow = set(settings['permissions']['allow'])
        for helper in ('contract.py', 'assurance.py', 'pull_request.py'):
            self.assertIn(f'Bash(python tools/agentic/{helper} *)', allow)
            self.assertIn(f'Bash(python3 tools/agentic/{helper} *)', allow)

    def test_settings_allow_forge_cli_reads_but_not_creation(self):
        # A GitHub project would otherwise hit a permission prompt on every read. The
        # guard, not this allow-list, is what refuses the dangerous forms.
        settings = json.loads((REPO / '.claude/settings.json').read_text(encoding='utf-8'))
        allow = set(settings['permissions']['allow'])
        for entry in ('Bash(glab mr list *)', 'Bash(glab mr view *)',
                      'Bash(gh pr list *)', 'Bash(gh pr view *)'):
            self.assertIn(entry, allow)
        # Creation moved behind the forge seam in v0.7. Leaving these allowed would let
        # a prompt-free CLI creation sit alongside the guard denial that replaced it.
        for entry in ('Bash(glab mr create *)', 'Bash(gh pr create *)'):
            self.assertNotIn(entry, allow)

    def test_guard_version_constants_match_contract(self):
        # guard.py runs as a hook on every tool call, so it stays import-light and
        # duplicates these two rather than importing contract.py. They must not drift:
        # in v0.6 they did, and the stale pair denied every remote operation.
        import contract
        self.assertEqual(contract.PROJECT_SCHEMA_VERSION, guard.SCHEMA_VERSION)
        self.assertEqual(contract.KIT_VERSION, guard.KIT_VERSION)

    def test_remote_guard_requires_current_schema(self):
        cfg = self.approved_cfg()
        self.assertTrue(guard.remote_ready(cfg))
        cfg['schema_version'] = 3; cfg['kit_version'] = '0.5'
        self.assertFalse(guard.remote_ready(cfg))

    def test_remote_guard_requires_assurance_policy_approval(self):
        cfg = self.approved_cfg()
        self.assertTrue(guard.remote_ready(cfg))
        cfg['assurance_policy']['approved'] = False
        self.assertFalse(guard.remote_ready(cfg))
        cfg = self.approved_cfg(); cfg['assurance_policy']['approval_ref'] = '__CONFIGURE__'
        self.assertFalse(guard.remote_ready(cfg))

    def test_guard_protects_v05_policy_docs(self):
        cwd = str(REPO)
        for rel in (
            'docs/agentic/ASSURANCE.md', 'docs/agentic/TRACEABILITY.md',
            'docs/agentic/MIGRATION.md', 'docs/agentic/CONTROLS.md',
        ):
            self.assertTrue(guard.protected_path(rel, cwd), rel)

    def test_guard_accepts_direct_reviewed_contract_helper(self):
        cfg = self.approved_cfg()
        payload = {'tool_name':'Bash','tool_input':{'command':'python tools/agentic/contract.py precheck .agentic/local/contracts/dev-a-S1.json'},'cwd':str(REPO)}
        decision, _ = guard.inspect(payload, cfg)
        self.assertIsNone(decision)

    def test_guard_accepts_direct_reviewed_assurance_helper(self):
        cfg = self.approved_cfg()
        payload = {'tool_name':'Bash','tool_input':{'command':'python tools/agentic/assurance.py status --task .agentic/local/tasks/dev-a-S1.json'},'cwd':str(REPO)}
        decision, _ = guard.inspect(payload, cfg)
        self.assertIsNone(decision)

    def test_guard_blocks_helper_shell_chaining(self):
        cfg = self.approved_cfg()
        payload = {'tool_name':'Bash','tool_input':{'command':'python tools/agentic/assurance.py status --task x.json && echo bypass'},'cwd':str(REPO)}
        decision, _ = guard.inspect(payload, cfg)
        self.assertEqual('deny', decision)

    def test_project_migration_apply_requires_human_review(self):
        cfg = self.approved_cfg()
        payload = {'tool_name':'Bash','tool_input':{'command':'python tools/agentic/contract.py project-migrate-v04 .agentic/project.json --apply'},'cwd':str(REPO)}
        decision, reason = guard.inspect(payload, cfg)
        self.assertEqual('ask', decision)
        self.assertIn('maintainer', reason.lower())

    def test_no_specialist_assurance_engines(self):
        forbidden = {'security.py','architecture.py','human_understanding.py','human-understanding.py'}
        present = {p.name for p in HERE.glob('*.py')}
        self.assertFalse(forbidden & present)

    def test_control_plane_and_start_skill_are_final_rc_not_phase11_checkpoint(self):
        texts = [
            (REPO/'.claude/agents/ae-control-plane.md').read_text(encoding='utf-8'),
            (REPO/'.claude/skills/ae-start/SKILL.md').read_text(encoding='utf-8'),
            (REPO/'docs/agentic/README.md').read_text(encoding='utf-8'),
        ]
        for text in texts:
            self.assertNotIn('through Phase 11', text)
            self.assertNotIn('implementation checkpoint — through Phase 11', text)

    def test_control_plane_preserves_release_boundary(self):
        text = (REPO/'.claude/agents/ae-control-plane.md').read_text(encoding='utf-8')
        self.assertIn('BUILD COMPLETE is not deployment/release/migration permission', text)

    def test_new_build_rule_is_schema3_and_legacy_is_explicit_exception(self):
        text = (REPO/'.claude/agents/ae-control-plane.md').read_text(encoding='utf-8')
        self.assertIn('every new task is schema 3', text)
        self.assertIn('schema-2 Story is a legacy exception', text)
        self.assertIn('Do not fabricate contracts/assurance', text)

    def test_cli_help_smoke_for_all_runtime_helpers(self):
        scripts = ('ae.py','pool.py','contract.py','assurance.py','merge.py','cleanup.py')
        for script in scripts:
            p = subprocess.run([sys.executable, str(HERE/script), '--help'], cwd=REPO, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, p.returncode, f'{script}: {p.stderr}')
            self.assertIn('usage:', p.stdout.lower())

    def test_installer_source_contains_final_helper_permissions(self):
        # Installer copies/merges repo/.claude/settings.json, so source permissions are the installation contract.
        settings = json.loads((REPO/'.claude/settings.json').read_text(encoding='utf-8'))
        allow = '\n'.join(settings['permissions']['allow'])
        self.assertIn('contract.py', allow)
        self.assertIn('assurance.py', allow)

    def test_fast_path_template_has_no_blanket_assurance_gates(self):
        policy = self.cfg['assurance_policy']
        self.assertEqual([], policy['project_required_gates'])
        self.assertEqual({'1': [], '2': [], '3': []}, policy['tier_required_gates'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
