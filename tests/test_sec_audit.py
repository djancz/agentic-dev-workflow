"""Behaviour tests for the wf sec audit scripts: the sandbox runner, evidence promotion and the audit tool.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import fcntl
import io
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

    def test_a_link_above_the_scratch_directory_leads_nowhere(self):
        # The agent may replace runs/scratch with a link; promotion must not follow it to another directory.
        elsewhere = self.tmp / 'elsewhere' / 'hunt-1' / 'evidence'
        elsewhere.mkdir(parents=True)
        (elsewhere / 'c01.out').write_bytes(b'forged\n')
        shutil.rmtree(self.tmp / 'runs' / 'scratch')
        (self.tmp / 'runs' / 'scratch').symlink_to(self.tmp / 'elsewhere', target_is_directory=True)
        r, entries = self.promote('evidence/c01.out:100')
        self.assertEqual(r.returncode, 125)
        self.assertIn('reached without a link', r.stderr)
        self.assertEqual(list(self.artifacts.rglob('*')), [])

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

    def test_a_copy_that_cannot_be_written_is_removed(self):
        import resource
        (self.scratch / 'evidence' / 'c01.out').write_bytes(b'x' * 100)
        args = [sys.executable, str(SANDBOX), 'promote', '--scratch', str(self.scratch), '--artifacts',
                str(self.artifacts), '--total', '1000', '--allow', 'evidence/c01.out:1000']
        r = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (10, 10)))
        self.assertEqual(r.returncode, 1, r.stderr)
        entry = json.loads(r.stdout)
        self.assertEqual(entry['status'], 'rejected')
        self.assertIn('could not be written', entry['reason'])
        self.assertFalse((self.artifacts / 'evidence' / 'c01.out').exists(), 'no partial copy stays')

    def test_invalid_use_exits_125(self):
        r, _ = self.promote('evidence/c01.out')
        self.assertEqual(r.returncode, 125)
        r = subprocess.run([sys.executable, str(SANDBOX), 'promote', '--scratch', str(self.tmp / 'missing'),
                            '--artifacts', str(self.artifacts), '--allow', 'a:1', '--total', '1'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 125)
        r = subprocess.run([sys.executable, str(SANDBOX), 'frobnicate'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 125)


def load_script(path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sandbox():
    return load_script(SANDBOX)


class SandboxGuards(unittest.TestCase):
    """Checks of the runner that need no sandbox."""

    def test_a_tool_tree_with_a_socket_or_fifo_is_refused(self):
        import socket
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'bin').mkdir()
        (tmp / 'bin' / 'tool').write_text('#!/bin/sh\n')
        (tmp / 'bin' / 'elsewhere').symlink_to('/run')
        fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool'))
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(str(tmp / 'bin' / 'service.sock'))
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool'), str(tmp / 'bin' / 'service.sock'))
        os.unlink(tmp / 'bin' / 'service.sock')
        os.mkfifo(tmp / 'pipe')
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool'), str(tmp / 'pipe'))
        # The scan reads the directory it holds: a rename of the path after the open changes nothing.
        os.unlink(tmp / 'pipe')
        os.rename(tmp, f'{tmp}.held')
        self.addCleanup(os.rename, f'{tmp}.held', tmp)
        os.mkdir(tmp)
        self.addCleanup(os.rmdir, tmp)
        os.mkfifo(tmp / 'swapped')
        self.addCleanup(os.unlink, tmp / 'swapped')
        self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool'))

    def test_no_tool_covers_a_mount_point_of_the_sandbox(self):
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        sandbox.RESERVED_MOUNTS = (str(tmp / 'src'),)  # stands in for /src, which this host may not have
        (tmp / 'src' / 'lib').mkdir(parents=True)
        for tool in (tmp / 'src', tmp / 'src' / 'lib', tmp):
            with self.subTest(tool=tool), self.assertRaises(sandbox.Refused) as caught:
                sandbox.check_tool(str(tool), REPO)
            self.assertIn('must not cover', str(caught.exception))

    def test_a_refused_export_stops_git_and_leaves_nothing(self):
        # git cat-file stands in for a child that keeps writing and survives a closed pipe and SIGTERM, so
        # that only a kill ends it: a run that merely closed the pipes would wait for it forever.
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        repo, ref = make_repo(tmp)
        pidfile = tmp / 'cat-file.pid'
        fake = tmp / 'fake-git'
        fake.write_text(f'''#!{sys.executable}
import os, signal, sys, time
if 'cat-file' not in sys.argv:
    os.execv({sandbox.GIT!r}, ['git', *sys.argv[1:]])
signal.signal(signal.SIGPIPE, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(pidfile)!r}, 'w').write(str(os.getpid()))
oid = sys.stdin.readline().strip()
os.write(1, (oid + ' blob 2000000\\n').encode())
while True:
    try:
        os.write(1, b'x' * 65536)
    except OSError:
        time.sleep(1)
''')
        fake.chmod(0o755)
        scratch = tmp / 'scratch'
        scratch.mkdir()
        script = ('import sys, importlib.util as u; s = u.spec_from_file_location("s", sys.argv[1]); '
                  'm = u.module_from_spec(s); s.loader.exec_module(m); m.SOURCE_MAX_BYTES = 1000; '
                  'm.GIT = sys.argv[2]; sys.exit(m.main(sys.argv[3:]))')
        r = subprocess.run([sys.executable, '-c', script, str(SANDBOX), str(fake), 'run', '--scratch', str(scratch),
                            '--ref', ref, '--check', 'c01', '--', 'true'], cwd=repo, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 125, r.stderr)
        self.assertIn('exceeds 1000 bytes; nothing ran', r.stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        self.assertEqual(list((scratch / 'src').iterdir()), [], 'the partial copy is removed')

    def test_cleanup_never_removes_a_directory_moved_into_the_exports_place(self):
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'src' / 'export').mkdir(parents=True)
        st = (tmp / 'src' / 'export').stat()
        (tmp / 'src' / 'export').rename(tmp / 'moved')
        (tmp / 'src' / 'export').mkdir()
        (tmp / 'src' / 'export' / 'keep.txt').write_text('not the export\n')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            sandbox.remove_export(os.open(tmp / 'src', os.O_RDONLY | os.O_DIRECTORY), 'export', (st.st_dev, st.st_ino))
        self.assertIn('replaced by another directory', err.getvalue())
        self.assertTrue((tmp / 'src' / 'export' / 'keep.txt').exists())
        st = (tmp / 'src' / 'export').stat()
        sandbox.remove_export(os.open(tmp / 'src', os.O_RDONLY | os.O_DIRECTORY), 'export', (st.st_dev, st.st_ino))
        self.assertFalse((tmp / 'src' / 'export').exists())

    def test_the_scope_check_reads_each_controller_on_its_own(self):
        sandbox = load_sandbox()
        r = subprocess.run(['/bin/sh', '-c', sandbox.SCOPE_SCRIPT], capture_output=True, text=True, timeout=30)
        self.assertEqual([line.split('=', 1)[0] for line in r.stdout.splitlines()],
                         ['pids.max', 'memory.max', 'cpu.max'], 'a missing file shifts no other value')

    def test_the_guard_starts_nothing_without_the_limits(self):
        sandbox = load_sandbox()
        r = subprocess.run(sandbox.guard_args(tasks=7, memory_mb=1, cpu_percent=1) + ['echo', 'started'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 125)
        self.assertIn('does not limit tasks', r.stderr)
        self.assertNotIn('started', r.stdout)


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
        os.mkfifo(self.scratch / 'inputs' / 'pipe')
        r = self.run_check('c26', 'true', options=('--inputs', 'inputs'))
        self.assertEqual(r.returncode, 125)
        self.assertIn('must not hold a socket or FIFO', r.stderr)
        (self.scratch / 'linked').symlink_to(self.scratch / 'inputs', target_is_directory=True)
        r = self.run_check('c27', 'true', options=('--inputs', 'linked'))
        self.assertEqual(r.returncode, 125)
        self.assertIn('must be a plain directory inside the scratch directory', r.stderr)

    def test_probe_reports_every_control(self):
        r = subprocess.run([sys.executable, str(SANDBOX), 'probe'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 12)
        self.assertTrue(all(line.startswith('enforced: ') for line in r.stdout.splitlines()))

    def test_every_run_mounts_a_fresh_export_of_the_commit(self):
        # Whatever is left in the scratch directory, also at the old cache path, never stands in for the commit.
        planted = self.scratch / 'src' / self.ref
        planted.mkdir(parents=True)
        (planted / 'app.py').write_text('print("planted")\n')
        (self.scratch / 'src' / f'{self.ref}.complete').write_text(self.ref + '\n')
        r = self.run_check('c20', 'python3', '/src/app.py')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'hello from target\n')
        self.assertEqual(sorted(p.name for p in (self.scratch / 'src').iterdir()), [self.ref, f'{self.ref}.complete'],
                         'the run removes its own copy')
        shutil.rmtree(self.scratch / 'src')
        (self.scratch / 'src').symlink_to(self.tmp, target_is_directory=True)
        r = self.run_check('c21', 'true')
        self.assertEqual(r.returncode, 125)
        self.assertIn('not a plain directory', r.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ['repo'], 'nothing is written through the link')

    def test_systemd_does_not_expand_the_command(self):
        r = self.run_check('c22', 'sh', '-c', 'echo "[${HOME}]"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, '[/work/home]\n')
        self.assertEqual(self.evidence('c22')['command'], ['sh', '-c', 'echo "[${HOME}]"'])

    def test_no_tool_exposes_a_runtime_directory(self):
        runtime = os.environ.get('XDG_RUNTIME_DIR') or '/run'
        scratch_tool = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch_tool)
        for check, tool in (('c23', runtime), ('c24', scratch_tool), ('c25', '/run')):
            with self.subTest(tool=tool):
                r = self.run_check(check, 'true', options=('--tool', tool))
                self.assertEqual(r.returncode, 125)
                self.assertIn('where host services keep their sockets', r.stderr)



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

    def local_check(self, run: Path, agent: str) -> dict:
        """A local check whose evidence the agent's sandbox left in its scratch, for the parent to promote."""
        evidence = run / 'runs' / 'scratch' / agent / 'evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'c01.json').write_text('{"status": 0}\n')
        return source_check(agent) | {'method': 'local', 'result': 'The object changed.',
                                      'artifact': f'agents/{agent}/artifacts/evidence/c01.json'}

    def hunter_result(self, run: Path, agent: str, candidate: dict | None = None, local: bool = False) -> dict:
        checks = [source_check(agent)]
        if local:
            checks.append(self.local_check(run, agent))
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
        self.assertIn('seeded 0 unit(s) for wave 1, 2 already present', self.audit('seed', str(run), str(units)).stdout)
        changed = unit_spec('a.py#y') | {'starting_paths': ['other.py']}
        units.write_text(json.dumps([changed]))
        self.assertIn('canonical identity collision', self.audit('seed', str(run), str(units), ok=False).stderr)
        units.write_text(json.dumps([unit_spec('c.py#z') | {'ordinary_attack_class_block': 'ATTACK-CLASSES.md#Nope'}]))
        self.assertIn('unknown block', self.audit('seed', str(run), str(units), ok=False).stderr)
        units.write_text(json.dumps([unit_spec('c.py#z') | {'status': 'covered'}]))
        self.assertIn('takes only', self.audit('seed', str(run), str(units), ok=False).stderr)
        self.assertEqual(len(self.ledger(run)), 2, 'a refused seed writes nothing')

    def commit(self, message: str = 'change') -> None:
        git(self.repo, 'add', '-A')
        git(self.repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', message)

    def test_init_keeps_records_private_and_lists_the_targets_agent_configuration(self):
        (self.repo / '.opencode' / 'plugin').mkdir(parents=True)
        (self.repo / '.opencode' / 'plugin' / 'start.js').write_text('// runs when OpenCode starts\n')
        (self.repo / '.claude' / 'skills').mkdir(parents=True)
        (self.repo / '.claude' / 'skills' / 'wf-sec-audit').symlink_to(REPO / 'skills' / 'wf-sec-audit')
        self.commit('agent configuration')
        audit = load_script(AUDIT)
        # Only the installer's link is exempt: a committed wf-named skill directory is the target's content.
        self.assertTrue(audit.installer_link('.claude/skills/wf-sec-audit', str(REPO / 'skills' / 'wf-sec-audit')))
        self.assertFalse(audit.installer_link('.claude/skills/wf-evil', str(self.repo / 'evil')))
        self.assertFalse(audit.installer_link('.claude/skills/wf-sec-audit/hook.js', str(REPO / 'skills')))
        args = ['init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude']
        r = self.audit(*args)
        self.assertIn('.opencode/plugin/start.js', r.stderr)
        run = Path(r.stdout.strip())
        meta = json.loads((run / 'run-metadata.json').read_text())
        self.assertEqual(meta['agent_config'], ['.opencode/plugin/start.js'], 'an installed wf skill link is no config')
        self.assertIn('agent configuration committed in the target (1 file(s))', self.audit('status', str(run)).stdout)
        r = self.audit('approve', str(run), '--words', 'start', ok=False)
        self.assertIn('--trust-agent-config', r.stderr, 'committed startup code needs the human\'s explicit choice')
        self.audit('approve', str(run), '--words', 'start, I trust that plugin', '--trust-agent-config')
        # An ignored plugin is this clone's, not the target's, but the gate shows it too.
        (self.repo / '.gitignore').write_text('/development\n/.opencode/plugins/\n')
        self.commit('ignore local plugins')
        (self.repo / '.opencode' / 'plugins').mkdir()
        (self.repo / '.opencode' / 'plugins' / 'local.js').write_text('// mine\n')
        r = self.audit(*args)
        self.assertIn('ignored agent configuration', r.stderr)
        self.assertEqual(json.loads((Path(r.stdout.strip()) / 'run-metadata.json').read_text())['local_agent_config'],
                         ['.opencode/plugins/local.js'])
        records = self.repo / 'development'
        records.rename(self.tmp / 'records')
        (self.tmp / 'elsewhere').mkdir()
        records.symlink_to(self.tmp / 'elsewhere', target_is_directory=True)
        self.assertIn('not to the records of the main worktree', self.audit(*args, ok=False).stderr)
        records.unlink()
        (self.tmp / 'records').rename(records)
        shutil.rmtree(self.repo / 'development' / 'audits')
        (self.repo / 'development' / 'audits').symlink_to(self.tmp, target_is_directory=True)
        self.assertIn('development/audits is a link', self.audit(*args, ok=False).stderr)
        (self.repo / 'development' / 'audits').unlink()
        git(self.repo, 'add', '-f', 'development/private.txt')
        self.commit('records')
        self.assertIn('git tracks files below development/', self.audit(*args, ok=False).stderr)

    def test_agents_start_only_on_the_approved_commit_and_learn_the_scope(self):
        (self.repo / 'app.py').write_text('print("version 2")\n')
        self.commit('v2')
        run = Path(self.audit('init', '--profile', 'quick', '--host', 'claude', '--hunt', 'opencode', '--verify',
                              'claude', '--scope', 'app.py', '--since', 'HEAD~1').stdout.strip())
        self.audit('approve', str(run), '--words', 'start')
        self.audit('prompt', str(run), 'recon', '--recon', '1a', '--agent', 'recon-1a')
        prompt = (run / 'runs' / 'recon-1a.prompt.md').read_text()
        self.assertIn('in scope are the paths app.py and the changes from', prompt)
        self.assertIn('(app.py), with the code they reach', prompt)
        (self.repo / 'run.sh').write_text('#!/bin/sh\necho changed\n')
        r = self.audit('prompt', str(run), 'recon', '--recon', '1b', '--agent', 'recon-1b', ok=False)
        self.assertIn('working tree changed since the audit gate', r.stderr)
        self.commit('later')
        r = self.audit('prompt', str(run), 'recon', '--recon', '1b', '--agent', 'recon-1b', ok=False)
        self.assertIn('the audit was approved for', r.stderr)
        self.assertFalse((run / 'runs' / 'recon-1b.prompt.md').exists())

    def test_a_failed_critic_runs_again_and_new_units_wait_for_the_next_wave(self):
        run = Path(self.audit('init', '--profile', 'standard', '--waves', '1', '--budget', '30', '--host', 'claude',
                              '--hunt', 'opencode', '--verify', 'claude').stdout.strip())
        self.audit('approve', str(run), '--words', 'start')
        (run / 'architecture.md').write_text('# Architecture\n\nOne script.\n')
        units = self.tmp / 'units.json'
        units.write_text(json.dumps([unit_spec()]))
        self.audit('seed', str(run), str(units))
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.result(run, 'critic-w1', 'usage limit', code=6)
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1', ok=False)
        # A failed critic is no pass: a fresh agent, here the fallback, runs the wave's critic again.
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1b', '--ran-as', 'codex,,high')
        missing = {k: v for k, v in unit_spec('other.py#x').items() if k in ('surface', 'boundary', 'subsystem',
                                                                            'attack_class', 'starting_paths')}
        self.result(run, 'critic-w1b', {'missing_units': [missing | {'reason': 'Another entry point.'}],
                                        'reassign_ids': [], 'resolved_prior_leads': [], 'stop': False})
        self.assertIn('work remains', self.audit('apply', str(run), 'critic', '--agent', 'critic-w1b').stdout)
        units.write_text(json.dumps([unit_spec('other.py#x')]))
        r = self.audit('seed', str(run), str(units), ok=False)
        self.assertIn('wave 2, past the cap of 1', r.stderr)
        out = self.audit('seed', str(run), str(units), '--status', 'deferred', '--reason', 'wave_cap_reached').stdout
        self.assertIn('seeded 1 unit(s) for wave 2', out)
        self.assertIn('1 proposal(s) resolved, 0 left', out)
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--final-clean', '--agent', 'critic-final-w1')
        self.result(run, 'critic-final-w1', {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [],
                                             'stop': True})
        self.assertIn('clean', self.audit('apply', str(run), 'critic', '--agent', 'critic-final-w1').stdout)
        r = self.audit('final', str(run), ok=False)
        self.assertIn('deferred by a cap or the budget', r.stderr)
        self.assertIn('incomplete: no records', self.audit('final', str(run), '--incomplete', 'wave_cap_reached').stdout)

    def test_a_reassigned_unit_waits_for_the_wave_after_its_critic(self):
        run = self.start(profile='standard', budget=40)
        first = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', first, '--wave', '1', '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        missing = {k: v for k, v in unit_spec('b.py#x').items() if k in ('surface', 'boundary', 'subsystem',
                                                                        'attack_class', 'starting_paths')}
        self.result(run, 'critic-w1', {'missing_units': [missing | {'reason': 'Another entry.'}], 'reassign_ids': [],
                                       'resolved_prior_leads': [], 'stop': False})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        self.assertIn('not seeded yet', self.audit('final', str(run), ok=False).stderr)
        units = self.tmp / 'units-2.json'
        units.write_text(json.dumps([unit_spec('b.py#x')]))
        self.audit('seed', str(run), str(units))
        second = next(u['coverage_id'] for u in self.ledger(run) if u['coverage_id'] != first)
        self.audit('prompt', str(run), 'hunter', '--units', second, '--wave', '2', '--agent', 'hunt-w2-01')
        result = self.hunter_result(run, 'hunt-w2-01')
        result['units'][0]['coverage_id'] = second
        self.result(run, 'hunt-w2-01', result)
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w2-01')
        self.audit('prompt', str(run), 'critic', '--wave', '2', '--agent', 'critic-w2')
        self.result(run, 'critic-w2', {'missing_units': [], 'reassign_ids': [first], 'resolved_prior_leads': [],
                                       'stop': False})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w2')
        unit = next(u for u in self.ledger(run) if u['coverage_id'] == first)
        self.assertEqual((unit['status'], unit['wave']), ('planned', 3), 'hunted in 1, reassigned after wave 2')
        self.audit('prompt', str(run), 'hunter', '--units', first, '--wave', '3', '--agent', 'hunt-w3-01')

    def test_results_of_an_unexpected_shape_are_discarded_and_source_only_runs_no_check(self):
        run = self.start()
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        bad = self.hunter_result(run, 'hunt-w1-01')
        bad['units'][0]['reviewed_paths'] = [{'path': 'app.py'}]
        self.result(run, 'hunt-w1-01', bad)
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('discarded hunt-w1-01', r.stdout)
        self.assertNotIn('Traceback', r.stderr)
        self.assertEqual(self.ledger(run)[0]['status'], 'planned')
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01b',
                   '--after-human-answer')
        twice = self.hunter_result(run, 'hunt-w1-01b')
        twice['units'][0]['checks'][0]['reviewed_paths'] = ['app.py', 'app.py']
        self.result(run, 'hunt-w1-01b', twice)
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01b', ok=False)
        self.assertIn('distinct repository-relative paths', r.stdout)
        self.assertEqual(self.ledger(run)[0]['status'], 'planned', 'the unit is planned again, not stuck')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1', '--after-human-answer')
        self.result(run, 'critic-w1', {'missing_units': [], 'reassign_ids': [[cid]], 'resolved_prior_leads': [],
                                       'stop': False})
        r = self.audit('apply', str(run), 'critic', '--agent', 'critic-w1', ok=False)
        self.assertIn('discarded critic-w1', r.stdout)
        self.assertNotIn('Traceback', r.stderr)
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1b')
        # After a sandbox failure the human chose source-only: no check may claim local evidence then.
        self.audit('approve', str(run), '--source-only', '--words', 'go on without local checks')
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-02',
                   '--after-human-answer')
        self.assertIn('source-only', (run / 'runs' / 'hunt-w1-02.prompt.md').read_text())
        self.result(run, 'hunt-w1-02', self.hunter_result(run, 'hunt-w1-02', local=True))
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-02', ok=False)
        self.assertIn('this audit is source-only', r.stdout)

    def test_resume_sets_interrupted_runs_aside(self):
        run = self.start()
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        self.assertIn('runs without a result: hunt-w1-01', self.audit('status', str(run)).stdout)
        self.assertIn('1 interrupted run(s) set aside', self.audit('resume', str(run)).stdout)
        self.assertEqual(self.ledger(run)[0]['status'], 'planned')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.assertIn('set aside by resume', self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01',
                                                        ok=False).stderr)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-02')
        self.result(run, 'hunt-w1-02', self.hunter_result(run, 'hunt-w1-02'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-02')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.audit('resume', str(run))
        self.assertIn('0 interrupted run(s) set aside', self.audit('resume', str(run)).stdout, 'resume is idempotent')
        self.assertIn('ended, not applied: none', self.audit('status', str(run)).stdout)
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1b')

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
        planted = run / 'agents' / 'hunt-w1-02' / 'artifacts' / 'evidence'
        planted.mkdir(parents=True)
        (planted / 'c02.json').write_text('{"status": 0}\n')  # exists, but this apply did not promote it
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
        r = self.audit('apply', str(run), 'candidate', '--agent', 'verify-001', ok=False)
        self.assertIn('needs a local check of your own', r.stdout, 'a source re-read alone confirms nothing')
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['candidates'][record['fingerprint']]['state'], 'pending')
        # A verifier whose sandbox failed settles nothing, whatever it decided: the candidate waits.
        self.audit('prompt', str(run), 'candidate', '--fingerprint', candidate['fingerprint'], '--agent', 'verify-002')
        self.result(run, 'verify-002', {'decision': 'confirmed', 'record': record,
                                        'checks': [source_check('verify-002')], 'sandbox_probe': 'fail: no-network'})
        r = self.audit('apply', str(run), 'candidate', '--agent', 'verify-002', ok=False)
        self.assertEqual(r.returncode, 4)
        self.assertIn('STOP: the sandbox probe failed', r.stdout)
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['candidates'][record['fingerprint']]['state'], 'pending')
        self.assertIn('verify-002', state['candidates'][record['fingerprint']]['failed_verifiers'])
        self.audit('prompt', str(run), 'candidate', '--fingerprint', candidate['fingerprint'], '--agent', 'verify-003')
        self.assertIn('needs at\nleast one local check of your own', (run / 'runs' / 'verify-003.prompt.md').read_text())
        self.result(run, 'verify-003', {'decision': 'confirmed', 'record': record, 'sandbox_probe': 'pass',
                                        'checks': [source_check('verify-003'), self.local_check(run, 'verify-003')]})
        self.audit('apply', str(run), 'candidate', '--agent', 'verify-003')
        out = self.audit('final', str(run)).stdout
        self.assertIn('PASS: 1 findings valid', out)
        self.assertIn('complete: confirmed 1', out)
        self.assertEqual(json.loads((run / 'findings.json').read_text()), [record])
        self.assertEqual(self.ledger(run)[0]['decisions'], {record['fingerprint']: 'confirmed'})
        # The run ends only once the reports exist, derived from the final records.
        meta = json.loads((run / 'run-metadata.json').read_text())
        self.assertEqual((meta['run_status'], meta['phase']), ('in_progress', 'report'))
        r = self.audit('prompt', str(run), 'recon', '--focus', 'more', '--agent', 'recon-late', ok=False)
        self.assertIn('findings are final', r.stderr)
        units = self.tmp / 'late.json'
        units.write_text(json.dumps([unit_spec('late.py#x')]))
        self.assertIn('findings are final', self.audit('seed', str(run), str(units), ok=False).stderr)
        self.assertIn('write REPORT.md, FINDINGS-DETAIL.md, NEEDS-VALIDATION.md',
                      self.audit('done', str(run), ok=False).stderr)
        for name in ('REPORT.md', 'FINDINGS-DETAIL.md', 'NEEDS-VALIDATION.md'):
            (run / name).write_text(f'# {name}\n')
        (run / 'findings.json').write_text('[]\n')
        self.assertIn('changed after final', self.audit('done', str(run), ok=False).stderr)
        self.audit('final', str(run))
        self.assertIn('complete', self.audit('done', str(run)).stdout)
        self.assertEqual(json.loads((run / 'run-metadata.json').read_text())['run_status'], 'complete')
        self.assertIn('the run is complete', self.audit('final', str(run), ok=False).stderr)

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
        self.assertIn(f'WF_AGENT_EXPECT_HEAD={self.ref}', out, 'every run is pinned to the audited commit')
        # A verifier whose sandbox failed cannot reproduce a decisive local result: nothing settles.
        self.result(run, 'record-002', {'decision': 'verified', 'fingerprint': fp, 'sandbox_probe': 'fail: no-network'})
        self.assertEqual(self.audit('apply', str(run), 'record', '--agent', 'record-002', ok=False).returncode, 4)
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual(state['candidates'][fp]['state'], 'needs_reverification')
        self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--reverify', '--agent', 'record-003')
        self.assertIn('"checks": the checks you ran yourself', (run / 'runs' / 'record-003.prompt.md').read_text())
        self.result(run, 'record-003', {'decision': 'verified', 'fingerprint': fp, 'sandbox_probe': 'pass',
                                        'checks': [source_check('record-003')]})
        r = self.audit('apply', str(run), 'record', '--agent', 'record-003', ok=False)
        self.assertIn('needs a local check of your own', r.stdout, 'nobody reproduced the stronger verdict')
        self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--reverify', '--agent', 'record-004')
        self.result(run, 'record-004', {'decision': 'verified', 'fingerprint': fp, 'sandbox_probe': 'pass',
                                        'checks': [source_check('record-004'), self.local_check(run, 'record-004')]})
        self.audit('apply', str(run), 'record', '--agent', 'record-004')
        self.assertIn('agents/record-004/artifacts/evidence/c01.json',
                      json.dumps(self.ledger(run)[0]['local_checks']), 'its reproduction is in the ledger')
        out = self.audit('final', str(run)).stdout
        self.assertIn('complete: confirmed 1', out)
        self.assertEqual(self.ledger(run)[0]['decisions'], {fp: 'confirmed'}, 'the ledger follows the final verdict')
        prompt = (run / 'runs' / 'record-001.prompt.md').read_text()
        self.assertIn('The checks behind it', prompt)
        self.assertIn('"agent_id": "verify-001"', prompt, 'the record verifier sees the evidence it checks')

    def test_the_record_verifier_sees_archived_and_verifier_checks(self):
        audit = load_script(AUDIT)
        fp = 'fp-1'
        archived = {'agent_id': 'hunt-w1-01', 'result_fingerprints': [fp], 'local_checks': [source_check('hunt-w1-01')]}
        unit = {'agent_id': 'hunt-w2-01', 'result_fingerprints': [], 'local_checks': [source_check('hunt-w2-01')],
                'attempts': [archived]}
        candidate = {'checks': [source_check('verify-001'), source_check('hunt-w1-01')]}
        checks = audit.fingerprint_checks(candidate, [unit], fp)
        self.assertEqual([c['agent_id'] for c in checks], ['hunt-w1-01', 'verify-001'],
                         'the archived attempt and the verifier, once each; not the new attempt that dropped it')

    def test_a_verifier_check_belongs_to_the_fingerprint_it_verified(self):
        audit = load_script(AUDIT)
        unit = {'agent_id': 'hunt-w1-01', 'result_fingerprints': ['fp-a', 'fp-b'], 'attempts': [],
                'local_checks': [source_check('hunt-w1-01'), source_check('verify-001')]}
        checks = audit.fingerprint_checks({'checks': [source_check('verify-002')]}, [unit], 'fp-b')
        self.assertEqual([c['agent_id'] for c in checks], ['hunt-w1-01', 'verify-002'],
                         'verify-001 checked fp-a in the same unit: its check is no evidence for fp-b')

        class SourceOnly:
            meta = {'source_only': True}
        self.assertIn('confirms nothing', audit.local_reproduction(SourceOnly, 'confirmed', [source_check('v')])[0])
        self.assertEqual(audit.local_reproduction(SourceOnly, 'needs_validation', [source_check('v')]), [])

    def test_linked_config_roots_and_codex_skills_are_agent_configuration(self):
        (self.repo / 'cfg').mkdir()
        (self.repo / 'cfg' / 'settings.json').write_text('{"hooks": {}}\n')
        (self.repo / '.gemini').symlink_to('cfg', target_is_directory=True)
        (self.repo / '.agents' / 'skills' / 'helper').mkdir(parents=True)
        (self.repo / '.agents' / 'skills' / 'helper' / 'SKILL.md').write_text('---\nname: helper\n---\n')
        (self.repo / '.agents' / 'skills' / 'wf-sec-audit').symlink_to(REPO / 'skills' / 'wf-sec-audit')
        (self.repo / '.codex').mkdir()
        (self.repo / '.codex' / 'a\tb.toml').write_text('# a name git quotes\n')
        self.commit('agent configuration elsewhere')
        run = Path(self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude').stdout.strip())
        self.assertEqual(json.loads((run / 'run-metadata.json').read_text())['agent_config'],
                         ['.agents/skills/helper/SKILL.md', '.codex/a\tb.toml', '.gemini'])

    def test_the_records_are_where_the_installer_keeps_them_also_with_a_separate_git_dir(self):
        main = self.tmp / 'main'
        git(self.tmp, 'init', '-q', '--separate-git-dir', str(self.tmp / 'meta.git'), str(main))
        (main / 'app.py').write_text('print(1)\n')
        (main / '.gitignore').write_text('/development\n')
        git(main, 'add', '.')
        git(main, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init')
        git(main, 'worktree', 'add', '-q', str(self.tmp / 'wt'))
        # The installer takes the first entry of git worktree list, here not the parent of the git directory.
        records = load_script(REPO / 'install.py').main_worktree(self.tmp / 'wt') / 'development'
        self.assertNotEqual(records, self.tmp / 'development')
        records.mkdir()
        (self.tmp / 'wt' / 'development').symlink_to(records, target_is_directory=True)
        r = subprocess.run([sys.executable, str(AUDIT), 'init', '--host', 'claude', '--hunt', 'opencode', '--verify',
                            'claude'], cwd=self.tmp / 'wt', capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(r.stdout.strip()).parent.resolve(), (records / 'audits').resolve())

    def test_proposals_stay_until_seeded_or_dismissed(self):
        run = self.start(profile='standard', budget=40)
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        labels = ('surface', 'boundary', 'subsystem', 'attack_class', 'starting_paths')
        missing = [{k: v for k, v in unit_spec(surface).items() if k in labels} | {'reason': 'Another entry.'}
                   for surface in ('b.py#x', 'c.py#y')]
        self.result(run, 'critic-w1', {'missing_units': missing, 'reassign_ids': [], 'resolved_prior_leads': [],
                                       'stop': False})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        units = self.tmp / 'units-2.json'
        units.write_text(json.dumps([unit_spec('b.py#x')]))
        self.assertIn('1 proposal(s) resolved, 1 left', self.audit('seed', str(run), str(units)).stdout)
        self.assertIn('to seed 1 (missing_units, from critic-w1): entry c.py#y',
                      self.audit('status', str(run)).stdout, 'the proposal not seeded stays')
        seeded = next(u['coverage_id'] for u in self.ledger(run) if u['surface'] == 'entry b.py#x')
        self.audit('defer', str(run), '--units', seeded, '--reason', 'budget_cannot_reserve_critics_and_validation')
        self.assertIn('not seeded yet', self.audit('final', str(run), ok=False).stderr)
        self.assertIn('no proposal 2', self.audit('dismiss', str(run), '2', '--reason', 'x', ok=False).stderr)
        out = self.audit('dismiss', str(run), '1', '--reason', 'the same entry as b.py#x').stdout
        self.assertIn('dismissed 1 proposal(s): the same entry as b.py#x; 0 left', out)
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual((state['missing_units'], state['dismissed'][0]['surface']), ([], 'entry c.py#y'))

    def test_a_final_clean_pass_must_cover_the_latest_wave(self):
        run = self.start(profile='standard', budget=40)
        cid = self.coverage_id(run)
        clean = {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [], 'stop': True}
        for wave in (1, 2):
            agent = f'hunt-w{wave}-01'
            self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', str(wave), '--agent', agent)
            self.result(run, agent, self.hunter_result(run, agent))
            self.audit('apply', str(run), 'hunter', '--agent', agent)
            if wave == 2:
                r = self.audit('final', str(run), ok=False)
                self.assertIn('no clean final-clean critic pass over the latest wave', r.stderr,
                              'the clean pass of wave 1 never saw what wave 2 hunted')
            for kind, extra in (('critic', ()), ('critic-final', ('--final-clean',))):
                self.audit('prompt', str(run), 'critic', '--wave', str(wave), *extra, '--agent', f'{kind}-w{wave}')
                self.result(run, f'{kind}-w{wave}', clean)
                self.audit('apply', str(run), 'critic', '--agent', f'{kind}-w{wave}')
            if wave == 1:
                self.audit('reopen', str(run), '--units', cid, '--reason', 'the human asked for another pass')
        self.assertIn('complete: no records', self.audit('final', str(run)).stdout)

    def test_a_result_that_would_outgrow_the_validator_is_discarded(self):
        run = self.start()
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        audit = load_script(AUDIT)
        audit.VL.MAX_INPUT_BYTES = len((run / 'coverage-ledger.json').read_bytes()) + 10
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = audit.main(['apply', str(run), 'hunter', '--agent', 'hunt-w1-01'])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("over the validator's limit", out.getvalue())
        self.assertEqual(self.ledger(run)[0]['status'], 'planned', 'final could not have read that ledger')

    def test_resume_changes_nothing_once_the_findings_are_final(self):
        run = self.start()
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        self.audit('final', str(run), '--incomplete', 'interrupted_by_the_human')
        before = (run / 'coverage-ledger.json').read_bytes()
        self.assertIn('its findings are final', self.audit('resume', str(run)).stdout)
        self.assertEqual((run / 'coverage-ledger.json').read_bytes(), before)
        for name in ('REPORT.md', 'FINDINGS-DETAIL.md', 'NEEDS-VALIDATION.md'):
            (run / name).write_text(f'# {name}\n')
        self.assertIn('incomplete (interrupted_by_the_human)', self.audit('done', str(run)).stdout)

    def test_a_changed_condition_or_instruction_is_material(self):
        audit = load_script(AUDIT)
        old = confirmed()
        self.assertFalse(audit.material(old, json.loads(json.dumps(old))))
        self.assertTrue(audit.material(old, old | {'conditions': ['An administrator enabled sharing.']}))
        changed = json.loads(json.dumps(old))
        changed['execution']['instructions'] = ['Call main twice.']
        self.assertTrue(audit.material(old, changed))
        reworded = json.loads(json.dumps(old))
        reworded['description'] = 'Another user\'s object can be changed.'
        self.assertFalse(audit.material(old, reworded), 'wording alone is not material')

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
            out = self.audit('apply', str(run), 'critic', '--agent', f'critic-w{wave}').stdout
            self.assertIn('work remains', out, 'a reassignment the cap defers is no clean pass')
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
