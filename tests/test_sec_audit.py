"""Behaviour tests for the wf sec audit scripts: the sandbox runner, evidence promotion and the audit tool.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
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


def sandbox_capability() -> str | None:
    """What this host lacks to run any such sandbox, found without sandbox-run.py: where nothing is lacking, a
    probe that fails is a fault of the runner, and the tests fail instead of skipping."""
    for tool in ('/usr/bin/bwrap', '/usr/bin/systemd-run'):
        if not os.access(tool, os.X_OK):
            return f'{tool} is not installed'
    script = 'd=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat "$d/pids.max" "$d/memory.max" "$d/cpu.max"'
    try:
        r = subprocess.run(['/usr/bin/bwrap', '--unshare-all', '--ro-bind', '/', '/', '/bin/sh', '-c', 'true'],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            return f'bwrap cannot make a sandbox here: {r.stderr.strip()[:200]}'
        r = subprocess.run(['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '-p', 'TasksMax=17', '-p',
                            'MemoryMax=67M', '-p', 'CPUQuota=50%', '/bin/sh', '-c', script],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f'no sandbox here: {exc}'
    if r.returncode or r.stdout.split() != ['17', str(67 * 1024 * 1024), '50000', '100000']:
        return f'no user scope with delegated pids, memory and cpu limits: {(r.stderr or r.stdout).strip()[:200]}'
    return None


CAPABILITY = sandbox_capability()


class Promote(unittest.TestCase):
    """The parent's copy of evidence into the artifacts directory; it needs no sandbox."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
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
        # An apply that stopped after the copy promotes again: the same bytes are the same evidence.
        again, repeated = self.promote('evidence/c01.out:100')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(repeated, [entries[0]])
        (self.scratch / 'evidence' / 'c01.out').write_bytes(b'observed otherwise\n')
        r, entries = self.promote('evidence/c01.out:100')
        self.assertEqual(r.returncode, 1, 'an existing destination is never overwritten')
        self.assertIn('exists already with other content', entries[0]['reason'])
        self.assertEqual(copy.read_bytes(), b'observed\n')
        # A destination that is a link is never taken for the copy, even with the same bytes.
        (self.scratch / 'evidence' / 'c03.out').write_bytes(b'same\n')
        (self.tmp / 'elsewhere').write_bytes(b'same\n')
        (self.artifacts / 'evidence' / 'c03.out').symlink_to(self.tmp / 'elsewhere')
        r, entries = self.promote('evidence/c03.out:100')
        self.assertEqual(r.returncode, 1)
        self.assertIn('exists already', entries[0]['reason'])

    def test_a_destination_replaced_while_it_is_compared_is_no_promotion(self):
        from unittest import mock
        sandbox = load_sandbox()
        (self.artifacts / 'c01.out').write_bytes(b'observed\n')
        fd = os.open(self.artifacts, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        self.assertTrue(sandbox.same_copy(fd, 'c01.out', b'observed\n'))
        real_read, swapped = os.read, []

        def swapping_read(descriptor, size):
            data = real_read(descriptor, size)
            if data and not swapped:
                swapped.append(True)
                (self.artifacts / 'c01.out').rename(self.artifacts / 'old.out')
                (self.artifacts / 'c01.out').write_bytes(b'other bytes\n')
            return data
        with mock.patch.object(os, 'read', swapping_read):
            self.assertFalse(sandbox.same_copy(fd, 'c01.out', b'observed\n'), 'the name holds other bytes now')
        self.assertEqual(swapped, [True])

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


def public_tmp() -> Path:
    """A new directory that every user may enter, below /tmp: the user's own TMPDIR may be closed to others."""
    path = Path(tempfile.mkdtemp(dir='/tmp' if os.path.isdir('/tmp') else None)).resolve()
    os.chmod(path, 0o755)
    return path


def made_of(root: Path) -> set:
    """The identities of root and every directory below it, as export_source records the ones it made."""
    found = [os.lstat(root)] + [os.lstat(Path(top) / name) for top, dirs, _ in os.walk(root) for name in dirs]
    return {(st.st_dev, st.st_ino) for st in found if stat.S_ISDIR(st.st_mode)}


class SandboxGuards(unittest.TestCase):
    """Checks of the runner that need no sandbox."""

    def test_a_tool_tree_with_a_socket_or_fifo_is_refused(self):
        import socket
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
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
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool'),
                         f'a socket or FIFO, which would reach a host process: {tmp / "bin" / "service.sock"}')
        os.unlink(tmp / 'bin' / 'service.sock')
        os.mkfifo(tmp / 'pipe')
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool'),
                         f'a socket or FIFO, which would reach a host process: {tmp / "pipe"}')
        # The scan reads the directory it holds: a rename of the path after the open changes nothing.
        os.unlink(tmp / 'pipe')
        os.rename(tmp, f'{tmp}.held')
        self.addCleanup(os.rename, f'{tmp}.held', tmp)
        os.mkdir(tmp)
        self.addCleanup(os.rmdir, tmp)
        os.mkfifo(tmp / 'swapped')
        self.addCleanup(os.unlink, tmp / 'swapped')
        self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool'))

    def test_the_sandbox_takes_writes_only_in_work_and_shared_memory_and_hides_the_host_name(self):
        sandbox = load_sandbox()
        args = sandbox.bwrap_args(work_bytes=1024, env={'DUMMY_USER': 'alice'}, binds=[('/opt/tool', '/opt/tool')],
                                  chdir='/', status_fd=9, etc=[(7, '/etc/ssl/certs')], python='/usr/bin/python3.99')
        tail = args[args.index('--remount-ro'):]
        self.assertEqual(tail[:4], ['--remount-ro', '/dev', '--remount-ro', '/'], 'after every mount')
        self.assertLess(args.index('/opt/tool'), args.index('--remount-ro'))
        self.assertIn(['--size', str(sandbox.SHM_BYTES), '--tmpfs', '/dev/shm'],
                      [args[n:n + 4] for n in range(len(args))])
        self.assertIn(['--size', '1024', '--tmpfs', '/work'], [args[n:n + 4] for n in range(len(args))])
        self.assertIn(['--hostname', 'sandbox'], [args[n:n + 2] for n in range(len(args))])
        mounted = [args[n + 1] for n in range(len(args)) if args[n] == '--ro-bind']
        self.assertFalse([path for path in mounted if path.startswith('/etc')],
                         'an entry of /etc is mounted by the descriptor that was checked, never by its path')
        self.assertIn(['--ro-bind-fd', '7', '/etc/ssl/certs'], [args[n:n + 3] for n in range(len(args))])
        self.assertNotIn('--setenv', args, 'the environment acts on the command, not on the step that starts it')
        self.assertEqual(args[args.index('--') + 1:],
                         ['/usr/bin/python3.99', '-I', '-S', '-c', sandbox.LAUNCHER, '1', 'DUMMY_USER=alice'])

    def test_an_etc_entry_goes_in_only_inside_etc_and_usr_and_readable_by_every_user(self):
        from unittest import mock
        sandbox = load_sandbox()
        etc, outside = public_tmp(), public_tmp()
        for path in (etc, outside):
            self.addCleanup(shutil.rmtree, path)
        (etc / 'fonts').mkdir(mode=0o755)
        (etc / 'fonts' / 'fonts.conf').write_text('<fontconfig/>\n')
        os.chmod(etc / 'fonts', 0o755)
        # A JMX password file of its owner keeps the Java runtime's configuration out.
        management = etc / 'java-21-openjdk' / 'management'
        management.mkdir(parents=True)
        for path in (etc / 'java-21-openjdk', management):
            os.chmod(path, 0o755)
        (management / 'jmxremote.password').write_text('monitorRole secret\n')
        os.chmod(management / 'jmxremote.password', 0o600)
        # A certificate entry that leads to a directory only some users may enter, elsewhere in /etc.
        (etc / 'keys').mkdir(mode=0o750)
        (etc / 'keys' / 'ca.pem').write_text('public\n')
        os.chmod(etc / 'keys', 0o750)
        (etc / 'ssl').mkdir()
        os.chmod(etc / 'ssl', 0o755)
        (etc / 'ssl' / 'certs').symlink_to('../keys')
        (etc / 'perl').symlink_to(outside)
        names = ('fonts', 'perl', 'ssl/certs')
        with mock.patch.object(sandbox, 'ETC_ENTRIES', names), contextlib.redirect_stderr(io.StringIO()) as notes:
            mounts = sandbox.etc_mounts(etc, roots=(etc, Path('/usr')))
        for fd, _ in mounts:
            os.close(fd)
        self.assertEqual([target for _, target in mounts], ['/etc/fonts'], notes.getvalue())
        text = notes.getvalue()
        self.assertIn('/etc/java-21-openjdk stays out of the sandbox: it holds '
                      f'{management / "jmxremote.password"}, which not every user may read', text)
        self.assertIn(f'/etc/ssl/certs stays out of the sandbox: not every user may pass through {etc / "keys"}', text)
        self.assertIn('/etc/perl stays out of the sandbox: it leads out of', text)

    def test_an_etc_entry_must_be_readable_by_every_user_throughout(self):
        sandbox = load_sandbox()
        tmp = public_tmp()
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'certs').mkdir()
        (tmp / 'certs' / 'ca.pem').write_text('public\n')
        (tmp / 'private').mkdir(mode=0o750)
        os.chmod(tmp, 0o755)
        fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool'), 'a toolchain may keep its own modes')
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool', public=True),
                         f'{tmp / "private"}, which not every user may read')
        os.chmod(tmp / 'private', 0o755)
        (tmp / 'private' / 'key.pem').write_text('secret\n')
        os.chmod(tmp / 'private' / 'key.pem', 0o640)
        self.assertEqual(sandbox.first_channel(fd, str(tmp), '--tool', public=True),
                         f'{tmp / "private" / "key.pem"}, which not every user may read')
        os.chmod(tmp / 'private' / 'key.pem', 0o644)
        self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool', public=True))
        if os.getuid() != 0:
            # What this user may not read shows nothing inside either, as a root-only file of /etc/default.
            os.chmod(tmp / 'private' / 'key.pem', 0o000)
            self.assertIsNone(sandbox.first_channel(fd, str(tmp), '--tool', public=True))
        # A file every user may read, behind a directory only some may enter, would leave it in a mount.
        os.chmod(tmp / 'private' / 'key.pem', 0o644)
        os.chmod(tmp / 'private', 0o750)
        with self.assertRaises(sandbox.Refused) as caught:
            sandbox.public_fd(tmp / 'private' / 'key.pem', '--tool')
        self.assertIn(f'not every user may pass through {tmp / "private"}', str(caught.exception))
        os.chmod(tmp / 'private', 0o755)
        os.chmod(tmp / 'private' / 'key.pem', 0o640)
        with self.assertRaises(sandbox.Refused) as caught:
            sandbox.public_fd(tmp / 'private' / 'key.pem', '--tool')
        self.assertIn('is not a regular file that every user may read', str(caught.exception))
        os.chmod(tmp / 'private' / 'key.pem', 0o644)
        os.close(sandbox.public_fd(tmp / 'private' / 'key.pem', '--tool'))

    def test_what_is_asked_for_in_etc_or_leads_there_keeps_the_rules_of_etc(self):
        from unittest import mock
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        nested = next((p for p in ('/etc/ssl/certs', '/etc/fonts/conf.d', '/etc/ld.so.conf.d')
                       if Path(p).is_dir() and Path(p).parent != Path('/etc')), None)
        entry = next((p for p in ('/etc/fonts', '/etc/alternatives', '/etc/ld.so.conf.d') if Path(p).is_dir()), None)
        if not nested or not entry:
            self.skipTest('no nested directory of /etc here')
        (tmp / 'alias').symlink_to(entry)
        with mock.patch.object(sandbox, 'public_fd', side_effect=sandbox.Refused('the rules of /etc')):
            for path in (nested, str(tmp / 'alias')):
                with self.subTest(path=path), self.assertRaises(sandbox.Refused) as caught:
                    sandbox.check_tool(path, tmp / 'repo')
                self.assertIn('the rules of /etc', str(caught.exception))
        # The sandbox's own files, and those that name the host, whatever leads there.
        (tmp / 'users').symlink_to('/etc/passwd')
        for path in ('/etc/hosts', '/etc/passwd', '/etc/hostname', str(tmp / 'users')):
            if os.path.exists(path):
                with self.subTest(path=path), self.assertRaises(sandbox.Refused) as caught:
                    sandbox.check_tool(path, tmp / 'repo')
                self.assertIn('the sandbox has its own', str(caught.exception))

    def test_no_tool_covers_a_mount_point_of_the_sandbox(self):
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
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
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        repo, ref = make_repo(tmp)
        pidfile = tmp / 'cat-file.pid'

        def reap():  # also when the run times out, which kills only the runner
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
        self.addCleanup(reap)
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
        from unittest import mock
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)

        def export() -> tuple[int, int, set]:
            (tmp / 'src' / 'export' / 'deep' / 'er').mkdir(parents=True)
            (tmp / 'src' / 'export' / 'deep' / 'er' / 'app.py').write_text('print(1)\n')
            (tmp / 'src' / 'export' / 'link').symlink_to(tmp / 'outside')
            return (os.open(tmp / 'src', os.O_RDONLY | os.O_DIRECTORY),
                    os.open(tmp / 'src' / 'export', os.O_RDONLY | os.O_DIRECTORY), made_of(tmp / 'src' / 'export'))

        def replace() -> None:
            (tmp / 'src' / 'export').rename(tmp / f'moved-{len(list(tmp.glob("moved-*")))}')
            (tmp / 'src' / 'export').mkdir()
            (tmp / 'src' / 'export' / 'keep.txt').write_text('not the export\n')

        (tmp / 'outside').mkdir()
        (tmp / 'outside' / 'keep.txt').write_text('reached through a link\n')
        # Moved before the cleanup: the export is emptied where it went, the directory now at its name stays.
        src_fd, root_fd, made = export()
        replace()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            sandbox.remove_export(src_fd, 'export', root_fd, made)
        self.assertIn('holds another directory now', err.getvalue())
        self.assertEqual(list((tmp / 'moved-0').iterdir()), [])
        # Moved between the check of the name and its removal: only an empty directory can go by name.
        shutil.rmtree(tmp / 'src' / 'export')
        src_fd, root_fd, made = export()
        real_stat = os.stat

        def racing_stat(path, *args, dir_fd=None, **kwargs):
            result = real_stat(path, *args, dir_fd=dir_fd, **kwargs)
            if path == 'export' and dir_fd == src_fd:
                replace()
            return result
        with mock.patch.object(os, 'stat', racing_stat), contextlib.redirect_stderr(io.StringIO()) as err:
            sandbox.remove_export(src_fd, 'export', root_fd, made)
        self.assertIn('was not removed', err.getvalue())
        self.assertTrue((tmp / 'src' / 'export' / 'keep.txt').exists(), 'the replacement stays')
        self.assertEqual(list((tmp / 'moved-1').iterdir()), [], 'the export itself is emptied')
        self.assertTrue((tmp / 'outside' / 'keep.txt').exists(), 'nothing is deleted through a link')
        shutil.rmtree(tmp / 'src' / 'export')
        src_fd, root_fd, made = export()
        sandbox.remove_export(src_fd, 'export', root_fd, made)
        self.assertEqual(list((tmp / 'src').iterdir()), [])

    def test_cleanup_never_descends_into_a_directory_moved_over_one_of_the_exports(self):
        from unittest import mock
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)

        def export() -> tuple[int, int, set]:
            (tmp / 'src' / 'export' / 'deep').mkdir(parents=True)
            (tmp / 'src' / 'export' / 'deep' / 'app.py').write_text('print(1)\n')
            (tmp / 'victim').mkdir()
            (tmp / 'victim' / 'keep.txt').write_text('not the export\n')
            return (os.open(tmp / 'src', os.O_RDONLY | os.O_DIRECTORY),
                    os.open(tmp / 'src' / 'export', os.O_RDONLY | os.O_DIRECTORY), made_of(tmp / 'src' / 'export'))

        # Moved in before the cleanup looks at the name: the directory is not one the export made.
        src_fd, root_fd, made = export()
        (tmp / 'src' / 'export' / 'deep').rename(tmp / 'moved')
        (tmp / 'victim').rename(tmp / 'src' / 'export' / 'deep')
        with contextlib.redirect_stderr(io.StringIO()) as err:
            sandbox.remove_export(src_fd, 'export', root_fd, made)
        self.assertIn('is not a directory of the export', err.getvalue())
        self.assertTrue((tmp / 'src' / 'export' / 'deep' / 'keep.txt').exists(), 'the directory moved in stays whole')
        shutil.rmtree(tmp / 'src')
        shutil.rmtree(tmp / 'moved')
        # Moved in between the look at the name and its opening.
        src_fd, root_fd, made = export()
        real_stat, raced = os.stat, []

        def racing_stat(path, *args, dir_fd=None, **kwargs):
            result = real_stat(path, *args, dir_fd=dir_fd, **kwargs)
            if path == 'deep' and dir_fd is not None and not raced:
                raced.append(path)
                (tmp / 'src' / 'export' / 'deep').rename(tmp / 'moved')
                (tmp / 'victim').rename(tmp / 'src' / 'export' / 'deep')
            return result
        with mock.patch.object(os, 'stat', racing_stat), contextlib.redirect_stderr(io.StringIO()) as err:
            sandbox.remove_export(src_fd, 'export', root_fd, made)
        self.assertEqual(raced, ['deep'])
        self.assertIn('is not a directory of the export', err.getvalue())
        self.assertTrue((tmp / 'src' / 'export' / 'deep' / 'keep.txt').exists(), 'the directory moved in stays whole')

    def test_the_export_takes_no_directory_it_did_not_make(self):
        from unittest import mock
        sandbox = load_sandbox()
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        made = {sandbox.identity(os.fstat(fd))}
        os.close(sandbox.walk(fd, ['pkg', 'mod'], create=True, made=made))
        os.close(sandbox.walk(fd, ['pkg', 'mod'], create=True, made=made))  # the export's own, for its next file
        self.assertEqual(made, made_of(root))
        # Put there by another process while the export was made: the cleanup would empty it later.
        (root / 'lib').mkdir()
        (root / 'lib' / 'keep.txt').write_text('not the export\n')
        with self.assertRaises(sandbox.Rejected) as caught:
            sandbox.walk(fd, ['lib', 'x'], create=True, made=made)
        self.assertIn('a directory the export did not make', str(caught.exception))
        # Moved into the place of a new directory before it was opened.
        real_mkdir = os.mkdir

        def swapping_mkdir(name, mode=0o777, *, dir_fd=None):
            real_mkdir(name, mode, dir_fd=dir_fd)
            if name == 'app':
                os.rename('app', 'app.new', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.rename('lib', 'app', src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        with mock.patch.object(os, 'mkdir', swapping_mkdir), self.assertRaises(sandbox.Rejected) as caught:
            sandbox.walk(fd, ['app'], create=True, made=made)
        self.assertIn('another directory took the place of the new one', str(caught.exception))
        self.assertEqual(made, made_of(root / 'pkg') | {sandbox.identity(os.fstat(fd))})
        self.assertTrue((root / 'app' / 'keep.txt').exists())

    def test_a_link_above_the_scratch_directory_runs_nothing(self):
        # As for promote: a planted runs/scratch link leads the run nowhere, not to a real directory elsewhere.
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        repo, ref = make_repo(tmp)
        runs = repo / 'development' / 'audits' / 'a' / 'runs'
        runs.mkdir(parents=True)
        (tmp / 'elsewhere' / 'hunt-1').mkdir(parents=True)
        (runs / 'scratch').symlink_to(tmp / 'elsewhere', target_is_directory=True)
        r = subprocess.run([sys.executable, str(SANDBOX), 'run', '--scratch', str(runs / 'scratch' / 'hunt-1'), '--ref',
                            ref, '--check', 'c01', '--', 'true'], cwd=repo, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 125, r.stderr)
        self.assertIn('reached without a link', r.stderr)
        self.assertIn('nothing ran', r.stderr)
        self.assertEqual(list((tmp / 'elsewhere' / 'hunt-1').iterdir()), [], 'no export and no evidence there')

    def test_no_tool_exposes_a_credential_store_through_its_link(self):
        from unittest import mock
        sandbox = load_sandbox()
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        sandbox.RUNTIME_ROOTS = ()  # the temporary directory stands in for a toolchain outside /tmp
        (tmp / 'home').mkdir()
        (tmp / 'vault' / 'ssh').mkdir(parents=True)
        (tmp / 'vault' / 'ssh' / 'id_ed25519').write_text('secret\n')
        (tmp / 'home' / '.ssh').symlink_to(tmp / 'vault' / 'ssh', target_is_directory=True)
        with mock.patch.dict(os.environ, {'HOME': str(tmp / 'home')}):
            for tool in (tmp / 'vault', tmp / 'vault' / 'ssh'):
                with self.subTest(tool=tool), self.assertRaises(sandbox.Refused) as caught:
                    sandbox.check_tool(str(tool), REPO)
                self.assertIn('.ssh', str(caught.exception))

    def test_directories_open_without_o_path_too(self):
        # macOS has no O_PATH; promotion, which a source-only audit also runs, must still open its roots.
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / 'a' / 'b').mkdir(parents=True)
        saved = getattr(os, 'O_PATH', None)
        if saved is not None:
            del os.O_PATH
        try:
            sandbox = load_sandbox()
            os.close(sandbox.open_real(tmp / 'a' / 'b', '--scratch'))
        finally:
            if saved is not None:
                os.O_PATH = saved
        self.assertEqual(sandbox.SEARCH_ONLY, os.O_RDONLY)

    def test_the_scope_check_reads_each_controller_on_its_own(self):
        sandbox = load_sandbox()
        r = subprocess.run(['/bin/sh', '-c', sandbox.SCOPE_SCRIPT], capture_output=True, text=True, timeout=30)
        self.assertEqual([line.split('=', 1)[0] for line in r.stdout.splitlines()],
                         ['pids.max', 'memory.max', 'cpu.max'], 'a missing file shifts no other value')

    def test_a_scope_continues_after_an_oom_kill_where_systemd_allows_it(self):
        from unittest import mock
        sandbox = load_sandbox()
        # The running user manager's version, which an upgrade can leave older than systemd-run's.
        cases = (('252.39-1~deb12u1\n', ()), ('253.5-1\n', ('-p', 'OOMPolicy=continue')),
                 ('259.9-0ubuntu0.1\n', ('-p', 'OOMPolicy=continue')), ('v254\n', ('-p', 'OOMPolicy=continue')),
                 ('', ()))
        for version, expected in cases:
            with self.subTest(version=version), mock.patch.object(
                    sandbox.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, version, '')) as run:
                sandbox.oom_policy.cache_clear()
                self.assertEqual(sandbox.oom_policy(), expected)
                self.assertEqual(run.call_args.args[0][:3], [sandbox.SYSTEMCTL, '--user', 'show'])
                scope, _ = sandbox.scope_args(tasks=1, memory_mb=1, cpu_percent=1)
                self.assertEqual('OOMPolicy=continue' in scope, bool(expected))
        sandbox.oom_policy.cache_clear()

    def test_the_guard_starts_nothing_without_the_limits(self):
        sandbox = load_sandbox()
        r = subprocess.run(sandbox.guard_args(tasks=7, memory_mb=1, cpu_percent=1) + ['echo', 'started'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 125)
        self.assertIn('does not limit tasks', r.stderr)
        self.assertNotIn('started', r.stdout)


class SandboxRun(unittest.TestCase):
    """The controls hold inside a real sandbox. Skipped only where the host cannot run one at all."""

    def setUp(self):
        if CAPABILITY:
            self.skipTest(CAPABILITY)
        self.tmp = Path(tempfile.mkdtemp()).resolve()
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
        # Forks counted by the fixture itself: a shell's diagnostic and its retries differ (dash, bash).
        script = ('import os, time\n'
                  'for n in range(30):\n'
                  '    try:\n'
                  '        if os.fork() == 0:\n'
                  '            time.sleep(2)\n'
                  '            os._exit(0)\n'
                  '    except BlockingIOError:\n'
                  '        print("refused after", n)\n'
                  '        break\n'
                  'else:\n'
                  '    print("spawned")\n'
                  'while True:\n'
                  '    try:\n'
                  '        os.wait()\n'
                  '    except ChildProcessError:\n'
                  '        break\n')
        r = self.run_check('c16', 'python3', '-c', script, options=('--tasks', '10'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('refused after', r.stdout)
        self.assertLess(int(r.stdout.split()[-1]), 10, 'the scope counts every task of the sandbox')
        r = self.run_check('c18', 'python3', '-c', script, options=('--tasks', '64'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'spawned\n')

    def test_a_check_past_its_memory_limit_is_killed_without_failing_its_scope(self):
        # A scope that fails with 'oom-kill' makes KDE and GNOME report that the system is low on memory.
        r = self.run_check('c17', 'python3', '-c', 'x = bytearray(400 * 1024 * 1024); print("allocated")',
                           options=('--memory-mb', '64'))
        self.assertEqual(r.returncode, 128 + 9, r.stderr)
        self.assertNotIn('allocated', r.stdout)
        if not load_sandbox().oom_policy():
            self.skipTest('systemd before 253: a scope takes no OOMPolicy')
        scope, log, deadline = self.evidence('c17')['scope'], '', time.monotonic() + 10
        while 'OOM killer' not in log and time.monotonic() < deadline:
            time.sleep(0.2)
            log = subprocess.run(['journalctl', '--user', '-u', scope, '-o', 'cat', '--no-pager'],
                                 capture_output=True, text=True).stdout
        if 'OOM killer' not in log:
            self.skipTest(f'the user journal does not show {scope}')
        time.sleep(1)
        log = subprocess.run(['journalctl', '--user', '-u', scope, '-o', 'cat', '--no-pager'],
                             capture_output=True, text=True).stdout
        self.assertNotIn("Failed with result 'oom-kill'", log)

    def test_the_probe_enforces_every_control_where_the_host_can_run_a_sandbox(self):
        self.assertIsNone(PROBE_FAILURE, 'the host runs sandboxes, so a missing control is the runner\'s fault')

    def test_the_target_sees_only_the_etc_entries_toolchains_read(self):
        sandbox = load_sandbox()
        r = self.run_check('c21', 'sh', '-c', 'ls -A /etc; python3 -c "import getpass, pwd; '
                           'print(\'user=\' + getpass.getuser(), len(pwd.getpwall()))"')
        self.assertEqual(r.returncode, 0, r.stderr)
        seen = set(r.stdout.splitlines()[:-1])
        self.assertNotIn('environment', seen, 'host files of /etc beyond the toolchains\' stay out')
        self.assertEqual(r.stdout.splitlines()[-1], 'user=sandbox 1', 'passwd names only the sandbox\'s user')
        self.assertLessEqual({n for n in seen if not sandbox.ETC_PATTERN.fullmatch(n)},
                             {e.split('/')[0] for e in sandbox.ETC_ENTRIES} | set(sandbox.ETC_GENERATED))
        # Of ssl and pki only the public certificates and the OpenSSL configuration.
        r = self.run_check('c24', 'sh', '-c', 'ls -A /etc/ssl /etc/pki /etc/pki/tls 2>/dev/null')
        self.assertNotIn('private', r.stdout.split(), 'a key the group ssl-cert may read stays out')
        # The host's name and network names stay out too.
        r = self.run_check('c25', 'sh', '-c', 'uname -n; cat /etc/hosts')
        self.assertEqual(r.stdout.splitlines()[0], 'sandbox')
        host = socket.gethostname()
        if host not in ('localhost', 'sandbox'):
            self.assertNotIn(host, r.stdout)
        extra = next((p for p in ('/etc/default', '/etc/apt', '/etc/skel') if Path(p).is_dir()), None)
        if extra:
            r = self.run_check('c22', 'sh', '-c', f'[ -d {extra} ] && echo mounted', options=('--tool', extra))
            self.assertEqual(r.stdout.strip(), 'mounted', r.stderr)
        # A file of /etc is an entry too, mounted where it was asked for.
        entry = next((p for p in ('/etc/environment', '/etc/hostname', '/etc/issue') if Path(p).is_file()
                      and os.stat(p).st_mode & 0o004), None)
        if entry:
            r = self.run_check('c26', 'cat', entry, options=('--tool', entry))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, Path(entry).read_text())
        if Path('/etc/ssl/private').is_dir() and not os.stat('/etc/ssl/private').st_mode & 0o001:
            r = self.run_check('c27', 'true', options=('--tool', '/etc/ssl'))
            self.assertEqual(r.returncode, 125)
            # Readable to the group ssl-cert: a credential; not readable to this user: it cannot be checked.
            self.assertRegex(r.stderr, r'/etc/ssl/private, which not every user may read|cannot be checked')
        r = self.run_check('c23', 'true', options=('--tool', '/etc'))
        self.assertEqual(r.returncode, 125)
        self.assertIn('must not mount all of /etc', r.stderr)

    def test_only_work_and_shared_memory_take_writes_each_with_its_size(self):
        script = ('for d in / /etc /usr /dev /proc /src; do (: > "$d/x") 2>/dev/null && echo "writable $d"; done; '
                  ': > /dev/shm/x && echo shm; grep -E " /dev/shm | /work " /proc/self/mountinfo')
        r = self.run_check('c28', 'sh', '-c', script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('writable', r.stdout, 'bwrap\'s root and /dev are read-only')
        self.assertIn('shm', r.stdout.splitlines())
        sizes = {line.split()[4]: line.rsplit(' ', 1)[-1] for line in r.stdout.splitlines()[1:]}
        self.assertIn(f'size={load_sandbox().SHM_BYTES // 1024}k', sizes['/dev/shm'].split(','))
        self.assertIn(f'size={256 * 1024}k', sizes['/work'].split(','), 'the default --work-mb')

    def test_a_check_whose_sandbox_never_started_in_time_leaves_no_record(self):
        from unittest import mock
        sandbox = load_sandbox()
        limits = {name: default for name, (default, _) in sandbox.LIMITS.items()} | {'timeout': 1}
        slow = (['/bin/sh', '-c', 'sleep 10; exec "$@"', 'slow-scope'], 'wf-sandbox-slow')
        with mock.patch.object(sandbox, 'scope_args', return_value=slow), self.assertRaises(sandbox.Refused) as caught:
            sandbox.sandboxed(['true'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV}, binds=[], chdir='/',
                              limits=limits, guard=False)
        self.assertIn('ran out before the command started', str(caught.exception))
        # bwrap reports its child before it has set up the sandbox: only the launch counts.
        real = sandbox.bwrap_args

        def slow_setup(**kwargs):
            args = real(**kwargs)
            at = len(args) - args[::-1].index('--')
            return args[:at] + ['/bin/sh', '-c', 'sleep 10; exec "$@"', 'slow-setup'] + args[at:]
        with mock.patch.object(sandbox, 'bwrap_args', slow_setup), self.assertRaises(sandbox.Refused) as caught:
            sandbox.sandboxed(['true'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV}, binds=[], chdir='/',
                              limits=limits)
        self.assertIn('ran out before the command started', str(caught.exception))
        result = sandbox.sandboxed(['sleep', '10'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV}, binds=[],
                                   chdir='/', limits=limits)
        self.assertEqual((result['status'], result['killed']), (124, 'timeout'), 'a command that ran times out')
        # The launcher reports the start only once the exec succeeded: one held up before it never ran.
        held = sandbox.LAUNCHER.replace('\ntry:\n    execute()', '\nimport time\ntime.sleep(10)\ntry:\n    execute()')
        self.assertNotEqual(held, sandbox.LAUNCHER)
        with mock.patch.object(sandbox, 'LAUNCHER', held), self.assertRaises(sandbox.Refused) as caught:
            sandbox.sandboxed(['true'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV}, binds=[], chdir='/',
                              limits=limits)
        self.assertIn('ran out before the command started', str(caught.exception))
        # A "." that comes only after the deadline, as from a watcher that outlived the launcher the kill ended.
        late = held.replace('report = os.read(done, 512)', 'time.sleep(1.4)\n        report = b""')
        real_stop = sandbox.stop

        def slow_stop(process, unit):
            time.sleep(0.8)
            real_stop(process, unit)
        with mock.patch.object(sandbox, 'LAUNCHER', late.replace('import errno,', 'import time, errno,')), \
                mock.patch.object(sandbox, 'stop', slow_stop), self.assertRaises(sandbox.Refused) as caught:
            sandbox.sandboxed(['true'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV}, binds=[], chdir='/',
                              limits=limits)
        self.assertIn('ran out before the command started', str(caught.exception))
        result = sandbox.sandboxed(['ls', '/proc/self/fd'], env={'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV},
                                   binds=[], chdir='/', limits=limits)
        self.assertEqual(result['stdout'].decode().split(), ['0', '1', '2', '3'],
                         'the command keeps no descriptor of the launch (3 is ls reading the list)')

    def test_the_command_gets_exactly_its_environment_or_does_not_start(self):
        sandbox = load_sandbox()
        limits = {name: default for name, (default, _) in sandbox.LIMITS.items()}
        env = {'PATH': sandbox.DEFAULT_PATH, **sandbox.FIXED_ENV, 'PWD': '/src', 'SHELLOPTS': 'xtrace'}
        result = sandbox.sandboxed(['env'], env=env, binds=[], chdir='/', limits=limits)
        self.assertEqual(result['stdout'].decode(), ''.join(f'{name}={value}\n' for name, value in env.items()),
                         'nothing is added, removed or changed on the way, whatever /bin/sh is')
        # The environment acts on the command alone, not on the step that starts it.
        result = sandbox.sandboxed(['/usr/bin/true'], env={**env, 'LD_TRACE_LOADED_OBJECTS': '1'}, binds=[], chdir='/',
                                   limits=limits)
        self.assertEqual(result['status'], 0, result['stderr'])
        self.assertIn(b'libc.so', result['stdout'])
        result = sandbox.sandboxed(['grep', '^SigIgn', '/proc/self/status'], env=env, binds=[], chdir='/',
                                   limits=limits)
        self.assertEqual(result['stdout'].split(), [b'SigIgn:', b'0' * 16], 'no signal ignored, as for any command')
        # A command whose exec fails did not run, whatever the reason.
        tools = public_tmp()
        self.addCleanup(shutil.rmtree, tools)
        (tools / 'missing-interpreter').write_text('#!/nonexistent/interpreter\necho ran\n')
        (tools / 'not-executable').write_text('echo ran\n')
        (tools / 'no-interpreter-line').write_text('echo ran as a shell script\n')
        os.chmod(tools / 'missing-interpreter', 0o755)
        os.chmod(tools / 'no-interpreter-line', 0o755)
        for command in ('/opt/t/missing-interpreter', '/opt/t/not-executable', 'no-such-command'):
            with self.subTest(command=command), self.assertRaises(sandbox.Refused) as caught:
                sandbox.sandboxed([command], env=env, binds=[(str(tools), '/opt/t')], chdir='/', limits=limits)
            self.assertIn(f'the command did not start: {command}: ', str(caught.exception))
        result = sandbox.sandboxed(['/opt/t/no-interpreter-line'], env=env, binds=[(str(tools), '/opt/t')], chdir='/',
                                   limits=limits)
        self.assertEqual(result['stdout'], b'ran as a shell script\n', 'as execvp runs it')

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
        self.tmp = Path(tempfile.mkdtemp()).resolve()
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

    def local_check(self, run: Path, agent: str, ref: str | None = None, stdout: bytes = b'changed\n') -> dict:
        """A local check whose evidence the agent's sandbox left in its scratch, for the parent to promote: the
        runner's record of the audited commit (or of ref) and the two streams it describes."""
        evidence = run / 'runs' / 'scratch' / agent / 'evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        streams = {'stdout': b'changed\n', 'stderr': b''}
        record = {'check': 'c01', 'ref': ref or json.loads((run / 'run-metadata.json').read_text())['source_ref'],
                  'status': 0, **{name: {'bytes': len(data), 'kept': len(data), 'truncated': False,
                                         'sha256': hashlib.sha256(data).hexdigest()} for name, data in streams.items()}}
        (evidence / 'c01.out').write_bytes(stdout)
        (evidence / 'c01.err').write_bytes(streams['stderr'])
        (evidence / 'c01.json').write_text(json.dumps(record))
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

    def test_installed_dependencies_hide_no_agent_configuration_at_the_gate(self):
        (self.repo / '.gitignore').write_text('/development\n/.opencode/\n')
        self.commit('ignore the local OpenCode directory')
        for n in range(30):
            (self.repo / '.opencode' / 'node_modules' / f'pkg{n:02d}').mkdir(parents=True)
            (self.repo / '.opencode' / 'node_modules' / f'pkg{n:02d}' / 'index.js').write_text('')
        (self.repo / '.opencode' / 'plugins').mkdir()
        (self.repo / '.opencode' / 'plugins' / 'local.js').write_text('// mine\n')
        (self.repo / '.opencode' / 'plugins' / 'node_modules').write_text('a file of that name is no tree\n')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude')
        summary = '.opencode/ (32 files: node_modules/ (30 files), plugins/local.js, plugins/node_modules)'
        self.assertIn(summary, r.stderr)
        self.assertNotIn('pkg00', r.stderr)
        run = Path(r.stdout.strip())
        self.assertEqual(len(json.loads((run / 'run-metadata.json').read_text())['local_agent_config']), 32,
                         'the record keeps every file')
        self.assertIn(summary, self.audit('status', str(run)).stdout)
        audit = load_script(AUDIT)
        self.assertEqual(audit.config_summary(['opencode.json', '.claude/settings.json', '.claude/hooks/a.sh',
                                               '.claude/hooks/b.sh'], limit=2),
                         'opencode.json; .claude/ (3 files: settings.json, hooks/a.sh and 1 more)')

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
        self.assertIn('outside the sandbox: .opencode/ (1 file: plugin/start.js)', r.stderr)
        run = Path(r.stdout.strip())
        meta = json.loads((run / 'run-metadata.json').read_text())
        self.assertEqual(meta['agent_config'], ['.opencode/plugin/start.js'], 'an installed wf skill link is no config')
        self.assertIn('agent configuration committed in the target: .opencode/ (1 file: plugin/start.js)',
                      self.audit('status', str(run)).stdout)
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
        self.assertIn('promoted 3 evidence file(s)', out)
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
        # A local check counts only with the runner's record of it, which the promoted output must match.
        self.audit('prompt', str(run), 'candidate', '--fingerprint', candidate['fingerprint'], '--agent', 'verify-02b')
        self.result(run, 'verify-02b', {'decision': 'confirmed', 'record': record, 'sandbox_probe': 'pass',
                                        'checks': [self.local_check(run, 'verify-02b', stdout=b'forged\n')]})
        self.assertIn('is not the output it records', self.audit('apply', str(run), 'candidate', '--agent',
                                                                  'verify-02b', ok=False).stdout)
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
        self.assertEqual([c['agent_id'] for c in self.ledger(run)[0]['attempts'][0]['local_checks']],
                         ['hunt-w1-01', 'verify-003', 'verify-003'],
                         'the deferred unit keeps the verifier\'s checks in the attempt that found the candidate')
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
        # Reports describe the finalization they were written for: a new final needs new reports.
        self.assertIn('exist from an earlier final', self.audit('final', str(run), ok=False).stderr)
        for name in ('REPORT.md', 'FINDINGS-DETAIL.md', 'NEEDS-VALIDATION.md'):
            (run / name).unlink()
        self.audit('final', str(run))
        self.assertIn('write REPORT.md', self.audit('done', str(run), ok=False).stderr)
        for name in ('REPORT.md', 'FINDINGS-DETAIL.md', 'NEEDS-VALIDATION.md'):
            (run / name).write_text(f'# {name}\n')
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
        # Another root cause: what found it goes with it, not with the fingerprint it was given to check.
        self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--reverify', '--agent', 'record-005')
        found = self.local_check(run, 'record-005')
        self.result(run, 'record-005', {'decision': 'replace', 'reason': 'Another cause.', 'sandbox_probe': 'pass',
                                        'record': needs_validation('app-other-root-cause'), 'checks': [found]})
        self.audit('apply', str(run), 'record', '--agent', 'record-005')
        state = json.loads((run / 'state.json').read_text())
        self.assertNotIn('record-005', json.dumps(state['candidates'][fp].get('reverify_checks', [])))
        self.assertEqual(state['candidates'][fp]['state'], 'needs_reverification')
        other = state['candidates']['app-other-root-cause']
        self.assertEqual((other['state'], other['origins'], other['discovery_checks']), ('pending', [fp], [found]))
        self.audit('prompt', str(run), 'record', '--fingerprint', fp, '--reverify', '--agent', 'record-004')
        self.result(run, 'record-004', {'decision': 'verified', 'fingerprint': fp, 'sandbox_probe': 'pass',
                                        'checks': [source_check('record-004'), self.local_check(run, 'record-004')]})
        # An apply that stopped after its promotion and before it saved: the next one keeps the same copies.
        allowlist = json.loads((run / 'state.json').read_text())['allowlists']['record-004']
        subprocess.run([sys.executable, str(SANDBOX), 'promote', '--scratch', str(run / 'runs' / 'scratch' / 'record-004'),
                        '--artifacts', str(run / 'agents' / 'record-004' / 'artifacts'), '--total', '1000000',
                        *[a for item in allowlist for a in ('--allow', item)]], check=True, capture_output=True)
        self.audit('apply', str(run), 'record', '--agent', 'record-004')
        self.assertIn('agents/record-004/artifacts/evidence/c01.json',
                      json.dumps(self.ledger(run)[0]['local_checks']), 'its reproduction is in the ledger')
        other = 'app-other-root-cause'
        self.audit('prompt', str(run), 'candidate', '--fingerprint', other, '--agent', 'verify-006')
        prompt = (run / 'runs' / 'verify-006.prompt.md').read_text()
        self.assertIn('and those of the verifier that found this root cause', prompt)
        self.assertIn('agents/record-005/artifacts/evidence/c01.json', prompt, 'the evidence that found it goes with it')
        self.result(run, 'verify-006', {'decision': 'needs_validation', 'record': needs_validation(other),
                                        'checks': [source_check('verify-006')], 'sandbox_probe': 'pass'})
        self.audit('apply', str(run), 'candidate', '--agent', 'verify-006')
        self.audit('prompt', str(run), 'record', '--fingerprint', other, '--agent', 'record-006')
        self.result(run, 'record-006', {'decision': 'verified', 'fingerprint': other})
        self.audit('apply', str(run), 'record', '--agent', 'record-006')
        out = self.audit('final', str(run)).stdout
        self.assertIn('complete: confirmed 1', out)
        self.assertEqual(self.ledger(run)[0]['decisions'], {fp: 'confirmed', other: 'needs_validation'},
                         'the ledger follows the final verdict')
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
            meta, state = {'source_only': True}, {}
        self.assertIn('confirms nothing', audit.local_reproduction(SourceOnly, 'v', 'confirmed', [source_check('v')])[0])
        self.assertEqual(audit.local_reproduction(SourceOnly, 'v', 'needs_validation', [source_check('v')]), [])

    def test_linked_config_roots_and_codex_skills_are_agent_configuration(self):
        (self.repo / 'cfg').mkdir()
        (self.repo / 'cfg' / 'settings.json').write_text('{"hooks": {}}\n')
        (self.repo / '.gemini').symlink_to('cfg', target_is_directory=True)
        (self.repo / '.agents' / 'skills' / 'helper').mkdir(parents=True)
        (self.repo / '.agents' / 'skills' / 'helper' / 'SKILL.md').write_text('---\nname: helper\n---\n')
        (self.repo / '.agents' / 'skills' / 'wf-sec-audit').symlink_to(REPO / 'skills' / 'wf-sec-audit')
        (self.repo / '.codex').mkdir()
        (self.repo / '.codex' / 'a\tb.toml').write_text('# a name git quotes\n')
        (self.repo / '.claude' / 'skills' / 'two\nlines').mkdir(parents=True)
        (self.repo / '.claude' / 'skills' / 'two\nlines' / 'SKILL.md').write_text('---\nname: x\n---\n')
        # A backslash and an n is another name than a newline, and must look so.
        (self.repo / '.claude' / 'skills' / 'two\\nlines').mkdir()
        (self.repo / '.claude' / 'skills' / 'two\\nlines' / 'SKILL.md').write_text('---\nname: y\n---\n')
        # A character that does not print shows as \\uNNNN, never as the \\xNN of a byte.
        (self.repo / '.codex' / '\u0085.toml').write_text('# U+0085\n')
        # A name that is not UTF-8, in the configuration and elsewhere: nothing stops the gate or hides it.
        # (APFS and other file systems that take UTF-8 names only refuse to create one.)
        raw = []
        for name in (b'.codex/\x85.toml', b'.codex/\xff.toml', b'latin-\xe9.txt'):
            try:
                with open(os.path.join(os.fsencode(self.repo), name), 'w') as handle:
                    handle.write('x\n')
            except OSError:
                continue
            raw.append(name)
        self.commit('agent configuration elsewhere')
        run = Path(self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude').stdout.strip())
        self.assertEqual(json.loads((run / 'run-metadata.json').read_text())['agent_config'],
                         ['.agents/skills/helper/SKILL.md', '.claude/skills/two\\nlines/SKILL.md',
                          '.claude/skills/two\\\\nlines/SKILL.md', '.codex/a\\tb.toml',
                          *(['.codex/\\x85.toml'] if b'.codex/\x85.toml' in raw else []), '.codex/\\u0085.toml',
                          *(['.codex/\\xff.toml'] if b'.codex/\xff.toml' in raw else []), '.gemini'])

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

    def test_the_final_clean_critic_follows_an_applied_post_wave_critic(self):
        run = self.start(profile='standard', budget=40)
        cid = self.coverage_id(run)
        clean = {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [], 'stop': True}
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        # Its result may still name gaps: the final-clean pass waits for it, not only for its prompt.
        r = self.audit('prompt', str(run), 'critic', '--wave', '1', '--final-clean', '--agent', 'critic-final-w1',
                       ok=False)
        self.assertIn('comes before the final-clean critic: apply its result first', r.stderr)
        self.result(run, 'critic-w1', clean)
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--final-clean', '--agent', 'critic-final-w1')

    def test_a_save_that_stopped_halfway_is_finished_before_the_records_are_read(self):
        from unittest import mock
        run = self.start()
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        candidate = needs_validation()
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01', candidate=candidate))
        audit = load_script(AUDIT)
        real, calls = audit.write_json, []

        def stop_halfway(path, value):
            calls.append(path.name)
            if len(calls) == 2:
                raise KeyboardInterrupt('the parent stopped between two files of one save')
            real(path, value)
        with mock.patch.object(audit, 'write_json', stop_halfway), self.assertRaises(KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            audit.main(['apply', str(run), 'hunter', '--agent', 'hunt-w1-01'])
        # The next command finds the ledger, the state and the metadata of one save, never a mix.
        status = self.audit('status', str(run)).stdout
        state = json.loads((run / 'state.json').read_text())
        self.assertIn(candidate['fingerprint'], state['candidates'], status)
        self.assertIn('hunt-w1-01', state['applied'])
        self.assertNotEqual(self.ledger(run)[0]['status'], 'in_progress')
        self.assertIn(candidate['fingerprint'], self.ledger(run)[0]['result_fingerprints'])
        self.assertFalse((run / '.save.json').exists())
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.assertIn('was applied already', r.stderr)

    def test_a_final_clean_pass_whose_proposals_were_all_rejected_is_clean(self):
        run = self.start(profile='standard', budget=40)
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        clean = {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [], 'stop': True}
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.result(run, 'critic-w1', clean)
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        gaps = [unit_spec('vendor/x.py#y') | {'starting_paths': ['vendor/x.py'], 'reason': 'Outside the scope.'},
                unit_spec('b.py#x') | {'starting_paths': ['b.py'], 'reason': 'A duplicate.'}]
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--final-clean', '--agent', 'critic-final-w1')
        self.result(run, 'critic-final-w1', clean | {'missing_units': gaps})
        self.assertIn('work remains', self.audit('apply', str(run), 'critic', '--agent', 'critic-final-w1').stdout)
        units = self.tmp / 'out-of-scope.json'
        units.write_text(json.dumps([unit_spec('vendor/x.py#y') | {'starting_paths': ['vendor/x.py']}]))
        self.audit('seed', str(run), str(units), '--status', 'out_of_scope', '--reason', 'vendored code')
        self.assertIn('not seeded yet', self.audit('final', str(run), ok=False).stderr)
        self.audit('dismiss', str(run), '1', '--reason', 'the same entry as app.py#main')
        # Upstream's parent rejects both: the pass asked for no accepted work, and coverage is complete.
        self.assertIn('complete: no records', self.audit('final', str(run)).stdout)

    def test_a_proposal_a_unit_of_the_seed_file_accepts_counts_as_accepted_wherever_it_stands(self):
        run = self.start(profile='standard', budget=40)
        cid = self.coverage_id(run)
        self.audit('prompt', str(run), 'hunter', '--units', cid, '--wave', '1', '--agent', 'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        clean = {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [], 'stop': True}
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.result(run, 'critic-w1', clean)
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        known = unit_spec('b.py#x') | {'starting_paths': ['b.py']}
        units = self.tmp / 'units-b.json'
        units.write_text(json.dumps([known]))
        self.audit('seed', str(run), str(units), '--status', 'out_of_scope', '--reason', 'another team owns b.py')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--final-clean', '--agent', 'critic-final-w1')
        self.result(run, 'critic-final-w1', clean | {'missing_units': [known | {'reason': 'A second owner check.'}]})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-final-w1')
        # The unit that was there already comes first; the new one, with the same labels, takes the proposal.
        other = known | {'lifecycle': 'after login', 'canonical_refs': known['canonical_refs'] | {'lifecycle': 'b.py#login'}}
        units.write_text(json.dumps([known, other]))
        self.audit('seed', str(run), str(units), '--status', 'deferred', '--reason', 'needs the deployed router')
        state = json.loads((run / 'state.json').read_text())
        self.assertEqual([e['accepted'] for e in state['resolved_proposals']], [True])
        self.assertIn('no clean final-clean critic pass', self.audit('final', str(run), ok=False).stderr)

    def test_a_proposal_the_parent_accepted_keeps_the_final_clean_pass_unclean(self):
        audit = load_script(AUDIT)
        critic = {'agent': 'critic-final-w1', 'kind': 'final-clean', 'wave': 1, 'clean': False, 'applied': 'x',
                  'capped': [], 'reopened': []}

        class FakeRun:
            meta = {'profile': 'standard'}
            ledger = [{'status': 'covered', 'wave': 1, 'attempts': []}]
            state = {'critics': [critic], 'missing_units': [], 'candidates': {},
                     'resolved_proposals': [{'from': 'critic-final-w1', 'accepted': True}]}
        self.assertFalse(audit.clean_final_pass(FakeRun()), 'a unit seeded to hunt is work it found')
        FakeRun.state['resolved_proposals'][0]['accepted'] = False
        self.assertTrue(audit.clean_final_pass(FakeRun()))
        FakeRun.state['missing_units'] = [{'from': 'critic-final-w1'}]
        self.assertFalse(audit.clean_final_pass(FakeRun()), 'a proposal not triaged yet')
        FakeRun.state['missing_units'] = []
        critic['reopened'] = ['u1']
        self.assertFalse(audit.clean_final_pass(FakeRun()), 'a reopened unit is work')

    def test_archived_attempts_and_the_next_wave_need_their_own_final_clean_pass(self):
        audit = load_script(AUDIT)

        class FakeRun:
            meta = {'profile': 'standard'}
            # Hunted in wave 2, reopened and then deferred by the parent: wave 2 still needs a final pass.
            ledger = [{'status': 'deferred', 'wave': 3, 'attempts': [{'wave': 1}, {'wave': 2}]}]
            state = {'critics': [{'kind': 'post-wave', 'clean': True, 'wave': 1},
                                 {'kind': 'final-clean', 'clean': True, 'wave': 1}], 'candidates': {}}
        self.assertFalse(audit.clean_final_pass(FakeRun()))
        FakeRun.ledger = [{'status': 'covered', 'wave': 1, 'attempts': []}]
        self.assertTrue(audit.clean_final_pass(FakeRun()))
        self.assertFalse(audit.clean_final_pass(FakeRun(), 2), 'a hunter of wave 2 needs a new final pass')
        # A wave-2 hunter keeps its post-wave critic, a new final-clean critic and 30% of the rest reserved.
        self.assertEqual(audit.reserves(FakeRun(), 2, 10), 2 + 3)

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

    def test_only_the_runners_record_of_the_audited_commit_backs_a_confirmation(self):
        audit = load_script(AUDIT)
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, tmp)
        evidence = tmp / 'agents' / 'verify-1' / 'artifacts' / 'evidence'
        evidence.mkdir(parents=True)

        class FakeRun:
            dir, meta = tmp, {'source_ref': 'a' * 40}

        def write(ref: str = 'a' * 40, check: str = 'c01', stdout: bytes = b'changed\n') -> None:
            (evidence / 'c01.out').write_bytes(stdout)
            (evidence / 'c01.err').write_bytes(b'')
            record = {'check': check, 'ref': ref, **{name: {'kept': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                                       for name, data in (('stdout', b'changed\n'), ('stderr', b''))}}
            (evidence / 'c01.json').write_text(json.dumps(record))
        artifact = 'agents/verify-1/artifacts/evidence/c01.json'
        write()
        self.assertIsNone(audit.sandbox_record(FakeRun, artifact))
        for kwargs, message in (({'ref': 'b' * 40}, 'records no run of c01 on the audited commit'),
                                ({'check': 'c02'}, 'records no run'), ({'stdout': b'forged\n'}, 'not the output')):
            with self.subTest(kwargs=kwargs):
                write(**kwargs)
                self.assertIn(message, audit.sandbox_record(FakeRun, artifact))
        write()
        (evidence / 'c01.err').unlink()
        self.assertIn('.err file of', audit.sandbox_record(FakeRun, artifact))
        self.assertIn('no check record', audit.sandbox_record(FakeRun, 'agents/verify-1/artifacts/evidence/c01.out'))

    def test_a_failed_probe_settles_nothing_also_after_the_switch_to_source_only(self):
        run = self.start()
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        first, second = needs_validation(), needs_validation('app-owner-check-second')
        result = self.hunter_result(run, 'hunt-w1-01', first)
        result['candidates'].append({k: v for k, v in second.items() if k != 'verdict'} |
                                    {'proposed_verdict': 'needs_validation'})
        result['units'][0]['candidate_fingerprints'].append(second['fingerprint'])
        self.result(run, 'hunt-w1-01', result)
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.result(run, 'critic-w1', {'missing_units': [], 'reassign_ids': [], 'resolved_prior_leads': [],
                                       'stop': True})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')

        def verifier(agent: str, record: dict, probe: str) -> None:
            self.audit('prompt', str(run), 'candidate', '--fingerprint', record['fingerprint'], '--agent', agent)
            self.result(run, agent, {'decision': 'needs_validation', 'record': record,
                                     'checks': [source_check(agent)], 'sandbox_probe': probe})

        def state(record: dict) -> str:
            return json.loads((run / 'state.json').read_text())['candidates'][record['fingerprint']]['state']
        # Both verifiers ran in one batch; the first failure makes the human switch to source-only.
        verifier('verify-001', first, 'fail: no-network')
        verifier('verify-002', second, 'fail: no-network')
        self.assertEqual(self.audit('apply', str(run), 'candidate', '--agent', 'verify-001', ok=False).returncode, 4)
        self.audit('approve', str(run), '--source-only', '--words', 'go on without local checks')
        out = self.audit('apply', str(run), 'candidate', '--agent', 'verify-002').stdout
        self.assertIn('launched before the switch to source-only', out)
        self.assertEqual(state(second), 'pending', 'its sandbox failed: what it decided settles nothing')
        # A verifier launched source-only runs no probe, and may settle what needs no local result.
        verifier('verify-003', second, 'pass')
        self.assertIn('runs no probe', self.audit('apply', str(run), 'candidate', '--agent', 'verify-003',
                                                  ok=False).stdout)
        verifier('verify-004', second, 'not_run')
        self.audit('apply', str(run), 'candidate', '--agent', 'verify-004')
        self.assertEqual(state(second), 'final')

    def test_verifier_checks_join_the_attempt_that_found_their_fingerprint(self):
        audit = load_script(AUDIT)

        def attempt(agent: str, fps: list[str]) -> dict:
            return {'wave': 1, 'status': 'candidate', 'agent_id': agent, 'reviewed_paths': ['app.py'],
                    'local_checks': [source_check(agent)], 'result_fingerprints': fps, 'unresolved': [],
                    'reassignment_reason': 'reassigned'}
        live = {'coverage_id': 'u1', 'wave': 2, 'attempts': [attempt('hunt-w1-01', ['fp-a'])]} | \
            attempt('hunt-w2-01', ['fp-b'])
        deferred = {'coverage_id': 'u2', 'wave': 2, 'status': 'deferred', 'agent_id': None, 'reviewed_paths': [],
                    'local_checks': [], 'result_fingerprints': [], 'unresolved': ['quick_profile_final_critic'],
                    'attempts': [attempt('hunt-w1-02', ['fp-a'])]}

        class FakeRun:
            ledger = [live, deferred]

            def unit(self, coverage_id):
                return next(u for u in self.ledger if u['coverage_id'] == coverage_id)
        checks = [source_check('verify-001') | {'reviewed_paths': ['lib.py']}]
        audit.add_checks(FakeRun(), {'fingerprint': 'fp-a', 'units': ['u1', 'u2']}, 'verify-001', checks)
        self.assertEqual([c['agent_id'] for c in live['local_checks']], ['hunt-w2-01'],
                         'the attempt that found fp-b holds no evidence about fp-a')
        for unit in (live, deferred):
            self.assertEqual([c['agent_id'] for c in unit['attempts'][0]['local_checks']], [unit['attempts'][0]['agent_id'],
                                                                                         'verify-001'])
            self.assertEqual(unit['attempts'][0]['reviewed_paths'], ['app.py', 'lib.py'])
        self.assertEqual(deferred['local_checks'], [])
        # A root cause a verifier found instead joins the attempt of the fingerprint it came from.
        audit.add_checks(FakeRun(), {'fingerprint': 'fp-c', 'origins': ['fp-b'], 'units': ['u1']}, 'verify-002',
                         [source_check('verify-002')])
        self.assertEqual(live['local_checks'][-1]['agent_id'], 'verify-002')

    def test_a_proposal_is_resolved_only_by_a_unit_that_covers_it(self):
        run = self.start(profile='standard', budget=40)
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        self.result(run, 'hunt-w1-01', self.hunter_result(run, 'hunt-w1-01'))
        self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01')
        # An unchecked parallel path with the labels of the existing unit.
        parallel = {k: v for k, v in unit_spec().items() if k in ('surface', 'boundary', 'subsystem', 'attack_class')} \
            | {'starting_paths': ['lib/parallel.py'], 'reason': 'An unchecked parallel path.'}
        self.audit('prompt', str(run), 'critic', '--wave', '1', '--agent', 'critic-w1')
        self.result(run, 'critic-w1', {'missing_units': [parallel], 'reassign_ids': [], 'resolved_prior_leads': [],
                                       'stop': False})
        self.audit('apply', str(run), 'critic', '--agent', 'critic-w1')
        units = self.tmp / 'units-2.json'
        for listed in ([unit_spec('b.py#x')], [unit_spec()]):
            units.write_text(json.dumps(listed))
            self.assertIn('0 proposal(s) resolved, 1 left', self.audit('seed', str(run), str(units)).stdout,
                          'neither an unrelated unit nor the one that starts elsewhere covers it')
        # The ledger holds one unit per label tuple, so only the parent's recorded decision settles it.
        self.assertIn('to seed 1 (missing_units, from critic-w1)', self.audit('status', str(run)).stdout)
        self.assertIn('0 left', self.audit('dismiss', str(run), '1', '--reason',
                                           'seeded as entry lib/parallel.py#main').stdout)

    def test_a_proposal_with_a_label_that_is_no_text_is_discarded(self):
        run = self.start(profile='standard', budget=40)
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-01')
        result = self.hunter_result(run, 'hunt-w1-01')
        proposal = {'surface': ['entry b.py#x'], 'boundary': 'owner check', 'subsystem': 'app',
                    'attack_class': 'Access control', 'starting_paths': ['b.py'], 'reason': 'Another entry.'}
        self.result(run, 'hunt-w1-01', result | {'uncovered': [proposal]})
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-01', ok=False)
        self.assertIn('uncovered[0].surface: expected text', r.stdout, 'seed could not have matched it later')
        self.audit('prompt', str(run), 'hunter', '--units', self.coverage_id(run), '--wave', '1', '--agent',
                   'hunt-w1-02')
        self.result(run, 'hunt-w1-02', self.hunter_result(run, 'hunt-w1-02') | {
            'uncovered': [proposal | {'surface': 'entry b.py#x', 'starting_paths': ['/etc/passwd']}]})
        r = self.audit('apply', str(run), 'hunter', '--agent', 'hunt-w1-02', ok=False)
        self.assertIn('uncovered[0].starting_paths: expected one or more distinct repository-relative paths', r.stdout)
        # Without a path, any unit of the same labels would seem to cover it and resolve it unseen.
        audit, labels = load_script(AUDIT), proposal | {'surface': 'entry b.py#x'}
        self.assertEqual(audit.proposal_errors([labels], 'missing_units'), [])
        for paths in ([], ['b.py', 'b.py']):
            with self.subTest(paths=paths):
                self.assertEqual(audit.proposal_errors([labels | {'starting_paths': paths}], 'missing_units'),
                                 ['missing_units[0].starting_paths: expected one or more distinct '
                                  'repository-relative paths'])

    def test_a_promotion_that_did_not_happen_is_no_empty_result(self):
        from unittest import mock
        audit = load_script(AUDIT)

        class FakeRun:
            state = {'allowlists': {'hunt-1': ['evidence/c01.out:10', 'evidence/c01.json:10']}}
            runs, dir = Path('/nonexistent/runs'), Path('/nonexistent')
        promoted = json.dumps({'path': 'evidence/c01.out', 'status': 'promoted', 'bytes': 3, 'sha256': 'ab'})
        bare = json.dumps({'path': 'evidence/c01.json', 'status': 'promoted'})
        record = json.dumps({'path': 'evidence/c01.json', 'status': 'promoted', 'bytes': 3, 'sha256': 'cd'})
        # The outcome of each allowlisted file, in its order, with the fields of its status.
        with mock.patch.object(audit.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0, f'{promoted}\n{record}\n', '')):
            self.assertEqual(len(audit.promote(FakeRun(), 'hunt-1')), 2)
        FakeRun.state['allowlists']['hunt-1'] = ['evidence/c01.out:10', 'evidence/c01.json:10']
        for code, stdout in ((1, ''), (1, f'{promoted}\n{promoted}\n'), (0, f'{promoted}\n{promoted}\n'),
                             (0, f'{record}\n{promoted}\n'), (0, f'{promoted}\n{bare}\n'), (0, 'Traceback\n'),
                             (2, '')):
            with self.subTest(code=code, stdout=stdout), self.assertRaises(audit.Refused) as caught, \
                    mock.patch.object(audit.subprocess, 'run',
                                      return_value=subprocess.CompletedProcess([], code, stdout, 'AttributeError')):
                audit.promote(FakeRun(), 'hunt-1')
            self.assertIn('promotion failed', str(caught.exception))
        self.assertIn('hunt-1', FakeRun.state['allowlists'], 'the allowlist stays for another apply')

    def test_a_change_the_index_hides_from_git_status_counts(self):
        git(self.repo, 'update-index', '--skip-worktree', 'app.py')
        (self.repo / 'app.py').write_text('print("what the agents would read")\n')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '', 'git status alone misses the change')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude', ok=False)
        self.assertIn('git status does not check 1 file(s) of the index', r.stderr)
        git(self.repo, 'update-index', '--no-skip-worktree', 'app.py')
        git(self.repo, 'checkout', '-q', '--', 'app.py')
        run = Path(self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude').stdout.strip())
        self.audit('approve', str(run), '--words', 'start')
        git(self.repo, 'update-index', '--assume-unchanged', 'app.py')
        r = self.audit('prompt', str(run), 'recon', '--recon', '1a', '--agent', 'recon-1a', ok=False)
        self.assertIn('git status does not check', r.stderr, 'nor before an agent starts')

    def test_a_name_with_a_line_separator_is_not_taken_for_a_hidden_entry(self):
        # Unquoted, as core.quotePath=false prints it, U+2028 splits a line for str.splitlines().
        (self.repo / 'a\u2028s.txt').write_text('x\n')
        self.commit('a name with a line separator')
        git(self.repo, 'config', 'core.quotePath', 'false')
        run = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude')
        self.assertTrue(Path(run.stdout.strip()).is_dir())

    def test_a_change_inside_a_submodule_counts_whatever_its_ignore_setting(self):
        sub = self.tmp / 'sub'
        sub.mkdir()
        git(sub, 'init', '-q')
        (sub / 'lib.py').write_text('x = 1\n')
        git(sub, 'add', '.')
        git(sub, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'sub')
        git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(sub), 'vendor')
        git(self.repo, 'config', '-f', '.gitmodules', 'submodule.vendor.ignore', 'all')
        self.commit('a submodule whose changes git status hides')
        (self.repo / 'vendor' / 'lib.py').write_text('x = 2\n')
        self.assertEqual(git(self.repo, 'status', '--porcelain'), '', 'git status alone hides the change')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude', ok=False)
        self.assertIn('not clean', r.stderr)
        # An index bit of the submodule hides the change even from git status --ignore-submodules=none.
        git(self.repo / 'vendor', 'checkout', '-q', '--', 'lib.py')
        git(self.repo / 'vendor', 'update-index', '--skip-worktree', 'lib.py')
        (self.repo / 'vendor' / 'lib.py').write_text('x = 3\n')
        self.assertEqual(git(self.repo, 'status', '--porcelain', '--ignore-submodules=none'), '')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude', ok=False)
        self.assertIn('git status does not check 1 file(s) of the index', r.stderr)
        self.assertIn('vendor/lib.py', r.stderr)
        # Inactive in the local configuration, it is still checked out for the agents to read.
        git(self.repo, 'config', 'submodule.vendor.active', 'false')
        self.assertNotIn('vendor/lib.py', git(self.repo, 'ls-files', '--recurse-submodules'),
                         'git skips an inactive submodule')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude', ok=False)
        self.assertIn('git status does not check 1 file(s) of the index', r.stderr)
        self.assertIn('vendor/lib.py', r.stderr)
        # Files at a submodule path without a repository: git status does not look there at all.
        git(self.repo / 'vendor', 'update-index', '--no-skip-worktree', 'lib.py')
        git(self.repo / 'vendor', 'checkout', '-q', '--', 'lib.py')
        (self.repo / 'vendor' / '.git').rename(self.tmp / 'vendor.git')
        (self.repo / 'vendor' / 'lib.py').write_text('x = 4\n')
        self.assertEqual(git(self.repo, 'status', '--porcelain', '--ignore-submodules=none', '--untracked-files=all'),
                         '')
        r = self.audit('init', '--host', 'claude', '--hunt', 'opencode', '--verify', 'claude', ok=False)
        self.assertIn('the submodule path vendor holds files but no repository', r.stderr)

    def test_printed_commands_quote_their_paths(self):
        # A linked worktree keeps its records in the main worktree, whose path here has a space.
        main = self.tmp / 'main dir'
        main.mkdir()
        git(main, 'init', '-q')
        (main / 'app.py').write_text('print(1)\n')
        (main / '.gitignore').write_text('/development\n')
        git(main, 'add', '.')
        git(main, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init')
        wt = self.tmp / 'wt'
        git(main, 'worktree', 'add', '-q', str(wt))
        (main / 'development').mkdir()
        (wt / 'development').symlink_to(main / 'development', target_is_directory=True)

        def audit(*args: str) -> str:
            r = subprocess.run([sys.executable, str(AUDIT), *args], cwd=wt, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return r.stdout
        run = Path(audit('init', '--host', 'claude', '--hunt', 'opencode,provider/open-model', '--verify',
                         'claude').strip()).resolve()
        audit('approve', str(run), '--words', 'yes')
        line = next(line for line in audit('prompt', str(run), 'recon', '--recon', '1a', '--agent',
                                           'recon-1a').splitlines() if line.startswith('run: '))
        words = shlex.split(line[len('run: '):])
        self.assertEqual(words[-2:], [os.path.relpath(run / 'runs' / f'recon-1a.{ext}.md', wt)
                                      for ext in ('prompt', 'out')])
        self.assertIn(' ', words[-2])
        self.assertTrue((wt / words[-2]).is_file(), 'the command names the prompt as it is')
        section = load_script(AUDIT).sandbox_section(load_script(AUDIT).Run(run), 'hunt-w1-01')
        check = shlex.split(section.split('Run each check as `', 1)[1].split(' [limits]', 1)[0])
        self.assertEqual(check[3:5], ['--scratch', os.path.relpath(run / 'runs' / 'scratch' / 'hunt-w1-01', wt)])

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
