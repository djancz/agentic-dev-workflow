"""Behaviour tests for the wf sec audit scripts: the sandbox runner, evidence promotion and the audit tool.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'skills' / 'wf-sec-audit' / 'scripts'
SANDBOX = SCRIPTS / 'sandbox-run.py'

sys.dont_write_bytecode = True


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(root: Path) -> tuple[Path, str]:
    """A small target repository with private records, as an installed wf project has them."""
    repo = root / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'app.py').write_text('print("hello from target")\n')
    (repo / 'run.sh').write_text('#!/bin/sh\necho exec-ok\n')
    (repo / 'run.sh').chmod(0o755)
    (repo / 'crlf.txt').write_bytes(b'line\r\n')
    (repo / 'version.txt').write_text('$Format:%H$\n')
    # A target's attributes must not change what the sandbox sees.
    (repo / '.gitattributes').write_text('app.py export-ignore\nversion.txt export-subst\n')
    (repo / '.gitignore').write_text('/development\n')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init')
    (repo / 'development').mkdir()
    (repo / 'development' / 'private.txt').write_text('private record\n')
    return repo, git(repo, 'rev-parse', 'HEAD').strip()


def probe_failure() -> str | None:
    r = subprocess.run([sys.executable, str(SANDBOX), 'probe', '--json'], capture_output=True, text=True)
    if r.returncode == 0:
        return None
    try:
        missing = {k: v for k, v in json.loads(r.stdout).items() if v != 'enforced'}
    except ValueError:
        return r.stderr.strip() or 'probe failed'
    return '; '.join(f'{k}: {v}' for k, v in sorted(missing.items()))


PROBE_FAILURE = probe_failure()


class Promote(unittest.TestCase):
    """The parent's copy of evidence into the artifacts directory; it needs no sandbox."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.scratch = self.tmp / 'runs' / 'scratch' / 'hunt-1'
        self.artifacts = self.tmp / 'agents' / 'hunt-1' / 'artifacts'
        (self.scratch / 'evidence').mkdir(parents=True)
        self.artifacts.mkdir(parents=True)

    def promote(self, *allow: str, total: int = 100000):
        args = [sys.executable, str(SANDBOX), 'promote', '--scratch', str(self.scratch),
                '--artifacts', str(self.artifacts), '--total', str(total)]
        for item in allow:
            args += ['--allow', item]
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return r, [json.loads(line) for line in r.stdout.splitlines()]

    def test_allowlisted_regular_files_are_copied_exactly(self):
        (self.scratch / 'evidence' / 'c01.out').write_bytes(b'observed\n')
        r, entries = self.promote('evidence/c01.out:100', 'evidence/c02.out:100')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(e['path'], e['status']) for e in entries],
                         [('evidence/c01.out', 'promoted'), ('evidence/c02.out', 'missing')])
        copy = self.artifacts / 'evidence' / 'c01.out'
        self.assertEqual(copy.read_bytes(), b'observed\n')
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
        self.assertEqual(entries[0]['bytes'], 9)
        r, entries = self.promote('evidence/c01.out:100')
        self.assertEqual(r.returncode, 1, 'an existing destination is never overwritten')
        self.assertIn('exists already', entries[0]['reason'])

    def test_a_check_that_never_ran_is_missing_not_rejected(self):
        shutil.rmtree(self.scratch / 'evidence')
        r, entries = self.promote('evidence/c01.out:100', 'evidence/c01.json:100')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([e['status'] for e in entries], ['missing', 'missing'])

    def test_unsafe_entries_are_rejected_and_nothing_else_is_copied(self):
        evidence = self.scratch / 'evidence'
        (self.tmp / 'outside.txt').write_text('outside\n')
        (evidence / 'link.out').symlink_to(self.tmp / 'outside.txt')
        (evidence / 'hard.out').write_text('x')
        os.link(evidence / 'hard.out', self.tmp / 'second-link')
        os.mkfifo(evidence / 'fifo.out')
        (evidence / 'big.out').write_bytes(b'x' * 200)
        (self.scratch / 'linked-dir').symlink_to(self.tmp, target_is_directory=True)
        (evidence / 'dir.out').mkdir()
        r, entries = self.promote('evidence/link.out:100', 'evidence/hard.out:100', 'evidence/fifo.out:100',
                                  'evidence/big.out:100', 'linked-dir/outside.txt:100', 'evidence/dir.out:100',
                                  '../outside.txt:100', '/etc/passwd:100', 'evidence/.hidden:100',
                                  'evidence//x:100')
        self.assertEqual(r.returncode, 1)
        reasons = {e['path']: e.get('reason', e['status']) for e in entries}
        self.assertTrue(all(e['status'] == 'rejected' for e in entries), entries)
        self.assertIn('following a link', reasons['evidence/link.out'])
        self.assertIn('2 links', reasons['evidence/hard.out'])
        self.assertIn('not a regular file', reasons['evidence/fifo.out'])
        self.assertIn('exceed its limit', reasons['evidence/big.out'])
        self.assertIn('not a plain directory', reasons['linked-dir/outside.txt'])
        self.assertIn('not a regular file', reasons['evidence/dir.out'])
        for path in ('../outside.txt', '/etc/passwd', 'evidence/.hidden', 'evidence//x'):
            self.assertIn('plain components', reasons[path])
        self.assertEqual(list(self.artifacts.rglob('*.out')), [], 'nothing unsafe reaches the artifacts')

    def test_the_cumulative_limit_counts_every_promoted_file(self):
        for name in ('a', 'b'):
            (self.scratch / 'evidence' / f'{name}.out').write_bytes(b'x' * 60)
        r, entries = self.promote('evidence/a.out:100', 'evidence/b.out:100', total=100)
        self.assertEqual(r.returncode, 1)
        self.assertEqual([e['status'] for e in entries], ['promoted', 'rejected'])
        self.assertIn('left of the total limit', entries[1]['reason'])

    def test_a_running_sandbox_blocks_promotion(self):
        (self.scratch / 'evidence' / 'c01.out').write_text('x')
        fd = os.open(self.scratch, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH)
        r, entries = self.promote('evidence/c01.out:100')
        self.assertEqual(r.returncode, 3)
        self.assertIn('still holds the scratch directory', r.stderr)
        self.assertEqual(entries, [])

    def test_invalid_use_exits_125(self):
        r, _ = self.promote('evidence/c01.out')
        self.assertEqual(r.returncode, 125)
        r = subprocess.run([sys.executable, str(SANDBOX), 'promote', '--scratch', str(self.tmp / 'missing'),
                            '--artifacts', str(self.artifacts), '--allow', 'a:1', '--total', '1'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 125)
        r = subprocess.run([sys.executable, str(SANDBOX), 'frobnicate'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 125)


class SandboxRun(unittest.TestCase):
    """The controls hold inside a real sandbox. Skipped, with the probe's reason, where it cannot run."""

    def setUp(self):
        if PROBE_FAILURE:
            self.skipTest(f'sandbox-run probe: {PROBE_FAILURE}')
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo, self.ref = make_repo(self.tmp)
        self.scratch = self.repo / 'development' / 'audits' / 'a' / 'runs' / 'scratch' / 'hunt-1'
        self.scratch.mkdir(parents=True)

    def run_check(self, check: str, *command: str, options: tuple[str, ...] = ()):
        args = [sys.executable, str(SANDBOX), 'run', '--scratch', str(self.scratch), '--ref', self.ref,
                '--check', check, *options, '--', *command]
        return subprocess.run(args, cwd=self.repo, capture_output=True, text=True, timeout=120)

    def evidence(self, check: str) -> dict:
        return json.loads((self.scratch / 'evidence' / f'{check}.json').read_text())

    def test_the_target_sees_a_clean_read_only_copy_and_nothing_of_the_host(self):
        home = str(Path.home())
        script = ('python3 /src/app.py; ./run.sh; od -An -c crlf.txt; cat version.txt; '
                  'touch /src/x 2>/dev/null || echo src-read-only; '
                  'touch /usr/x 2>/dev/null || echo usr-read-only; '
                  f'[ -e "{self.repo}" ] || echo repo-hidden; [ -e "{home}" ] || echo home-hidden; '
                  '[ -e /src/development ] || echo records-hidden; env | cut -d= -f1 | sort | tr "\\n" " "')
        r = self.run_check('c01', 'sh', '-c', script, options=('--env', 'DUMMY_USER=alice'))
        self.assertEqual(r.returncode, 0, r.stderr)
        for line in ('hello from target', 'exec-ok', '\\r  \\n', '$Format:%H$', 'src-read-only', 'usr-read-only',
                     'repo-hidden', 'home-hidden', 'records-hidden'):
            self.assertIn(line, r.stdout)
        names = set(r.stdout.strip().splitlines()[-1].split())
        self.assertEqual(names - {'PWD', 'SHLVL', '_'}, {'DUMMY_USER', 'HOME', 'LANG', 'PATH', 'TMPDIR'})
        record = self.evidence('c01')
        self.assertEqual((record['status'], record['killed'], record['ref']), (0, None, self.ref))
        self.assertEqual(record['network'], 'loopback only')
        self.assertEqual(record['stdout']['bytes'], len(r.stdout.encode()))
        self.assertEqual((self.scratch / 'evidence' / 'c01.out').read_text(), r.stdout)

    def test_no_network_and_writes_only_to_the_limited_tmpfs(self):
        script = ('python3 -c "import socket; socket.create_connection((\'1.1.1.1\', 80), timeout=3)" '
                  '2>/dev/null || echo no-network; '
                  'cp -r /src /work/src && touch /work/src/built && echo work-writable; '
                  'head -c 3145728 /dev/zero > /work/big 2>/dev/null || echo disk-full')
        r = self.run_check('c02', 'sh', '-c', script, options=('--work-mb', '2'))
        self.assertEqual(r.returncode, 0, r.stderr)
        for line in ('no-network', 'work-writable', 'disk-full'):
            self.assertIn(line, r.stdout)

    def test_limits_end_the_command(self):
        r = self.run_check('c03', 'sleep', '30', options=('--timeout', '2'))
        self.assertEqual(r.returncode, 124)
        self.assertEqual(self.evidence('c03')['killed'], 'timeout')
        r = self.run_check('c04', 'sh', '-c', 'head -c 3145728 /dev/zero > /work/big',
                           options=('--file-mb', '1', '--work-mb', '8'))
        self.assertEqual(r.returncode, 128 + 25, 'SIGXFSZ ends a write past the file-size limit')
        r = self.run_check('c05', 'sh', '-c', 'head -c 5000 /dev/zero | tr "\\0" x', options=('--output-bytes', '100'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.evidence('c05')['stdout'], {**self.evidence('c05')['stdout'], 'bytes': 5000,
                                                          'kept': 100, 'truncated': True})

    def test_the_cgroup_limits_processes_and_memory(self):
        script = 'i=0; while [ $i -lt 30 ]; do sleep 2 & i=$((i+1)); done; wait; echo spawned'
        r = self.run_check('c16', 'sh', '-c', script, options=('--tasks', '10'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Cannot fork', r.stderr)
        self.assertNotIn('spawned', r.stdout)
        r = self.run_check('c18', 'sh', '-c', script, options=('--tasks', '64'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('spawned', r.stdout)
        r = self.run_check('c17', 'python3', '-c', 'x = bytearray(400 * 1024 * 1024); print("allocated")',
                           options=('--memory-mb', '64'))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('allocated', r.stdout)

    def test_refused_calls_run_nothing_and_leave_no_evidence(self):
        cases = [
            ('c06', ('--env', 'GITHUB_TOKEN=x'), 'looks like a credential'),
            ('c07', ('--env', 'HOME=/x'), 'fixed inside the sandbox'),
            ('c08', ('--tool', str(Path.home())), 'must not expose the home directory'),
            ('c09', ('--tool', str(self.repo)), 'must not expose the home directory or the repository'),
            ('c10', ('--timeout', '99999'), 'must be 1-1800'),
        ]
        for check, options, message in cases:
            with self.subTest(check=check):
                r = self.run_check(check, 'true', options=options)
                self.assertEqual(r.returncode, 125)
                self.assertIn(message, r.stderr)
                self.assertIn('nothing ran', r.stderr)
                self.assertFalse((self.scratch / 'evidence' / f'{check}.json').exists())
        r = self.run_check('c11', '/nonexistent-command')
        self.assertEqual(r.returncode, 125, 'a command that cannot start is not a result')
        self.assertIn('did not start', r.stderr)
        r = self.run_check('BAD', 'true')
        self.assertEqual(r.returncode, 125)
        r = subprocess.run([sys.executable, str(SANDBOX), 'run', '--scratch', str(self.scratch), '--ref', 'HEAD',
                            '--check', 'c12', '--', 'true'], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 125)
        self.assertIn('full commit id', r.stderr)
        self.assertEqual(self.run_check('c13', 'true').returncode, 0)
        r = self.run_check('c13', 'true')
        self.assertEqual(r.returncode, 125, 'evidence is never overwritten')
        self.assertIn('already has evidence', r.stderr)

    def test_inputs_are_read_only_and_stay_inside_the_scratch_directory(self):
        (self.scratch / 'inputs').mkdir()
        (self.scratch / 'inputs' / 'fixture.json').write_text('{"user": "alice"}\n')
        r = self.run_check('c14', 'sh', '-c', 'cat /input/fixture.json; touch /input/x 2>/dev/null || echo ro',
                           options=('--inputs', 'inputs'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"alice"', r.stdout)
        self.assertIn('ro', r.stdout)
        r = self.run_check('c15', 'true', options=('--inputs', '../../..'))
        self.assertEqual(r.returncode, 125)

    def test_probe_reports_every_control(self):
        r = subprocess.run([sys.executable, str(SANDBOX), 'probe'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 12)
        self.assertTrue(all(line.startswith('enforced: ') for line in r.stdout.splitlines()))



AUDIT = SCRIPTS / 'audit.py'


def unit_spec(surface: str = 'app.py#main', attack: str = 'Access control') -> dict:
    return {
        'canonical_refs': {'surface': surface, 'boundary': 'app.py#check_owner', 'subsystem': 'app',
                           'attack_class': f'ATTACK-CLASSES.md#{attack}'},
        'surface': f'entry {surface}', 'boundary': 'owner check', 'subsystem': 'app', 'attack_class': attack,
        'starting_paths': ['app.py'],
        'ordinary_attack_class_block': f'ATTACK-CLASSES.md#{attack}',
        'selected_companion_blocks': ['WEB-PROTOCOL-AND-AUTH.md#Core discipline',
                                      'WEB-PROTOCOL-AND-AUTH.md#Browser-session attack classes',
                                      'WEB-PROTOCOL-AND-AUTH.md#Universal moves',
                                      'WEB-PROTOCOL-AND-AUTH.md#Validation rules'],
        'excluded_blocks': [{'block': 'CLIENT-SIDE.md#Core discipline', 'reason': 'no browser code'}],
        'prior_status': 'none',
    }


def step(kind: str = 'entrypoint', line: int = 1) -> dict:
    return {'kind': kind, 'file': 'app.py', 'line': line, 'scope': 'main',
            'description': 'Attacker data reaches the operation.'}


def needs_validation(fp: str = 'app-owner-check-hypothesis') -> dict:
    return {'verdict': 'needs_validation', 'fingerprint': fp, 'title': 'Owner check may be skipped',
            'description': 'A request may change an object without its owner check.',
            'claimed_root_cause': 'main may call the store before check_owner.', 'trace': [step()],
            'evidence': [{'file': 'app.py', 'line': 1, 'description': 'The call has no owner check.'}],
            'blockers': ['The deployed router is not in the repository.'],
            'validation_plan': {'local': 'Run main with a dummy object of another user.'}}


def confirmed(fp: str = 'app-owner-check-hypothesis', observed: str = 'The other user\'s object changed.') -> dict:
    return {'verdict': 'confirmed', 'fingerprint': fp, 'title': 'Missing owner check',
            'description': 'A user changes another user\'s object.',
            'root_cause': 'main calls the store before check_owner.',
            'intended_behavior': 'Only the owner changes an object.', 'trace': [step()],
            'evidence': [{'file': 'app.py', 'line': 1, 'description': 'The call has no owner check.'}],
            'conditions': [],
            'execution': {'attacker_perspective': 'A user with an account.', 'payloads': ['object id 2'],
                          'instructions': ['Call main with the id of another user\'s object.'],
                          'observed_result': observed},
            'remediation': {'strategy': 'Check the owner before the store call.'},
            'severity': {'likelihood': {'score': 'medium', 'reason': 'Reachable.'},
                         'impact': {'score': 'medium', 'reason': 'One object changes.'},
                         'overall_severity': 'medium'},
            'confidence': {'score': 'high', 'reason': 'Reproduced locally.'}}


def source_check(agent: str) -> dict:
    return {'agent_id': agent, 'reviewed_paths': ['app.py'], 'invariant': 'Only the owner changes an object.',
            'method': 'source', 'result': 'main has no owner check.', 'artifact': None}


class AuditTool(unittest.TestCase):
    """The parent's tool: records, budget, prompts and the validation of agent results (no agent runs)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo, self.ref = make_repo(self.tmp)

    def audit(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(AUDIT), *args], cwd=self.repo, capture_output=True, text=True,
                           timeout=120)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def start(self, profile: str = 'quick', budget: int = 12) -> Path:
        run = Path(self.audit('init', '--profile', profile, '--budget', str(budget), '--host', 'claude',
                              '--hunt', 'opencode,provider/open-model', '--verify', 'claude,,high').stdout.strip())
        self.audit('approve', str(run), '--words', 'yes, start it')
        (run / 'architecture.md').write_text('# Architecture\n\nOne script, app.py, changes objects.\n')
        units = self.tmp / 'units.json'
        units.write_text(json.dumps([unit_spec()]))
        self.audit('seed', str(run), str(units))
        for agent in ('recon-1a', 'recon-1b', 'recon-1c', 'recon-1d'):
            self.audit('prompt', str(run), 'recon', '--recon', agent[-2:], '--agent', agent)
            self.result(run, agent, 'app.py:1 main is the entry point.\n')
            self.audit('apply', str(run), 'recon', '--agent', agent)
        return run

    def result(self, run: Path, agent: str, value, code: int = 0) -> None:
        text = value if isinstance(value, str) else json.dumps(value)
        (run / 'runs' / f'{agent}.out.md').write_text(text)
        (run / 'runs' / f'{agent}.out.md.exit').write_text(f'{code}\n')

    def ledger(self, run: Path) -> list[dict]:
        return json.loads((run / 'coverage-ledger.json').read_text())

    def coverage_id(self, run: Path) -> str:
        return self.ledger(run)[0]['coverage_id']

    def hunter_result(self, run: Path, agent: str, candidate: dict | None = None, local: bool = False) -> dict:
        checks = [source_check(agent)]
        if local:
            evidence = run / 'runs' / 'scratch' / agent / 'evidence'
            evidence.mkdir(parents=True)
            (evidence / 'c01.json').write_text('{"status": 0}\n')
            checks.append(source_check(agent) | {'method': 'local', 'result': 'The object changed.',
                                                 'artifact': f'agents/{agent}/artifacts/evidence/c01.json'})
        unit = {'coverage_id': self.coverage_id(run), 'disposition': 'candidate' if candidate else 'covered',
                'reviewed_paths': ['app.py'], 'checks': checks,
                'candidate_fingerprints': [candidate['fingerprint']] if candidate else [], 'unresolved': []}
        proposed = []
        if candidate:
            proposed = [{k: v for k, v in candidate.items() if k != 'verdict'} |
                        {'proposed_verdict': candidate['verdict']}]
        return {'units': [unit], 'candidates': proposed, 'hardening': ['Log owner-check failures.'],
                'uncovered': [], 'sandbox_probe': 'pass'}

    def test_init_checks_the_tree_the_budget_floor_and_the_profile(self):
        args = ['init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude']
        for extra, message in ((['--profile', 'quick', '--budget', '5'], 'budget must be 6-500'),
                               (['--profile', 'standard', '--budget', '6'], 'budget must be 7-500'),
                               (['--profile', 'quick', '--waves', '2'], 'exactly one hunter wave'),
                               (['--profile', 'deep', '--parallel', '9'], 'parallel must be 1-8'),
                               (['--profile', 'quick', '--scope', '../x'], 'repository-relative')):
            with self.subTest(extra=extra):
                r = self.audit(*args, *extra, ok=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)
        (self.repo / 'app.py').write_text('changed\n')
        r = self.audit(*args, ok=False)
        self.assertIn('not clean', r.stderr)
        git(self.repo, 'checkout', '--', 'app.py')
        run = Path(self.audit(*args).stdout.strip())
        self.assertEqual(run.parent, self.repo / 'development' / 'audits')
        meta = json.loads((run / 'run-metadata.json').read_text())
        self.assertEqual((meta['profile'], meta['budget'], meta['waves'], meta['source_ref'], meta['run_status']),
                         ('standard', 60, 3, self.ref, 'in_progress'))
        self.assertEqual(meta['execution_policy'], 'sandboxed-source-and-local-only')
        r = self.audit('prompt', str(run), 'recon', '--recon', '1a', '--agent', 'recon-1a', ok=False)
        self.assertIn('audit gate is not approved', r.stderr)
        self.assertEqual(list((run / 'runs').glob('*.prompt.md')), [])
        self.audit('approve', str(run), '--words', 'start')
        r = self.audit('approve', str(run), '--words', 'again', ok=False)
        self.assertIn('approved already', r.stderr)
        # A sandbox that fails inside a run: the human may choose to go on without local checks.
        self.assertIn('Source-only from now on', self.audit('approve', str(run), '--source-only', '--words',
                                                            'go on without local checks').stdout)
        meta = json.loads((run / 'run-metadata.json').read_text())
        self.assertEqual((meta['source_only'], meta['source_only_approved']['words']),
                         (True, 'go on without local checks'))
        self.audit('approve', str(run), '--source-only', '--words', 'again', ok=False)

    def test_seed_derives_ids_checks_blocks_and_refuses_collisions(self):
        run = Path(self.audit('init', '--profile', 'quick', '--host', 'claude', '--hunt', 'opencode',
                              '--verify', 'claude').stdout.strip())
        units = self.tmp / 'units.json'
        units.write_text(json.dumps([unit_spec('b.py#x'), unit_spec('a.py#y')]))
        self.assertIn('seeded 2 unit(s)', self.audit('seed', str(run), str(units)).stdout)
        ledger = self.ledger(run)
        self.assertEqual([u['coverage_id'] for u in ledger], sorted(u['coverage_id'] for u in ledger))
        self.assertTrue(ledger[0]['coverage_id'].startswith('a.py%23y::app.py%23check_owner::app::'))
        self.assertEqual(json.loads((run / 'run-metadata.json').read_text())['selected_companions'],
                         ['WEB-PROTOCOL-AND-AUTH.md'])
        self.assertIn('seeded 0 unit(s), 2 already present', self.audit('seed', str(run), str(units)).stdout)
        changed = unit_spec('a.py#y') | {'starting_paths': ['other.py']}
        units.write_text(json.dumps([changed]))
        self.assertIn('canonical identity collision', self.audit('seed', str(run), str(units), ok=False).stderr)
        units.write_text(json.dumps([unit_spec('c.py#z') | {'ordinary_attack_class_block': 'ATTACK-CLASSES.md#Nope'}]))
        self.assertIn('unknown block', self.audit('seed', str(run), str(units), ok=False).stderr)
        units.write_text(json.dumps([unit_spec('c.py#z') | {'status': 'covered'}]))
        self.assertIn('takes only', self.audit('seed', str(run), str(units), ok=False).stderr)
        self.assertEqual(len(self.ledger(run)), 2, 'a refused seed writes nothing')

    def test_blocks_are_copied_verbatim_and_listed(self):
        listing = self.audit('blocks').stdout
        self.assertIn('## WEB-PROTOCOL-AND-AUTH.md', listing)
        self.assertIn('ATTACK-CLASSES.md#Access control', listing)
        shown = self.audit('blocks', '--show', 'ATTACK-CLASSES.md#Obvious things').stdout
        self.assertTrue(shown.startswith('**Obvious things** (subagent_type: `general`)'))
        self.assertIn('**Important**', shown, 'a bold note inside a block does not end it')
        self.assertNotIn('**Wildcard**', shown)
        core = self.audit('blocks', '--show', 'WEB-PROTOCOL-AND-AUTH.md#Core discipline').stdout
        self.assertTrue(core.startswith('## Core discipline'))
        self.assertNotIn('## HTTP framing', core)

    def test_a_hunter_prompt_has_every_part_and_its_result_is_validated(self):
        run = self.start()
        cid = self.coverage_id(run)
        out = self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01').stdout
        self.assertIn('--expect-clean opencode rw', out)
        self.assertIn('WF_AGENT_MODEL=provider/open-model', out)
        prompt = (run / 'runs' / 'hunt-w1-01.prompt.md').read_text()
        for part in ('You are the delegated run: never start another agent', 'One script, app.py, changes objects.',
                     '**Access control** (subagent_type:', '## Core discipline', 'no browser code',
                     '## Defensive vulnerability-finding method', 'Artifact promotion procedure',
                     '## Candidate gate', f'--ref {self.ref}', '"sandbox_probe"', '"observed_result"',
                     'exactly one JSON object',
                     # The contract's rules after its JSON block: a hunter missing them returns verdict
                     # instead of proposed_verdict and lists paths no check reviewed.
                     'uses `proposed_verdict` in place of `verdict`',
                     'the unit-level list is exactly the union of those owned paths',
                     'A `source` check uses `artifact: null`'):
            with self.subTest(part=part):
                self.assertIn(part, prompt)
        self.assertEqual(self.ledger(run)[0]['status'], 'in_progress')
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.assertIn('has not ended', r.stderr)
        # A result with prose around the object is discarded, never repaired; the unit is planned again.
        self.result(run, 'hunt-w1-01', 'Here it is: ' + json.dumps(self.hunter_result(run, 'hunt-w1-01')))
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn('not exactly one JSON object', r.stdout)
        self.assertEqual(self.ledger(run)[0]['status'], 'planned')
        # A second discarded result of the role stops it until the human answered.
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-02')
        forged = self.hunter_result(run, 'hunt-w1-02')
        forged['units'][0]['checks'].append(source_check('hunt-w1-02') | {
            'method': 'local', 'artifact': 'agents/hunt-w1-02/artifacts/evidence/c02.json'})
        self.result(run, 'hunt-w1-02', forged)
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-02', ok=False)
        self.assertEqual(r.returncode, 4)
        self.assertIn('was not promoted by the parent', r.stdout)
        r = self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-03', ok=False)
        self.assertEqual(r.returncode, 4)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-03',
                   '--after-human-answer')
        candidate = needs_validation()
        self.result(run, 'hunt-w1-03', self.hunter_result(run, 'hunt-w1-03', candidate, local=True))
        out = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-03').stdout
        self.assertIn('promoted 1 evidence file(s)', out)
        unit = self.ledger(run)[0]
        self.assertEqual((unit['status'], unit['agent_id'], unit['result_fingerprints']),
                         ('candidate', 'hunt-w1-03', [candidate['fingerprint']]))
        self.assertEqual(unit['hardening'], ['Log owner-check failures.'])
        self.assertTrue((run / 'agents' / 'hunt-w1-03' / 'artifacts' / 'evidence' / 'c01.json').is_file())
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['candidates'][candidate['fingerprint']]['state'], 'pending')
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-03', ok=False)
        self.assertIn('applied already', r.stderr)
        r = self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-03', ok=False)
        self.assertIn('used already', r.stderr)

    def test_the_budget_keeps_the_critic_and_verifier_reserves(self):
        run = self.start(budget=6)
        r = self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1',
                       '--agent', 'hunt-w1-01', ok=False)
        self.assertIn('no hunter fits', r.stderr)
        self.assertIn('budget_cannot_reserve_critics_and_validation', r.stderr)
        self.assertFalse((run / 'runs' / 'hunt-w1-01.prompt.md').exists())
        # Only the human's number raises it, and only upwards.
        self.assertIn('must be above 6', self.audit('budget', str(run), '--set', '5', '--words', 'x', ok=False).stderr)
        self.audit('budget', str(run), '--set', '9', '--words', 'raise it to 9')
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1',
                   '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', 'no result', code=5)
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.audit('defer', str(run), '--units', self.coverage_id(run), '--reason',
                   'budget_cannot_reserve_critics_and_validation')
        self.assertEqual(self.ledger(run)[0]['status'], 'deferred')
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['events'][0]['words'], 'raise it to 9')

    def test_a_quick_run_ends_with_validated_findings(self):
        run = self.start()
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        candidate = needs_validation()
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01', candidate))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        r = self.audit('final', str(run), ok=False)
        self.assertIn('without a final, independently verified disposition', r.stderr)
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.assertIn('exactly one coverage critic', self.audit('prompt', str(run), 'critic', '--wave', '1',
                                                                 '--agent', 'critic-w1b', ok=False).stderr)
        self.result(run, 'critic-w1', {'missing_units': [], 'reassign_ids': [cid], 'resolved_prior_leads': [],
                                       'stop': False})
        self.assertIn('deferred 1 at the cap', self.audit('apply', str(run), 'critic', '--agent', 'critic-w1').stdout)
        unit = self.ledger(run)[0]
        self.assertEqual((unit['status'], unit['unresolved'], unit['wave']), ('deferred', ['quick_profile_final_critic'], 2))
        self.assertEqual(unit['attempts'][0]['agent_id'], 'hunt-w1-01')
        out = self.audit('prompt', str(run), 'candidate', '--fingerprint', candidate['fingerprint'],
                         '--agent', 'verify-001').stdout
        self.assertIn('--expect-clean claude rw', out)
        prompt = (run / 'runs' / 'verify-001.prompt.md').read_text()
        self.assertIn('You did not write this candidate', prompt)
        self.assertIn('For `confirmed`, require it to check', prompt, 'quick merges Phase 3 and 5')
        record = confirmed()
        self.result(run, 'verify-001', {'decision': 'confirmed', 'record': record,
                                        'checks': [source_check('verify-001')], 'sandbox_probe': 'pass'})
        self.audit('apply', str(run), 'candidate', '--agent', 'verify-001')
        out = self.audit('final', str(run)).stdout
        self.assertIn('PASS: 1 findings valid', out)
        self.assertIn('complete: confirmed 1', out)
        self.assertEqual(json.loads((run / 'findings.json').read_text()), [record])
        self.assertEqual(json.loads((run / 'run-metadata.json').read_text())['run_status'], 'complete')

    def test_a_material_replacement_goes_to_a_fresh_verifier(self):
        run = self.start(profile='standard', budget=20)
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        candidate = needs_validation()
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01', candidate))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.assertIn('comes before the final-clean', self.audit('prompt', str(run), 'critic', '--wave', '1',
                                                                 '--final-clean', '--agent', 'critic-final-w1',
                                                                 ok=False).stderr)
        clean = {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [], 'stop': True}
        for agent, extra in (('critic-w1', ()), ('critic-final-w1', ('--final-clean',))):
            self.audit('prompt', str(run), 'critic', '--wave', '1', *extra, '--agent', agent)
            self.result(run, agent, clean)
            self.assertIn('clean', self.audit('apply', str(run), 'critic', '--agent', agent).stdout)
        fp = candidate['fingerprint']
        self.audit('prompt', str(run), 'candidate', '--fingerprint', fp, '--agent', 'verify-001')
        self.result(run, 'verify-001', {'decision': 'needs_validation', 'record': candidate,
                                        'checks': [source_check('verify-001')], 'sandbox_probe': 'pass'})
        self.audit('apply', str(run), 'candidate', '--agent', 'verify-001')
        r = self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--agent', 'verify-001', ok=False)
        self.assertIn('used already', r.stderr)
        self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--agent', 'record-001')
        self.result(run, 'record-001', {'decision': 'replace', 'reason': 'Reproduced.', 'record': confirmed()})
        self.audit('apply', str(run), 'record', '--agent', 'record-001')
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['candidates'][fp]['state'], 'needs_reverification', 'a stronger verdict is material')
        r = self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--agent', 'record-002', ok=False)
        self.assertIn('use --reverify', r.stderr)
        out = self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--reverify', '--agent', 'record-002').stdout
        self.assertIn('--expect-clean claude rw', out)
        self.result(run, 'record-002', {'decision': 'verified', 'fingerprint': fp, 'sandbox_probe': 'pass'})
        self.audit('apply', str(run), 'record', '--agent', 'record-002')
        out = self.audit('final', str(run)).stdout
        self.assertIn('complete: confirmed 1', out)

    def test_reassignment_is_capped_and_resume_refuses_another_commit(self):
        run = self.start(profile='standard', budget=40)
        cid = self.coverage_id(run)
        for wave in (1, 2, 3):
            agent = f'hunt-w{wave}-01'
            self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', str(wave), '--agent', agent)
            self.result(run, agent, self.hunter_result(run, agent))
            self.audit('apply', str(run), 'hunter', '--agent', agent)
            self.audit('prompt', str(run), 'critic', '--wave', str(wave), '--agent', f'critic-w{wave}')
            self.result(run, f'critic-w{wave}', {'missing_units': [], 'reassign_ids': [cid],
                                                 'resolved_prior_leads': [], 'stop': False})
            self.audit('apply', str(run), 'critic', '--agent', f'critic-w{wave}')
        unit = self.ledger(run)[0]
        self.assertEqual((unit['status'], unit['unresolved'], len(unit['attempts'])), ('deferred', ['stagnation'], 3))
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'x', ok=False)
        (self.repo / 'new.txt').write_text('x\n')
        git(self.repo, 'add', 'new.txt')
        git(self.repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'later')
        r = self.audit('resume', str(run), ok=False)
        self.assertIn('the run audits', r.stderr)


if __name__ == '__main__':
    unittest.main()
