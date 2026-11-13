import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import codex_council as cc, council_provenance as p


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'app.py').write_text('value = 1\n')
        (self.root / 'AGENTS.md').write_text('Preserve source ownership.\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        self.session = cc.init_session('Bound review', self.root, effort='simple',
            session_root=self.base / 'sessions', config_root=self.base / 'config')
        self.plan = {'goal': 'review', 'criteria': [
            {'id': 'source', 'description': 'Inspect current source', 'environment': 'local'}]}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True).stdout

    def bind(self, scopes=None, profile='generic', pr=None):
        return p.bind_inputs(self.session, scopes or ['app.py'], [], profile, self.plan, pr)

    def record(self, kind='criterion', identifier='source', **kwargs):
        artifact = self.session / 'observation.txt'
        artifact.write_text('Observed value = 1 in current source.\n')
        return p.evidence_record(self.session, {
            'kind': kind, 'id': identifier, 'result': 'PASS', 'environment': 'local',
            'observation': 'host-tool', 'summary': 'Read the current artifact',
            'artifacts': [artifact.name], **kwargs})

    def finish(self):
        manifest = p.load_manifest(self.session)
        for name in manifest['required_contributions']:
            path = self.session / name
            path.write_text(path.read_text() + '\nInspected current source; no blocking defect found.\n')
            self.record('contribution', name, artifacts=[name])
        self.record()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(Path(cc.__file__)), *map(str, args)],
                              capture_output=True, text=True)

    def test_working_bytes_and_instruction_changes_invalidate_review(self):
        (self.root / 'app.py').write_text('value = 2\n')
        value = self.bind()
        self.assertEqual(value['inputs']['source']['head'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(p.freshness(self.session)['state'], 'unchanged')
        (self.root / 'app.py').write_text('value = 3\n')
        self.assertEqual(p.freshness(self.session)['changed'], ['app.py'])
        (self.root / 'app.py').write_text('value = 2\n')
        (self.root / 'AGENTS.md').write_text('Changed owner constraints.\n')
        self.assertEqual(p.freshness(self.session)['state'], 'stale')

    def test_directory_scope_tracks_additions_deletions_and_not_excluded_changes(self):
        self.bind(['.'])
        (self.root / 'new.py').write_text('new = True\n')
        self.assertEqual(p.freshness(self.session)['changed'], ['new.py'])
        (self.root / 'new.py').unlink()
        (self.root / 'app.py').unlink()
        self.assertEqual(p.freshness(self.session)['changed'], ['app.py'])

    def test_unrelated_change_is_outside_scope_but_head_change_is_stale(self):
        self.bind()
        (self.root / 'other.py').write_text('other = 2\n')
        self.git('add', 'other.py')
        self.assertEqual(p.freshness(self.session)['state'], 'unchanged')
        self.git('commit', '-qm', 'another commit')
        self.assertEqual(p.freshness(self.session)['state'], 'stale')

    def test_source_missing_or_manifest_changed_is_unknown(self):
        self.bind()
        self.root.rename(self.base / 'moved')
        self.assertEqual(p.freshness(self.session)['state'], 'unknown')
        data = p.read_json(self.session / p.INPUTS)
        data['scopes'] = ['.']
        p.write_json(self.session / p.INPUTS, data)
        self.assertEqual(p.freshness(self.session)['reason'], 'input manifest integrity mismatch')

    def test_changed_context_or_requirements_invalidates_completion(self):
        self.bind()
        self.finish()
        self.assertEqual(p.validate_completion(self.session)['complete'], True)
        (self.session / 'brief.md').write_text('A different question')
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertIn('inputs are stale', result['problems'])

    def test_scaffold_is_structurally_valid_but_not_complete(self):
        self.bind()
        self.assertEqual(cc.validate_session(self.session)['ok'], True)
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertEqual({x['result'] for x in result['results']}, {'UNKNOWN'})

    def test_model_claim_and_empty_contribution_cannot_complete_review(self):
        self.bind()
        for name in p.load_manifest(self.session)['required_contributions']:
            self.record('contribution', name, artifacts=[name])
        self.record(observation='model-claim')
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertEqual({x['result'] for x in result['results']}, {'UNKNOWN'})

    def test_modified_evidence_artifact_is_rejected(self):
        self.bind()
        self.finish()
        (self.session / 'observation.txt').write_text('Replacement evidence')
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertIn('evidence artifact changed', result['problems'])

    def test_failure_is_not_erased_by_a_later_pass(self):
        self.bind()
        self.finish()
        self.record(result='FAIL')
        self.record()
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertEqual(result['results'][-1]['result'], 'FAIL')

    def test_pr_head_must_be_supplied_and_match(self):
        self.bind(pr={'url': 'https://github.com/example/repo/pull/1', 'base': 'a'*40, 'head': 'b'*40})
        self.assertEqual(p.freshness(self.session)['state'], 'unknown')
        self.assertEqual(p.freshness(self.session, 'b'*40)['state'], 'unchanged')
        self.assertEqual(p.freshness(self.session, 'c'*40)['changed'], ['github:pr-head'])
        self.finish()
        self.assertEqual(p.validate_completion(self.session, 'c'*40)['complete'], False)

    def test_ci_cannot_replace_local_acceptance_or_use_other_commit(self):
        self.bind(pr={'url': 'https://github.com/example/repo/pull/1', 'base': 'a'*40, 'head': 'b'*40})
        with self.assertRaisesRegex(ValueError, 'environment must match'):
            self.record(environment='ci', pr_head='b'*40)

    def test_new_ignored_nested_instruction_owner_invalidates_evidence(self):
        directory = self.root / 'feature'
        directory.mkdir()
        (directory / 'app.py').write_text('value = 1')
        (self.root / '.gitignore').write_text('feature/AGENTS.md\n')
        self.bind(['feature'])
        (directory / 'AGENTS.md').write_text('New instruction applies to this source')
        self.assertEqual(p.freshness(self.session)['changed'], ['instructions:feature/AGENTS.md'])

    def test_malformed_evidence_and_metadata_are_incomplete_not_tracebacks(self):
        self.bind()
        self.finish()
        path = next((self.session / 'evidence-records').glob('*.json'))
        value = p.read_json(path)
        value['artifact_hashes'] = []
        value['record_id'] = p.digest({k: v for k, v in value.items() if k != 'record_id'})
        p.write_json(path, value)
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertIn('evidence artifact hashes must be an object', result['problems'])
        (self.session / 'session.json').write_text('[]')
        result = self.cli('validate-completion', '--session', self.session)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('session metadata must be an object', json.loads(result.stdout)['problems'])

    def test_ci_is_bound_to_the_exact_pr_head(self):
        self.plan['criteria'][0]['environment'] = 'ci'
        self.bind(pr={'url': 'https://github.com/example/repo/pull/1', 'base': 'a'*40, 'head': 'b'*40})
        with self.assertRaisesRegex(ValueError, 'bound PR head'):
            self.record(environment='ci', pr_head='c'*40)
        good = self.record(environment='ci', pr_head='b'*40)
        self.assertEqual(good['pr_head'], 'b'*40)

    def test_browser_target_and_revision_must_match_the_requirement(self):
        self.plan['criteria'][0].update(environment='browser', target_url='https://dev.example.test', deployment_revision='release-17')
        self.bind()
        with self.assertRaisesRegex(ValueError, 'target_url must match'):
            self.record(environment='browser', target_url='http://localhost:3000', deployment_revision='release-17')
        with self.assertRaisesRegex(ValueError, 'deployment_revision must match'):
            self.record(environment='browser', target_url='https://dev.example.test', deployment_revision='release-16')
        value = self.record(environment='browser', target_url='https://dev.example.test', deployment_revision='release-17')
        self.assertEqual(value['result'], 'PASS')

    def test_missing_deployment_identity_retains_unknown_without_claiming_pass(self):
        self.plan['criteria'][0].update(environment='deployed', target_url='https://dev.example.test')
        self.bind()
        with self.assertRaisesRegex(ValueError, 'deployment revision'):
            self.record(environment='deployed', target_url='https://dev.example.test')
        value = self.record(environment='deployed', target_url='https://dev.example.test', result='UNKNOWN')
        self.assertEqual(value['result'], 'UNKNOWN')
        result = p.validate_completion(self.session)
        self.assertEqual(result['results'][-1]['result'], 'UNKNOWN')
        self.assertEqual(result['complete'], False)

    def test_localhost_browser_evidence_is_limited_to_its_bound_target(self):
        self.plan['criteria'][0].update(environment='browser', target_url='http://localhost:3000')
        self.bind()
        value = self.record(environment='browser', target_url='http://localhost:3000')
        self.assertEqual(value['environment'], 'browser')
        self.assertNotIn('deployment_revision', value)

    def test_nested_instructions_reach_saved_dispatch_prompts(self):
        directory = self.root / 'feature'
        directory.mkdir()
        (directory / 'app.py').write_text('value = 1')
        (directory / 'AGENTS.md').write_text('Nested ownership rule')
        self.bind(['feature'])
        self.assertIn('Nested ownership rule', (self.session / p.PROFILE).read_text())
        for prompt in (self.session / 'prompts').rglob('*.md'):
            self.assertIn(str(self.session / p.PROFILE), prompt.read_text())
        (self.session / 'prompts' / 'extra.md').write_text('Additional dispatch context')
        self.assertEqual(p.freshness(self.session)['state'], 'stale')

    def test_semantically_invalid_persisted_evidence_cannot_pass(self):
        self.bind()
        self.finish()
        path = next((self.session / 'evidence-records').glob('*.json'))
        value = p.read_json(path)
        value['result'] = 'SUCCESS'
        value['record_id'] = p.digest({k: v for k, v in value.items() if k != 'record_id'})
        p.write_json(path, value)
        result = p.validate_completion(self.session)
        self.assertEqual(result['complete'], False)
        self.assertIn('invalid evidence kind or result', result['problems'])

    def test_truey_profile_reads_real_instructions_and_requires_missing_files(self):
        with self.assertRaises(FileNotFoundError):
            self.bind(profile='truey')
        for name in p.TRUEY_INSTRUCTIONS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'Instruction from {name}\n')
        self.bind(profile='truey')
        bundle = (self.session / p.PROFILE).read_text()
        self.assertIn('Instruction from DOCTRINE.md', bundle)
        self.assertIn('Instruction from .agents/skills/verify-changes/SKILL.md', bundle)

    def test_sensitive_paths_and_symlinks_are_refused(self):
        (self.root / '.env').write_text('SECRET=fixture')
        with self.assertRaisesRegex(ValueError, 'sensitive path'):
            self.bind(['.'])
        (self.root / '.env').unlink()
        (self.root / 'linked.py').symlink_to(self.root / 'app.py')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.bind(['.'])

    def test_bad_requirements_and_path_escape_are_refused(self):
        self.plan['criteria'][0]['environment'] = 'imagined'
        with self.assertRaisesRegex(ValueError, 'environment'):
            self.bind()
        self.plan['criteria'][0]['environment'] = 'local'
        with self.assertRaisesRegex(ValueError, 'relative path'):
            self.bind(['../outside'])

    def test_cli_local_command_records_observed_exit_and_handoff(self):
        self.bind()
        good = self.cli('run-check', '--session', self.session, '--criterion', 'source',
                        '--', sys.executable, '-c', 'print("actual output")')
        self.assertEqual(good.returncode, 0, good.stderr)
        evidence = json.loads(good.stdout)
        self.assertEqual(evidence['exit_code'], 0)
        self.assertEqual((self.session / evidence['artifacts'][0]).read_text(), 'actual output\n')
        self.finish()
        handoff = self.cli('handoff', '--session', self.session)
        self.assertEqual(handoff.returncode, 0, handoff.stderr)
        self.assertEqual(json.loads(handoff.stdout)['posting'], 'none; report only')
        bad = self.cli('run-check', '--session', self.session, '--criterion', 'source',
                       '--', sys.executable, '-c', 'raise SystemExit(7)')
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertEqual(json.loads(bad.stdout)['exit_code'], 7)
        self.assertEqual(self.cli('validate-completion', '--session', self.session).returncode, 1)

    def test_cli_unbound_and_invalid_json_return_actionable_errors(self):
        result = self.cli('validate-completion', '--session', self.session)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['complete'], False)
        path = self.base / 'requirements.json'
        path.write_text('{"goal":"review", "goal":"implementation", "criteria":[]}')
        result = self.cli('bind-inputs', '--session', self.session, '--include', 'app.py', '--requirements', path)
        self.assertEqual(result.returncode, 2)
        self.assertIn('duplicate JSON key', result.stderr)


if __name__ == '__main__':
    unittest.main()
