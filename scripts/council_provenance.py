"""Input identity and completion evidence for host-orchestrated Council sessions.

Hashes establish applicability and artifact integrity, not independent attestation
of an agent's claims. This module never posts to GitHub or launches agents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = 1
INPUTS = 'input-manifest.json'
PLAN = 'completion-requirements.json'
PROFILE = 'repository-instructions.md'
SHA = re.compile(r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
ENVIRONMENTS = {'local', 'ci', 'deployed', 'browser'}
TRUEY_INSTRUCTIONS = (
    'AGENTS.md', 'DOCTRINE.md', 'TESTING.md', 'openspec/AGENTS.md',
    '.agents/skills/effective-coder/SKILL.md', '.agents/skills/unslop/SKILL.md',
    '.agents/skills/codex-review/SKILL.md', '.agents/skills/verify-changes/SKILL.md',
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def write_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.evidence-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative(value):
    path = Path(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value:
        raise ValueError(f'expected a relative path: {value}')
    return path.as_posix()


def file_bytes(root, name):
    path = root
    for part in Path(relative(name)).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f'symlink input is unsupported: {name}')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f'not a regular file: {name}')
    data = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode) != (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode):
        raise ValueError(f'file changed while reading: {name}')
    return data


def file_hash(root, name):
    data = file_bytes(root, name)
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'executable': bool((root / name).stat().st_mode & 0o111)}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError(f'Git inspection failed: {args[0]}')
    return result.stdout


def inventory(root, scopes):
    git(root, 'rev-parse', '--show-toplevel')
    names = set(os.fsdecode(x) for x in git(root, 'ls-files', '-z', '--cached',
                                           '--others', '--exclude-standard').split(b'\0') if x)
    selected = sorted(name for name in names if any(
        scope == '.' or name == scope or name.startswith(scope + '/') for scope in scopes))
    files = {}
    for name in selected:
        if any(part in {'.git', '.env', '.npmrc', '.netrc', 'id_rsa', 'id_ed25519'}
               or part.startswith('.env.') for part in Path(name).parts):
            raise ValueError(f'sensitive path selected; narrow the scope: {name}')
        if (root / name).is_symlink():
            raise ValueError(f'symlink input is unsupported: {name}')
        if not (root / name).exists():
            continue  # A tracked deletion is represented by absence from files.
        files[name] = file_hash(root, name)
    index = git(root, 'ls-files', '--stage', '-z').split(b'\0')
    relevant = [x for x in index if x and any(
        scope == '.' or os.fsdecode(x.split(b'\t', 1)[-1]) == scope or
        os.fsdecode(x.split(b'\t', 1)[-1]).startswith(scope + '/') for scope in scopes)]
    return {'head': git(root, 'rev-parse', 'HEAD').decode().strip(),
            'files': files, 'index_sha256': hashlib.sha256(b'\0'.join(relevant)).hexdigest()}


def required_contributions(session):
    meta = read_json(session / 'session.json')
    if not isinstance(meta, dict):
        raise ValueError('session metadata must be an object')
    inputs = read_json(session / 'prompts/synthesis-inputs.json')['inputs']
    names = []
    for group in ('members', 'reviewers', 'evidence_runners'):
        names.extend(inputs.get(group, []))
    if not meta.get('roles') or not names:
        raise ValueError('session roster is empty')
    return names + ['final.md']


def validate_requirements(plan):
    if not isinstance(plan, dict) or set(plan) != {'goal', 'criteria'}:
        raise ValueError('requirements need goal and criteria')
    if plan['goal'] not in {'review', 'implementation'} or not isinstance(plan['criteria'], list) or not plan['criteria']:
        raise ValueError('requirements need a goal and nonempty criteria')
    ids = set()
    for criterion in plan['criteria']:
        if not isinstance(criterion, dict) or not {'id', 'description', 'environment'}.issubset(criterion) or set(criterion) - {'id', 'description', 'environment', 'target_url', 'deployment_revision'}:
            raise ValueError('each criterion needs id, description and environment')
        if not all(isinstance(criterion[k], str) and criterion[k].strip() for k in criterion):
            raise ValueError('criterion fields must be nonempty strings')
        if criterion['environment'] not in ENVIRONMENTS or criterion['id'] in ids:
            raise ValueError('invalid environment or duplicate criterion id')
        if criterion['environment'] in {'deployed', 'browser'}:
            target = urlsplit(criterion.get('target_url', ''))
            if target.scheme not in {'http', 'https'} or not target.hostname or target.username or target.password:
                raise ValueError('browser/deployed criteria need an explicit target_url without credentials')
        ids.add(criterion['id'])
    return plan


def instruction_names(root, scopes, explicit, profile):
    names = set(relative(x) for x in explicit)
    if profile == 'truey':
        names.update(TRUEY_INSTRUCTIONS)
    names.update(name for name in ('AGENTS.md', 'DOCTRINE.md') if (root / name).is_file())
    # Include nested instruction owners for every selected source file.
    for name in inventory(root, scopes)['files']:
        for parent in Path(name).parents:
            candidate = (parent / 'AGENTS.md').as_posix()
            if (root / candidate).is_file():
                names.add(candidate)
    return sorted(names)


def current_inputs(session, manifest):
    root = Path(manifest['workspace_root'])
    if not root.is_dir():
        raise ValueError('source workspace is unavailable')
    names = instruction_names(root, manifest['scopes'], manifest['explicit_instruction_paths'], manifest['profile'])
    return {'source': inventory(root, manifest['scopes']),
            'instructions': {name: file_hash(root, name) for name in names},
            'session_inputs': {name: file_hash(session, name) for name in sorted(set(manifest['session_paths']) |
                {path.relative_to(session).as_posix() for path in (session / 'prompts').rglob('*') if path.is_file()})}}


def load_manifest(session):
    value = read_json(session / INPUTS)
    if not isinstance(value, dict) or value.get('schema_version') != SCHEMA:
        raise ValueError('unsupported input manifest')
    if value.get('snapshot_id') != digest({k: v for k, v in value.items() if k != 'snapshot_id'}):
        raise ValueError('input manifest integrity mismatch')
    return value


def bind_inputs(session, scopes, instructions, profile, requirements, pr=None):
    session = session.resolve(strict=True)
    if (session / INPUTS).exists():
        raise ValueError('session is already bound; start a new session')
    metadata = read_json(session / 'session.json')
    if not isinstance(metadata, dict):
        raise ValueError('session metadata must be an object')
    root = Path(metadata['workspace_root']).resolve(strict=True)
    if session == root or root in session.parents:
        raise ValueError('session artifacts must be outside the source workspace')
    scopes = sorted(set(relative(x) for x in scopes))
    if not scopes:
        raise ValueError('at least one input scope is required')
    if pr is not None:
        if set(pr) != {'url', 'base', 'head'} or not all(isinstance(pr[k], str) for k in pr):
            raise ValueError('PR identity needs url, base and head')
        if not re.fullmatch(r'https://github\.com/[^/]+/[^/]+/pull/[0-9]+', pr['url']) or not SHA.fullmatch(pr['base']) or not SHA.fullmatch(pr['head']):
            raise ValueError('invalid PR URL or full commit SHA')
    plan = validate_requirements(requirements)
    names = instruction_names(root, scopes, instructions, profile)
    instruction_data = {name: file_bytes(root, name) for name in names}
    bundle = '\n\n'.join(f'## {name}\n\n' + data.decode('utf-8') for name, data in instruction_data.items())
    if not bundle:
        bundle = 'No repository instruction files were found in the selected scope.\n'
    (session / PROFILE).write_text(bundle, encoding='utf-8')
    (session / PROFILE).chmod(0o600)
    write_json(session / PLAN, plan)
    contributions = required_contributions(session)
    for prompt in (session / 'prompts').rglob('*.md'):
        marker = '\n\n## Bound repository instructions\n'
        original = prompt.read_text(encoding='utf-8').split(marker)[0]
        prompt.write_text(original + marker + f'Read {session / PROFILE} before answering. '
                          'It contains the repository rules and selected contracts. '
                          'Repository rules take precedence over this generic workflow.\n', encoding='utf-8')
    paths = ['session.json', 'brief.md', PROFILE, PLAN]
    paths += [p.relative_to(session).as_posix() for p in sorted((session / 'prompts').rglob('*')) if p.is_file()]
    manifest = {'schema_version': SCHEMA, 'workspace_root': str(root), 'scopes': scopes,
                'repository': git(root, 'rev-parse', '--show-toplevel').decode().strip(),
                'profile': profile, 'instruction_paths': names,
                'explicit_instruction_paths': instructions, 'session_paths': paths,
                'required_contributions': contributions, 'pr': pr}
    before = current_inputs(session, manifest)
    if any(before['instructions'][name]['sha256'] != hashlib.sha256(data).hexdigest()
           for name, data in instruction_data.items()):
        raise ValueError('instructions changed while preparing the dispatch bundle')
    if before != current_inputs(session, manifest):
        raise ValueError('inputs changed during binding; retry after writers stop')
    manifest['inputs'] = before
    manifest['snapshot_id'] = digest(manifest)
    write_json(session / INPUTS, manifest)
    return manifest


def freshness(session, current_pr_head=None):
    try:
        manifest = load_manifest(session)
        current = current_inputs(session, manifest)
        changed = []
        for group in ('instructions', 'session_inputs'):
            changed.extend(f'{group}:{name}' for name in sorted(set(manifest['inputs'][group]) | set(current[group]))
                           if current[group].get(name) != manifest['inputs'][group].get(name))
        old, new = manifest['inputs']['source'], current['source']
        changed.extend(name for name in sorted(set(old['files']) | set(new['files']))
                       if old['files'].get(name) != new['files'].get(name))
        if old['head'] != new['head'] or old['index_sha256'] != new['index_sha256']:
            changed.append('git:head-or-selected-index')
        pr = manifest['pr']
        if pr and current_pr_head and current_pr_head != pr['head']:
            changed.append('github:pr-head')
        if changed:
            return {'state': 'stale', 'changed': changed}
        if pr and not current_pr_head:
            return {'state': 'unknown', 'reason': 'current PR head must be supplied; no remote lookup performed'}
        return {'state': 'unchanged', 'changed': [], 'scope': 'captured local inputs and supplied PR head only'}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'state': 'unknown', 'reason': str(exc)}


def evidence_record(session, record, *, persist=True):
    manifest = load_manifest(session)
    required = {'kind', 'id', 'result', 'environment', 'observation', 'summary', 'artifacts'}
    if not isinstance(record, dict) or not required.issubset(record) or set(record) - required - {'command', 'exit_code', 'pr_head', 'deployment_revision', 'target_url'}:
        raise ValueError('invalid evidence fields')
    if not isinstance(record['id'], str) or not record['id'].strip():
        raise ValueError('evidence id must be a nonempty string')
    if record['kind'] not in {'contribution', 'criterion'} or record['result'] not in {'PASS', 'FAIL', 'UNKNOWN'}:
        raise ValueError('invalid evidence kind or result')
    if record['environment'] not in ENVIRONMENTS or record['observation'] not in {'host-tool', 'command', 'model-claim'}:
        raise ValueError('invalid evidence environment or observation')
    if not isinstance(record['summary'], str) or not record['summary'].strip():
        raise ValueError('evidence summary is required')
    if record['observation'] == 'command':
        if not isinstance(record.get('command'), list) or not record['command'] or not all(isinstance(x, str) for x in record['command']) or type(record.get('exit_code')) is not int:
            raise ValueError('command evidence needs argv and an integer exit code')
        if record['result'] == 'PASS' and record['exit_code'] != 0:
            raise ValueError('nonzero command cannot be PASS')
    if record['kind'] == 'contribution':
        if record['id'] not in manifest['required_contributions'] or record['environment'] != 'local':
            raise ValueError('contribution is outside the session roster')
    else:
        criterion = next((x for x in read_json(session / PLAN)['criteria'] if x['id'] == record['id']), None)
        if criterion is None or criterion['environment'] != record['environment']:
            raise ValueError('criterion environment must match the bound requirements')
    artifacts = record['artifacts']
    if not isinstance(artifacts, list) or not artifacts or not all(isinstance(x, str) for x in artifacts):
        raise ValueError('at least one session-relative artifact is required')
    if record['kind'] == 'contribution' and record['id'] not in artifacts:
        raise ValueError('contribution must include its actual output artifact')
    if record['environment'] == 'ci':
        if manifest['pr'] is None or record.get('pr_head') != manifest['pr']['head']:
            raise ValueError('CI evidence must match the bound PR head')
    if record['kind'] == 'criterion':
        for key in ('target_url', 'deployment_revision'):
            if criterion.get(key) is not None and record.get(key) != criterion[key]:
                raise ValueError(f'evidence {key} must match the bound criterion')
    if record['environment'] in {'deployed', 'browser'}:
        if not isinstance(record.get('target_url'), str) or not record['target_url'].startswith(('http://', 'https://')):
            raise ValueError('browser/deployment evidence needs a target URL')
        if record['environment'] == 'deployed' and record['result'] == 'PASS' and not record.get('deployment_revision'):
            raise ValueError('deployed evidence needs a deployment revision')
        target = urlsplit(record['target_url'])
        if record['result'] == 'PASS' and (record['environment'] == 'deployed' or target.hostname not in {'localhost', '127.0.0.1', '::1'}) and not criterion.get('deployment_revision'):
            raise ValueError('hosted acceptance without an expected deployment revision must remain UNKNOWN')
    if not persist:
        return record
    value = {**record, 'schema_version': SCHEMA, 'snapshot_id': manifest['snapshot_id'],
             'artifact_hashes': {name: file_hash(session, name) for name in artifacts},
             'recorded_at': datetime.now(timezone.utc).isoformat()}
    value['record_id'] = digest(value)
    directory = session / 'evidence-records'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (value['record_id'] + '.json')
    write_json(path, value)
    return value


def validate_completion(session, current_pr_head=None):
    state = freshness(session, current_pr_head)
    problems = []
    criteria_results = []
    try:
        manifest = load_manifest(session)
        plan = validate_requirements(read_json(session / PLAN))
        try:
            from .codex_council import validate_session
        except ImportError:
            from codex_council import validate_session
        if not isinstance(read_json(session / 'session.json'), dict):
            raise ValueError('session metadata must be an object')
        problems.extend(validate_session(session)['problems'])
        if state['state'] != 'unchanged':
            problems.append('inputs are ' + state['state'])
        records = []
        for path in sorted((session / 'evidence-records').glob('*.json')):
            record = read_json(path)
            if not isinstance(record, dict):
                raise ValueError('evidence record must be an object')
            if record.get('schema_version') != SCHEMA:
                raise ValueError('unsupported evidence schema')
            if record.get('record_id') != digest({k: v for k, v in record.items() if k != 'record_id'}):
                raise ValueError('evidence record integrity mismatch')
            if record['snapshot_id'] != manifest['snapshot_id']:
                raise ValueError('evidence belongs to different inputs')
            if not isinstance(record.get('artifact_hashes'), dict):
                raise ValueError('evidence artifact hashes must be an object')
            if any(file_hash(session, name) != value for name, value in record['artifact_hashes'].items()):
                raise ValueError('evidence artifact changed')
            payload = {k: v for k, v in record.items() if k not in {
                'schema_version', 'snapshot_id', 'artifact_hashes', 'recorded_at', 'record_id'}}
            evidence_record(session, payload, persist=False)
            if set(record['artifact_hashes']) != set(record['artifacts']):
                raise ValueError('evidence artifact inventory mismatch')
            records.append(record)
        targets = [('contribution', name, 'local') for name in manifest['required_contributions']]
        targets += [('criterion', item['id'], item['environment']) for item in plan['criteria']]
        for kind, name, environment in targets:
            matches = [r for r in records if (r['kind'], r['id'], r['environment']) == (kind, name, environment)]
            result = 'UNKNOWN'
            if matches:
                # A later pass must not silently erase a failure; resolve it in a fresh session.
                if any(r['result'] == 'FAIL' for r in matches):
                    result = 'FAIL'
                elif all(r['result'] == 'PASS' and r['observation'] != 'model-claim' and r['artifacts'] for r in matches):
                    result = 'PASS'
            if kind == 'contribution' and matches:
                text = file_bytes(session, name).decode('utf-8')
                body = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
                if not body:
                    result = 'UNKNOWN'
            criteria_results.append({'kind': kind, 'id': name, 'environment': environment, 'result': result})
            if result != 'PASS':
                problems.append(f'{kind} {name}: {result}')
        return {'complete': not problems, 'goal': plan['goal'], 'freshness': state,
                'results': criteria_results, 'problems': problems,
                'assurance': 'artifact integrity and recorded observations; not independent execution attestation'}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'complete': False, 'freshness': state, 'results': criteria_results,
                'problems': problems + [str(exc)]}


def add_parsers(subparsers):
    for name in ('bind-inputs', 'freshness', 'record-evidence', 'run-check', 'validate-completion', 'handoff'):
        parser = subparsers.add_parser(name, help=__doc__.splitlines()[0])
        parser.add_argument('--session', type=Path, required=True)
        if name == 'bind-inputs':
            parser.add_argument('--include', action='append', required=True)
            parser.add_argument('--instruction', action='append', default=[])
            parser.add_argument('--repository-profile', choices=('generic', 'truey'), default='generic')
            parser.add_argument('--requirements', type=Path, required=True)
            parser.add_argument('--pr', type=Path, help='JSON with url, base and head')
        elif name == 'record-evidence':
            parser.add_argument('--input', type=Path, required=True)
        elif name == 'run-check':
            parser.add_argument('--criterion', required=True)
            parser.add_argument('argv', nargs=argparse.REMAINDER)
        else:
            parser.add_argument('--current-pr-head')


def handle(args):
    session = args.session.resolve(strict=True)
    if args.command == 'bind-inputs':
        result = bind_inputs(session, args.include, args.instruction, args.repository_profile,
                             read_json(args.requirements), read_json(args.pr) if args.pr else None)
    elif args.command == 'freshness':
        result = freshness(session, args.current_pr_head)
    elif args.command == 'record-evidence':
        result = evidence_record(session, read_json(args.input))
    elif args.command == 'run-check':
        manifest = load_manifest(session)
        argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        criterion = next((x for x in read_json(session / PLAN)['criteria'] if x['id'] == args.criterion), None)
        if not argv or criterion is None or criterion['environment'] != 'local':
            raise ValueError('run-check requires a bound local criterion and an argv after --')
        if freshness(session, manifest['pr']['head'] if manifest['pr'] else None)['state'] != 'unchanged':
            raise ValueError('inputs changed before check')
        directory = session / 'check-outputs'
        directory.mkdir(mode=0o700, exist_ok=True)
        fd, output = tempfile.mkstemp(dir=directory, suffix='.log')
        with os.fdopen(fd, 'wb') as stream:
            process = subprocess.run(argv, cwd=manifest['workspace_root'], stdout=stream, stderr=subprocess.STDOUT)
        result = evidence_record(session, {'kind': 'criterion', 'id': args.criterion,
            'result': 'PASS' if process.returncode == 0 else 'FAIL', 'environment': 'local',
            'observation': 'command', 'summary': 'Executed by run-check', 'command': argv,
            'exit_code': process.returncode, 'artifacts': [Path(output).relative_to(session).as_posix()]})
    else:
        result = validate_completion(session, args.current_pr_head)
        if args.command == 'handoff':
            result['posting'] = 'none; report only'
            try:
                result['pr'] = load_manifest(session)['pr']
            except (OSError, ValueError, KeyError, TypeError):
                result['pr'] = None
    print(json.dumps(result, indent=2))
    if args.command == 'freshness':
        return 0 if result['state'] == 'unchanged' else 1
    if args.command in {'validate-completion', 'handoff'}:
        return 0 if result['complete'] else 1
    if args.command == 'run-check':
        return 0 if result['result'] == 'PASS' else 1
    return 0
