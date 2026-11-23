#!/usr/bin/env python3
"""Run one local check of wf sec audit in an OS-enforced sandbox, and promote its evidence afterwards.

  sandbox-run.py probe [--json]
  sandbox-run.py run --scratch <dir> --ref <commit> --check <name> [options] -- <command> [<arg>…]
  sandbox-run.py promote --scratch <dir> --artifacts <dir> --allow <path>:<bytes> [--allow …] --total <bytes>

probe   reports each control the audit requires as `enforced` or `missing` and exits 0 only when every one
        is enforced, else 125. It runs a small sandbox to see the controls work, not only that the tools
        exist. Run it where the checks will run: inside a delegated agent run, not only on the host.

run     (hunters and candidate verifiers) runs <command> with no external network (only loopback), an empty
        environment plus --env NAME=VALUE, read-only /usr, toolchains (--tool) and target source, an /etc of
        the entries toolchains read (ETC_ENTRIES; passwd, group and hosts name only the sandbox, as its host
        name does), and writes only to the size-limited tmpfs /work (HOME and TMPDIR live there; copy /src to
        /work to build) and /dev/shm (64 MiB).
        The source is a fresh export of the committed bytes of <commit> for this run (no .gitattributes of
        the target applies), written by this script into a new <scratch>/src/<random>/, mounted read-only
        at /src and removed afterwards; submodules are not exported. --inputs <dir under scratch> is mounted
        read-only at /input. A --tool is a directory that must not hold a socket or FIFO, nor lie in the
        home directory's credential stores, the repository, a runtime directory such as /run or /tmp, or
        /src, /work or /input; an entry of /etc, /etc/<name>, may be a file too and must be readable by
        every user throughout; --inputs holds no socket or FIFO either. Tools, inputs and the source are
        mounted by descriptor: what was checked is what the command sees. Limits: a cgroup v2
        scope (systemd-run --user, which must not expand ${NAME} in the command) for tasks, memory and CPU
        rate, read back inside the scope before the command starts, rlimits for CPU time, file size, open
        files and core files (address space only with --as-mb), and a wall-clock timeout that kills the scope.
        The only evidence is the command's stdout and stderr, read by this trusted process with a byte cap
        and written to <scratch>/evidence/<name>.out, .err and .json (command, limits, result, sha256).
        Target code never writes to disk outside its tmpfs. Exit: the command's status, 128+N for signal N,
        124 on timeout, 125 when a control cannot be enforced or the call is invalid (nothing ran then).

promote (the parent only, after the agent run has ended) copies each allowlisted scratch-relative file
        into <artifacts> with the race-safe procedure of references/HUNTING.md: directory descriptors held
        from the start, no-follow traversal of every component, nonblocking open, a regular file with one
        link within its byte limit and the cumulative limit, an unchanged identity and size after the copy,
        exclusive creation of the destination (one that holds the same bytes already, from a promotion an
        interrupted apply made, counts as promoted). A file that fails any step is not promoted. It prints one
        JSON line per allowed path (promoted, missing or rejected with the reason). Exit 0 when nothing was
        rejected, 1 when something was, 3 when a run still holds the scratch directory, 125 on invalid use.

The tools are taken from fixed system paths (/usr/bin/bwrap, /usr/bin/systemd-run), never from PATH.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import resource
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BWRAP = '/usr/bin/bwrap'
SYSTEMD_RUN = '/usr/bin/systemd-run'
SYSTEMCTL = '/usr/bin/systemctl'
GIT = shutil.which('git', path='/usr/bin:/bin:/usr/local/bin') or '/usr/bin/git'
CONTROLS = ('no-network', 'empty-environment', 'read-only-target-and-toolchain', 'scratch-only-writes',
            'process-limit', 'memory-limit', 'cpu-limit', 'file-size-limit', 'disk-limit', 'wall-clock-limit',
            'source-copy', 'artifact-promotion')
# name: (default, maximum). Low defaults; a check that needs more says so in its command.
LIMITS = {
    'cpu_seconds': (60, 600),
    'memory_mb': (1024, 4096),
    'tasks': (64, 512),
    'file_mb': (64, 1024),
    'work_mb': (256, 2048),
    'nofile': (256, 4096),
    'timeout': (120, 1800),
    'output_bytes': (65536, 1048576),
    'cpu_percent': (100, 400),
}
SOURCE_MAX_BYTES = 1024 * 1024 * 1024
CHECK_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,63}')
SECRET_NAME = re.compile(r'TOKEN|SECRET|PASSW|CREDENTIAL|KEY|AUTH|COOKIE|SESSION|PRIVATE|CERT', re.I)
FIXED_ENV = {'HOME': '/work/home', 'TMPDIR': '/work/tmp', 'LANG': 'C.UTF-8'}
DEFAULT_PATH = '/usr/local/bin:/usr/bin:/bin'
COMMIT = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
# Credential stores under the home directory that no --tool may expose.
HOME_SECRETS = ('.ssh', '.gnupg', '.aws', '.azure', '.config', '.docker', '.kube', '.netrc', '.git-credentials',
                '.local/share', '.claude', '.codex', '.gemini', '.password-store', '.pki', '.npmrc', '.pypirc',
                '.cargo/credentials', '.cargo/credentials.toml')
# Where host services keep sockets and runtime state. A socket reached through a read-only bind still
# connects (the user bus could start a process outside the sandbox), so no --tool may lie below these.
RUNTIME_ROOTS = ('/run', '/var/run', '/tmp', '/var/tmp', '/dev', '/proc', '/sys')
# The sandbox's own mount points, which a --tool (mounted at its host path) must not cover.
RESERVED_MOUNTS = ('/src', '/work', '/input')
TOOL_SCAN_LIMIT = 500000
# The entries of the host /etc that toolchains read: the loader, alternatives, time and name service files,
# the public certificates and the configuration of common runtimes. The rest of /etc stays out: a host keeps
# user-readable credentials there too (a proxy password, a package index token, a TLS key readable by the
# group ssl-cert), so of ssl and pki only the certificates and the OpenSSL configuration go in. An entry
# whose host path leads out of /etc and /usr stays out as well. passwd, group and hosts are the sandbox's
# own; a toolchain that needs another entry gets it with --tool /etc/<name>.
ETC_ENTRIES = ('alternatives', 'ca-certificates', 'crypto-policies', 'debian_version', 'fonts', 'gai.conf',
               'host.conf', 'ld.so.cache', 'ld.so.conf', 'ld.so.conf.d', 'locale.alias', 'locale.conf',
               'localtime', 'lsb-release', 'magic', 'mime.types', 'nsswitch.conf', 'os-release', 'perl',
               'pki/ca-trust', 'pki/java', 'pki/tls/cert.pem', 'pki/tls/certs', 'pki/tls/openssl.cnf', 'protocols',
               'services', 'ssl/cert.pem', 'ssl/certs', 'ssl/openssl.cnf', 'timezone')
ETC_PATTERN = re.compile(r'java-\d+-openjdk|python3(\.\d+)?')
# The directories bwrap makes for the nested entries above.
ETC_PARENTS = tuple(sorted({entry.rsplit('/', 1)[0] for entry in ETC_ENTRIES if '/' in entry} |
                           {entry.split('/', 1)[0] for entry in ETC_ENTRIES if '/' in entry}))
ETC_GENERATED = ('group', 'hosts', 'passwd')
HOSTNAME = 'sandbox'
# /dev is read-only but for shared memory, a tmpfs of its own with this size.
SHM_BYTES = 64 * 1024 * 1024
# The mounts that stay writable inside: /work and /dev/shm take files; the others are the kernel's.
WRITABLE_MOUNTS = ('/work', '/dev/shm', '/proc', '/dev/pts', '/dev/null', '/dev/zero', '/dev/full', '/dev/random',
                   '/dev/urandom', '/dev/tty')


class Refused(Exception):
    """The sandbox cannot be set up as required; nothing ran."""


def fail(message: str, code: int = 125) -> int:
    print(f'sandbox-run: {message}', file=sys.stderr)
    return code


# --------------------------------------------------------------------------- the sandbox


def host_root_links() -> list[str]:
    """bwrap arguments that recreate /bin, /sbin, /lib… as on the host: a link, or a read-only directory."""
    args: list[str] = []
    for name in ('bin', 'sbin', 'lib', 'lib32', 'lib64', 'libx32'):
        path = Path('/') / name
        if path.is_symlink():
            args += ['--symlink', os.readlink(path), str(path)]
        elif path.is_dir():
            args += ['--ro-bind', str(path), str(path)]
    return args


def etc_sources() -> list[str]:
    """The entries of the host /etc that the sandbox mounts: those of ETC_ENTRIES and ETC_PATTERN that exist
    and do not lead out of /etc and /usr (bwrap would mount where a link of the host leads)."""
    names = list(ETC_ENTRIES) + sorted(name for name in os.listdir('/etc') if ETC_PATTERN.fullmatch(name))
    kept = []
    for name in names:
        path = Path('/etc') / name
        if not os.path.lexists(path):
            continue
        real = path.resolve()
        if real.exists() and any(real == root or root in real.parents for root in (Path('/etc'), Path('/usr'))):
            kept.append(str(path))
    return kept


def etc_args(generated: list[tuple[int, str]]) -> list[str]:
    """The sandbox's /etc: the host entries of etc_sources(), and passwd, group and hosts from the
    descriptors in generated."""
    args = ['--dir', '/etc']
    for path in etc_sources():
        args += ['--ro-bind', path, path]
    for fd, target in generated:
        args += ['--ro-bind-data', str(fd), target]
    return args


def identity_fds() -> list[tuple[int, str]]:
    """Descriptors that hold the sandbox's passwd, group and hosts: one user, its group, home /work/home, and
    only loopback names, so that nothing of the host's name or network shows."""
    uid, gid = os.getuid(), os.getgid()
    fds = []
    for target, text in (('/etc/passwd', f'sandbox:x:{uid}:{gid}::/work/home:/bin/sh\n'),
                         ('/etc/group', f'sandbox:x:{gid}:\n'),
                         ('/etc/hosts', f'127.0.0.1 localhost {HOSTNAME}\n::1 localhost {HOSTNAME}\n')):
        read, write = os.pipe()
        os.write(write, text.encode())
        os.close(write)
        fds.append((read, target))
    return fds


# Runs inside the finished sandbox, as the last step before the command: its standard input is the launch
# pipe, and one byte there says that the command starts, which then reads /dev/null instead, as before. A
# command that cannot start sends no byte, so it is no result. The shell adds no variable of its own.
LAUNCH_SCRIPT = r'''
case $1 in
  */*) [ -f "$1" ] && [ -x "$1" ] ;;
  *) command -v "$1" >/dev/null 2>&1 ;;
esac || { printf 'wf-sandbox-launch: %s: no such executable\n' "$1" >&2; exit 125; }
printf . >&0 || exit 125
exec 0</dev/null
unset PWD OLDPWD SHLVL
exec "$@"
'''


def bwrap_args(*, work_bytes: int, env: dict[str, str], binds: list[tuple[str | int, str]], chdir: str,
               status_fd: int, identity: list[tuple[int, str]] = (), launch: bool = False) -> list[str]:
    """A bind's source is a path, or an open descriptor that keeps its identity until bwrap mounts it."""
    # The status descriptor gets an "exit-code" line only when the command itself ran and ended.
    args = [BWRAP, '--json-status-fd', str(status_fd), '--unshare-all', '--unshare-user', '--disable-userns',
            '--cap-drop', 'ALL', '--die-with-parent', '--new-session', '--clearenv', '--hostname', HOSTNAME]
    for name, value in env.items():
        args += ['--setenv', name, value]
    args += ['--ro-bind', '/usr', '/usr', *host_root_links(), *etc_args(list(identity)),
             '--proc', '/proc', '--dev', '/dev', '--size', str(SHM_BYTES), '--tmpfs', '/dev/shm',
             '--size', str(work_bytes), '--tmpfs', '/work', '--dir', '/work/home', '--dir', '/work/tmp',
             '--symlink', '/work/tmp', '/tmp']
    for source, target in binds:
        args += ['--ro-bind-fd', str(source), target] if isinstance(source, int) else ['--ro-bind', source, target]
    # bwrap leaves its root and its /dev writable: only /work and /dev/shm, which have a size, may take writes.
    args += ['--remount-ro', '/dev', '--remount-ro', '/', '--chdir', chdir, '--']
    if launch:
        args += ['/bin/sh', '-c', LAUNCH_SCRIPT, 'wf-sandbox-launch']
    return args


@functools.lru_cache(maxsize=1)
def no_expansion() -> tuple[str, ...]:
    """systemd-run 254+ expands ${NAME} in the command with its own environment, before the sandbox's."""
    try:
        text = subprocess.run([SYSTEMD_RUN, '--help'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    return ('--expand-environment=no',) if '--expand-environment' in text else ()


@functools.lru_cache(maxsize=1)
def oom_policy() -> tuple[str, ...]:
    """OOMPolicy=continue, which scopes take from systemd 253. A check past its memory limit loses the process
    the kernel killed either way; under the default, stop, the scope also fails with 'oom-kill', which KDE and
    GNOME report as the system running low on memory. What is left of the scope ends with its command, or the
    wall-clock limit stops it. The running user manager decides what a scope takes: after an upgrade it can be
    older than systemd-run."""
    try:
        text = subprocess.run([SYSTEMCTL, '--user', 'show', '--property=Version', '--value'], capture_output=True,
                              text=True, env=scope_env(), timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.match(r'v?(\d+)', text.strip())
    return ('-p', 'OOMPolicy=continue') if match and int(match.group(1)) >= 253 else ()


def scope_args(*, tasks: int, memory_mb: int, cpu_percent: int) -> tuple[list[str], str]:
    unit = f'wf-sandbox-{secrets.token_hex(6)}'
    return [SYSTEMD_RUN, '--user', '--scope', '--quiet', '--collect', *no_expansion(), '--unit', unit,
            '-p', f'TasksMax={tasks}', '-p', f'MemoryMax={memory_mb}M', '-p', 'MemorySwapMax=0', *oom_policy(),
            '-p', f'CPUQuota={cpu_percent}%', '--'], unit


# Runs inside the new scope before bwrap: systemd accepts a limit even where its controller is not delegated,
# so the scope's own values are read back and nothing starts unless all three hold.
GUARD_SCRIPT = r'''
d=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
[ "$(cat "$d/pids.max" 2>/dev/null)" = "$1" ] || { echo "the scope does not limit tasks" >&2; exit 125; }
[ "$(cat "$d/memory.max" 2>/dev/null)" = "$2" ] || { echo "the scope does not limit memory" >&2; exit 125; }
[ "$(cat "$d/cpu.max" 2>/dev/null)" = "$3" ] || { echo "the scope does not limit the CPU rate" >&2; exit 125; }
shift 3
exec "$@"
'''


def guard_args(*, tasks: int, memory_mb: int, cpu_percent: int) -> list[str]:
    return ['/bin/sh', '-c', GUARD_SCRIPT, 'wf-sandbox-guard', str(tasks), str(memory_mb * 1024 * 1024),
            f'{cpu_percent * 1000} 100000']


def rlimits(cpu_seconds: int, file_bytes: int, nofile: int, as_bytes: int | None):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if as_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    return apply


class Capture(threading.Thread):
    """Read a stream to its end, keeping at most `limit` bytes."""

    def __init__(self, stream, limit: int):
        super().__init__(daemon=True)
        self.stream, self.limit = stream, limit
        self.data, self.total = bytearray(), 0

    def run(self):
        while True:
            chunk = self.stream.read(65536)
            if not chunk:
                break
            self.total += len(chunk)
            room = self.limit - len(self.data)
            if room > 0:
                self.data += chunk[:room]


def sandboxed(command: list[str], *, env: dict[str, str], binds: list[tuple[str | int, str]], chdir: str,
              limits: dict[str, int], as_bytes: int | None = None, cwd: str | None = None,
              guard: bool = True) -> dict:
    """Run command in the sandbox; return its result. Raises Refused before anything runs.

    Without guard (the probe only) the command starts even when a cgroup limit does not hold, so that the
    probe can test the other controls on their own; run always keeps it."""
    for tool in (BWRAP, SYSTEMD_RUN):
        if not os.access(tool, os.X_OK):
            raise Refused(f'{tool} is not installed')
    cgroup = {'tasks': limits['tasks'], 'memory_mb': limits['memory_mb'], 'cpu_percent': limits['cpu_percent']}
    scope, unit = scope_args(**cgroup)
    status_read, status_write = os.pipe()
    launch_read, launch_write = os.pipe()
    identity = identity_fds()
    argv = scope + (guard_args(**cgroup) if guard else []) + bwrap_args(
        work_bytes=limits['work_mb'] * 1024 * 1024, env=env, binds=binds, chdir=chdir, status_fd=status_write,
        identity=identity, launch=True) + command
    bind_fds = tuple(source for source, _ in binds if isinstance(source, int)) + tuple(fd for fd, _ in identity)
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, stdin=launch_write, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=cwd, start_new_session=True, close_fds=True,
                                   pass_fds=(status_write, *bind_fds), env=scope_env(),
                                   preexec_fn=rlimits(limits['cpu_seconds'], limits['file_mb'] * 1024 * 1024,
                                                      limits['nofile'], as_bytes))
    except OSError as exc:
        os.close(status_read)
        os.close(launch_read)
        raise Refused(f'cannot start the sandbox: {exc}') from exc
    finally:
        os.close(status_write)
        os.close(launch_write)
        for fd, _ in identity:
            os.close(fd)
    out, err = Capture(process.stdout, limits['output_bytes']), Capture(process.stderr, limits['output_bytes'])
    status_pipe = Capture(os.fdopen(status_read, 'rb'), 65536)
    launch_pipe = Capture(os.fdopen(launch_read, 'rb'), 16)
    for reader in (out, err, status_pipe, launch_pipe):
        reader.start()
    killed = None
    try:
        process.wait(timeout=limits['timeout'])
    except subprocess.TimeoutExpired:
        killed = 'timeout'
        stop(process, unit)
        process.wait()
    for reader in (out, err, status_pipe, launch_pipe):
        reader.join()
        reader.stream.close()
    duration = round(time.monotonic() - started, 3)
    code = process.returncode
    status = 124 if killed else (128 - code if code < 0 else code)
    stderr = bytes(err.data)
    # Only the launch byte, written inside the finished sandbox right before the command, says that it ran:
    # without it systemd, the guard or bwrap stopped first, or the time ran out while they set up.
    launched = bytes(launch_pipe.data) == b'.'
    if killed and not launched:
        raise Refused(f'the wall-clock limit of {limits["timeout"]} s ran out before the command started')
    if not killed and (not launched or b'"exit-code"' not in status_pipe.data):
        raise Refused('the sandbox did not start: ' + stderr.decode('utf-8', 'replace').strip()[:300])
    return {'status': status, 'killed': killed, 'duration_s': duration, 'stdout': bytes(out.data),
            'stdout_bytes': out.total, 'stderr': stderr, 'stderr_bytes': err.total, 'unit': unit}


def scope_env() -> dict[str, str]:
    """systemd-run needs the user bus; nothing else of the caller's environment is passed on."""
    keep = ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')
    return {name: os.environ[name] for name in keep if name in os.environ}


def stop(process: subprocess.Popen, unit: str) -> None:
    """Kill the sandbox: its outer bwrap, whose death kills the PID namespace, and the whole scope."""
    with contextlib.suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([SYSTEMCTL, '--user', 'kill', '--signal=SIGKILL', f'{unit}.scope'], env=scope_env(),
                       capture_output=True, timeout=10)


# --------------------------------------------------------------------------- probe


PROBE_SCRIPT = r'''
home=$1 tmp=$2
shift 2
nets=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | tr '\n' ' ')
echo "nets=$nets"
echo "env=$(env | cut -d= -f1 | sort | tr '\n' ' ')"
for p in /x /src/x /usr/x /etc/x /dev/x "$home/x" "$tmp/x"; do
  if (: > "$p") 2>/dev/null; then echo "writable=$p"; fi
done
for p in "$home" "$tmp"; do [ -e "$p" ] && echo "visible=$p"; done
echo "etc=$(ls -A /etc | tr '\n' ' ')"
for d in "$@"; do echo "etc/$d=$(ls -A "/etc/$d" 2>/dev/null | tr '\n' ' ')"; done
rw= shm=
while read -r _ _ _ _ mnt opts rest; do
  case $opts in rw|rw,*) rw="$rw $mnt";; esac
  [ "$mnt" = /dev/shm ] && shm=${rest##* }
done < /proc/self/mountinfo
echo "rw=$rw"; echo "shm=$shm"
: > /work/ok && echo work=ok
head -c 2097152 /dev/zero > /work/big 2>/dev/null; echo "work_bytes=$(wc -c < /work/big)"
echo "cpu=$(ulimit -t)"; echo "nofile=$(ulimit -n)"; echo "core=$(ulimit -c)"; echo "fsize=$(ulimit -f)"
'''
# Read back the limits the scope applied: systemd accepts a property even where the controller is not
# delegated, and then nothing enforces it.
# One labeled line per controller, so that a missing one does not shift the others.
SCOPE_SCRIPT = 'cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); for f in pids.max memory.max cpu.max; do ' \
               'printf "%s=%s\\n" "$f" "$(cat "$cg/$f" 2>/dev/null || echo missing)"; done'


def probe() -> dict[str, str]:
    """Each control and why it is missing, or 'enforced'."""
    result = dict.fromkeys(CONTROLS, 'enforced')

    def missing(reason: str, *names: str):
        for name in names or CONTROLS:
            if result[name] == 'enforced':
                result[name] = reason

    sandbox_controls = CONTROLS[:10]
    for tool in (BWRAP, SYSTEMD_RUN):
        if not os.access(tool, os.X_OK):
            missing(f'{tool} is not installed', *sandbox_controls)
    if result['no-network'] == 'enforced':
        home = str(Path.home())
        with tempfile.TemporaryDirectory(prefix='wf-probe-') as tmp:
            src = Path(tmp) / 'src'
            src.mkdir()
            limits = {name: default for name, (default, _) in LIMITS.items()}
            limits.update(work_mb=1, timeout=30)
            src_fd = open_dir(src)  # mounted by descriptor, as run mounts the source export
            try:
                run = sandboxed(['/bin/sh', '-c', PROBE_SCRIPT, 'probe', home, tmp, *ETC_PARENTS],
                                env={'PATH': DEFAULT_PATH, **FIXED_ENV, 'WF_PROBE': '1'},
                                binds=[(src_fd, '/src')], chdir='/', limits=limits, guard=False)
            except Refused as exc:
                os.close(src_fd)
                missing(str(exc), *sandbox_controls)
            else:
                os.close(src_fd)
                text = run['stdout'].decode('utf-8', 'replace')
                facts = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
                if facts.get('nets', '').split() != ['lo']:
                    missing(f'network interfaces inside: {facts.get("nets", "?")!r}', 'no-network')
                expected = sorted(['HOME', 'LANG', 'PATH', 'PWD', 'TMPDIR', 'WF_PROBE', 'SHLVL', '_'])
                extra = sorted(set(facts.get('env', '').split()) - set(expected))
                if extra:
                    missing(f'unexpected variables inside: {", ".join(extra)}', 'empty-environment')
                writable = [line.split('=', 1)[1] for line in text.splitlines() if line.startswith('writable=')]
                visible = [line.split('=', 1)[1] for line in text.splitlines() if line.startswith('visible=')]
                if writable:
                    missing(f'writable inside: {", ".join(writable)}', 'read-only-target-and-toolchain',
                            'scratch-only-writes')
                if visible:
                    missing(f'host paths visible inside: {", ".join(visible)}', 'scratch-only-writes')
                etc = [name for name in facts.get('etc', '').split() if name not in ETC_GENERATED and name not in
                       ETC_ENTRIES and name not in ETC_PARENTS and not ETC_PATTERN.fullmatch(name)]
                etc += [f'{parent}/{name}' for parent in ETC_PARENTS for name in facts.get(f'etc/{parent}', '').split()
                        if f'{parent}/{name}' not in ETC_ENTRIES + ETC_PARENTS]
                if etc or 'etc' not in facts:
                    missing(f'host /etc entries visible inside: {", ".join(sorted(etc)) or "?"}', 'scratch-only-writes')
                mounts = facts.get('rw', '').split()
                extra = sorted(set(mounts) - set(WRITABLE_MOUNTS))
                if extra or '/work' not in mounts:
                    missing(f'writable mounts inside: {", ".join(extra) or "?"}', 'scratch-only-writes')
                if f'size={SHM_BYTES // 1024}k' not in facts.get('shm', '').split(','):
                    missing(f'the shared-memory tmpfs has no size of {SHM_BYTES} bytes', 'disk-limit')
                if facts.get('work') != 'ok':
                    missing('the work tmpfs is not writable', 'scratch-only-writes')
                if facts.get('work_bytes', '').strip() != str(1024 * 1024):
                    missing(f'the 1 MiB work tmpfs took {facts.get("work_bytes", "?")} bytes', 'disk-limit')
                if facts.get('cpu') != str(limits['cpu_seconds']):
                    missing(f'CPU time limit inside: {facts.get("cpu", "?")}', 'cpu-limit')
                if facts.get('nofile') != str(limits['nofile']) or facts.get('core') != '0':
                    missing('open-file or core limit not applied inside', 'process-limit')
                if facts.get('fsize') != str(limits['file_mb'] * 1024 * 1024 // 512):
                    missing(f'file-size limit inside: {facts.get("fsize", "?")} blocks', 'file-size-limit')
                if run['status'] != 0 or run['killed']:
                    missing(f'the probe command ended with {run["status"]}', *sandbox_controls)
        scope, _ = scope_args(tasks=17, memory_mb=67, cpu_percent=50)
        try:
            applied = subprocess.run(scope + ['/bin/sh', '-c', SCOPE_SCRIPT], capture_output=True, text=True,
                                     env=scope_env(), timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            missing(f'no cgroup scope could start: {exc}', 'process-limit', 'memory-limit', 'cpu-limit')
        else:
            if applied.returncode != 0:
                missing(f'no cgroup scope could start: {applied.stderr.strip()[:200]}', 'process-limit',
                        'memory-limit', 'cpu-limit')
            values = dict(line.split('=', 1) for line in applied.stdout.splitlines() if '=' in line)
            if values.get('pids.max') != '17':
                missing('the cgroup scope does not limit tasks (is the pids controller delegated?)', 'process-limit')
            if values.get('memory.max') != str(67 * 1024 * 1024):
                missing('the cgroup scope does not limit memory (is the memory controller delegated?)',
                        'memory-limit')
            if values.get('cpu.max', '').split() != ['50000', '100000']:
                missing('the cgroup scope does not limit the CPU rate (is the cpu controller delegated?)',
                        'cpu-limit')
    if not os.access(GIT, os.X_OK):
        missing(f'{GIT} is not installed', 'source-copy')
    if not (getattr(os, 'O_NOFOLLOW', 0) and getattr(os, 'O_NONBLOCK', 0) and getattr(os, 'O_DIRECTORY', 0)
            and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd):
        missing('no-follow, nonblocking and directory-relative opens are unavailable', 'artifact-promotion')
    return result


def cmd_probe(args) -> int:
    result = probe()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for name in CONTROLS:
            state = result[name]
            print(f'enforced: {name}' if state == 'enforced' else f'missing: {name} — {state}')
    return 0 if all(state == 'enforced' for state in result.values()) else 125


# --------------------------------------------------------------------------- run


def open_dir(path: str | Path, dir_fd: int | None = None) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


# Linux opens the directories above a path for search only; elsewhere (macOS) they are opened for reading.
SEARCH_ONLY = getattr(os, 'O_PATH', os.O_RDONLY)


def open_real(path: Path, what: str) -> int:
    """A descriptor of the directory at an absolute path, opened from / one component at a time without
    following a link: a link anywhere on the path refuses instead of leading somewhere else. The audit passes
    resolved paths, so only a link planted on the way refuses."""
    parts = list(path.parts[1:])
    if not path.is_absolute() or not parts:
        raise Refused(f'{what} must be an absolute directory path: {path}')
    fd = os.open('/', SEARCH_ONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for n, part in enumerate(parts):
            mode = os.O_RDONLY if n == len(parts) - 1 else SEARCH_ONLY
            try:
                child = os.open(part, mode | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as exc:
                raise Refused(f'{what} must be an existing directory reached without a link: {path} ({part}: '
                              f'{errno.errorcode.get(exc.errno, exc.errno)})') from None
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_tool(path: str, repo: Path) -> tuple[str, int]:
    """The mount point of a --tool and a descriptor of what was checked. A toolchain is a directory, mounted
    at its real path; an entry of /etc, /etc/<name>, is a directory or a file, mounted at /etc/<name> wherever
    the host's link leads, and what this user may read in it every user must be able to read: what only its
    owner or group may read there (/etc/ssl/private, for example) is a credential."""
    p = Path(path)
    if not p.is_absolute() or not p.exists():
        raise Refused(f'--tool needs an existing absolute path: {path}')
    given = Path(os.path.normpath(p))
    etc_entry = given.parent == Path('/etc')
    real = p.resolve()
    home = Path.home().resolve()

    def within(child: Path, parent: Path) -> bool:
        return child == parent or parent in child.parents

    if real == Path('/') or within(home, real) or within(repo, real) or within(real, repo):
        raise Refused(f'--tool must not expose the home directory or the repository: {path}')
    if Path('/etc') in (real, given):
        raise Refused('--tool must not mount all of /etc, which holds host credentials too: mount the entry a '
                      f'toolchain needs, --tool /etc/<name>: {path}')
    for reserved in RESERVED_MOUNTS:
        if within(real, Path(reserved)) or within(Path(reserved), real):
            raise Refused(f'--tool is mounted at its own path and must not cover {reserved} of the sandbox: {path}')
    for name in HOME_SECRETS:
        # A credential store that is itself a link exposes its destination: check both.
        for secret in dict.fromkeys((home / name, (home / name).resolve())):
            if within(real, secret) or within(secret, real):
                raise Refused(f'--tool must not expose {home / name}: {path}')
    runtime = [Path(p).resolve() for p in RUNTIME_ROOTS + ((os.environ['XDG_RUNTIME_DIR'],)
                                                          if os.environ.get('XDG_RUNTIME_DIR') else ())]
    for root in runtime:
        if within(real, root) or within(root, real):
            raise Refused(f'--tool must not expose {root}, where host services keep their sockets: {path}')
    if etc_entry and real.is_file():
        # bwrap mounts this descriptor, the file that was checked.
        parent = open_real(real.parent, '--tool')
        try:
            fd = os.open(real.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        except OSError as exc:
            raise Refused(f'--tool cannot open {real}: {exc.strerror}') from None
        finally:
            os.close(parent)
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or not mode & stat.S_IROTH:  # opened, so this user may read it
            os.close(fd)
            raise Refused(f'--tool of /etc must be a regular file that every user may read: {path}')
        return str(given), fd
    if not real.is_dir():
        raise Refused(f'--tool must be a directory, or a file of /etc: {path}')
    # bwrap mounts this descriptor, the directory that was checked, whatever happens to the path meanwhile.
    fd = open_real(real, '--tool')
    found = first_channel(fd, str(real), '--tool', public=etc_entry)
    if found:
        os.close(fd)
        raise Refused(f'--tool must not expose {found}')
    return (str(given) if etc_entry else str(real)), fd


def first_channel(root_fd: int, label: str, what: str, public: bool = False) -> str | None:
    """The first socket or FIFO in the directory root_fd or below, without following links, and with public
    the first entry that this user may read but not every user (one that only root may read shows nothing);
    refuses a tree too large to check."""
    def unreadable(exc: OSError):
        raise Refused(f'{what} cannot be checked for sockets: {exc}')

    if public and (os.fstat(root_fd).st_mode & 0o005) != 0o005:  # opened, so this user may read it
        return f'{label}, which not every user may read'
    seen = 0
    for top, dirs, files, top_fd in os.fwalk('.', dir_fd=root_fd, follow_symlinks=False, onerror=unreadable):
        for name in dirs + files:
            seen += 1
            if seen > TOOL_SCAN_LIMIT:
                raise Refused(f'{what} has more than {TOOL_SCAN_LIMIT} entries to check; mount a narrower '
                              f'directory: {label}')
            mode = os.stat(name, dir_fd=top_fd, follow_symlinks=False).st_mode
            where = os.path.normpath(os.path.join(label, top, name))
            if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode):
                return f'a socket or FIFO, which would reach a host process: {where}'
            needed = 0o005 if stat.S_ISDIR(mode) else 0o004
            if public and not stat.S_ISLNK(mode) and (mode & needed) != needed and \
                    os.access(name, os.R_OK, dir_fd=top_fd, follow_symlinks=False):
                return f'{where}, which not every user may read'
    return None


def parse_env(values: list[str]) -> dict[str, str]:
    env = {'PATH': DEFAULT_PATH, **FIXED_ENV}
    for item in values:
        name, sep, value = item.partition('=')
        if not sep or not ENV_NAME.fullmatch(name):
            raise Refused(f'--env takes NAME=VALUE with an upper-case name: {item!r}')
        if name in FIXED_ENV:
            raise Refused(f'{name} is fixed inside the sandbox ({FIXED_ENV[name]})')
        if SECRET_NAME.search(name):
            raise Refused(f'{name} looks like a credential; use dummy values under another name')
        if '\n' in value or '\0' in value:
            raise Refused(f'--env value of {name} must be one line')
        env[name] = value
    return env


def limit_values(args) -> dict[str, int]:
    values = {}
    for name, (default, maximum) in LIMITS.items():
        value = getattr(args, name)
        value = default if value is None else value
        if not 1 <= value <= maximum:
            raise Refused(f'--{name.replace("_", "-")} must be 1-{maximum}, got {value}')
        values[name] = value
    return values


def git(repo: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    env = {'PATH': '/usr/bin:/bin', 'GIT_ATTR_NOSYSTEM': '1', 'GIT_CONFIG_NOSYSTEM': '1', 'LC_ALL': 'C',
           'HOME': os.environ.get('HOME', '/')}
    return subprocess.run([GIT, '-C', str(repo), *args], env=env, **kwargs)


def export_source(repo: Path, ref: str, scratch_fd: int) -> tuple[int, list[str], tuple[int, str, int]]:
    """A fresh copy of the commit for one run, made by trusted code: returns a descriptor of its directory
    (bwrap mounts that, so a later rename cannot swap it), the omitted submodules and what remove_export needs.

    The files are the committed bytes, read with `git cat-file`: no attribute of the target applies (git
    archive would honour its export-ignore, export-subst and filters), no line endings change, and nothing
    is written through a link. Every run gets a new directory with a random name below <scratch>/src/,
    reached from the scratch descriptor without following links and removed after the run, so nothing
    left in the scratch directory can stand in for the commit."""
    resolved = git(repo, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}', capture_output=True, text=True)
    if resolved.returncode != 0 or resolved.stdout.strip() != ref:
        raise Refused(f'--ref must be the full id of a commit of this repository: {ref}')
    listing = git(repo, 'ls-tree', '-r', '-z', '--full-tree', ref, capture_output=True)
    if listing.returncode != 0:
        raise Refused('git ls-tree failed for the source export')
    entries, submodules = [], []
    for raw in listing.stdout.split(b'\0'):
        if not raw:
            continue
        meta, _, path = raw.partition(b'\t')
        mode, kind, oid = meta.decode().split()
        parts = path.split(b'/')
        if any(part in {b'', b'.', b'..'} or part.lower() == b'.git' for part in parts):
            raise Refused(f'the commit has an unsafe path: {path.decode("utf-8", "replace")!r}')
        if mode == '160000':
            submodules.append(path.decode('utf-8', 'replace'))
        elif kind == 'blob':
            entries.append((mode, oid, parts))
    try:
        src_fd = walk(scratch_fd, ['src'], create=True)
    except Rejected as exc:
        raise Refused(f'the source directory of the scratch directory: {exc}') from None
    name = f'{ref[:12]}-{secrets.token_hex(8)}'
    os.mkdir(name, 0o700, dir_fd=src_fd)
    root = open_dir(name, dir_fd=src_fd)
    # Every directory of the export, as it was made: the cleanup descends into these only.
    made = {identity(os.fstat(root))}
    batch = subprocess.Popen([GIT, '-C', str(repo), 'cat-file', '--batch'], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                                                          'LC_ALL': 'C'})
    total = 0
    done = False
    try:
        for mode, oid, parts in entries:
            batch.stdin.write(oid.encode() + b'\n')
            batch.stdin.flush()
            header = batch.stdout.readline().split()
            if len(header) != 3 or header[1] != b'blob':
                raise Refused(f'git cat-file did not return blob {oid}')
            size = int(header[2])
            total += size
            if total > SOURCE_MAX_BYTES:
                raise Refused(f'the source export exceeds {SOURCE_MAX_BYTES} bytes')
            data = batch.stdout.read(size)
            batch.stdout.read(1)
            names = [os.fsdecode(part) for part in parts]
            parent = walk(root, names[:-1], create=True, made=made)
            try:
                if mode == '120000':
                    os.symlink(os.fsdecode(data), names[-1], dir_fd=parent)
                    continue
                fd = os.open(names[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o755 if mode == '100755' else 0o644, dir_fd=parent)
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data)
            except Rejected as exc:
                raise Refused(f'cannot export {"/".join(names)}: {exc}') from None
            finally:
                os.close(parent)
        done = True
    finally:
        if not done:
            batch.kill()  # it may be blocked writing a blob nobody reads
        with contextlib.suppress(OSError):
            batch.stdin.close()
        batch.stdout.close()
        batch.wait()
        if not done:
            remove_export(src_fd, name, root, made)
    return root, submodules, (src_fd, name, os.dup(root), made)


def identity(st: os.stat_result) -> tuple[int, int]:
    return st.st_dev, st.st_ino


def clear_dir(top_fd: int, made: set[tuple[int, int]]) -> None:
    """Delete everything below the directory top_fd through descriptors alone: never by a path from above it,
    never through a link, wherever the directory has been moved meanwhile, and only in the directories of
    made, those the export created: a directory moved in from elsewhere stays, whenever it came."""
    stack: list[list] = [[os.dup(top_fd), None, None]]  # descriptor, name in its parent, names left
    try:
        while stack:
            frame = stack[-1]
            fd = frame[0]
            if frame[2] is None:
                frame[2] = os.listdir(fd)
            if frame[2]:
                name = frame[2].pop()
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    child = open_dir(name, dir_fd=fd)
                    if identity(os.fstat(child)) not in made:
                        os.close(child)
                        raise OSError(errno.EBUSY, f'{name} is not a directory of the export, so it stays')
                    stack.append([child, name, None])
                else:
                    os.unlink(name, dir_fd=fd)
                continue
            stack.pop()
            os.close(fd)
            if frame[1] is not None:
                os.rmdir(frame[1], dir_fd=stack[-1][0])
    finally:
        for frame in stack:
            os.close(frame[0])


def remove_export(src_fd: int, name: str, root_fd: int, made: set[tuple[int, int]]) -> None:
    """Delete one run's source copy: its content through its own descriptor root_fd, then its name below the
    descriptor of <scratch>/src, which removes only an empty directory: a directory moved into its place stays."""
    try:
        clear_dir(root_fd, made)
        st, own = os.stat(name, dir_fd=src_fd, follow_symlinks=False), os.fstat(root_fd)
        if (st.st_dev, st.st_ino) != (own.st_dev, own.st_ino):
            print(f'sandbox-run: the source copy {name} was emptied, but its name holds another directory now, '
                  'which stays', file=sys.stderr)
            return
        os.rmdir(name, dir_fd=src_fd)
    except OSError as exc:
        print(f'sandbox-run: the source copy {name} was not removed: {exc}', file=sys.stderr)
    finally:
        os.close(root_fd)
        os.close(src_fd)


def cmd_run(args) -> int:
    if not args.command:
        return fail('give the command after --')
    if not CHECK_NAME.fullmatch(args.check):
        return fail(f'--check must match {CHECK_NAME.pattern}: {args.check}')
    if not COMMIT.fullmatch(args.ref):
        return fail('--ref must be a full commit id')
    try:
        # The path as given, not resolved: a link anywhere on it (a planted runs/scratch) refuses below.
        scratch = Path(os.path.abspath(args.scratch))
        repo_out = git(Path.cwd(), 'rev-parse', '--show-toplevel', capture_output=True, text=True)
        if repo_out.returncode != 0:
            raise Refused('run from inside the audited repository')
        repo = Path(repo_out.stdout.strip()).resolve()
        limits = limit_values(args)
        env = parse_env(args.env)
        checked = [check_tool(tool, repo) for tool in args.tool]
        tools = [path for path, _ in checked]
        binds: list[tuple[str | int, str]] = [(fd, path) for path, fd in checked]
        scratch_fd = open_real(scratch, '--scratch')
        try:
            fcntl.flock(scratch_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            raise Refused('the scratch directory is being promoted; nothing ran') from None
        evidence = scratch / 'evidence'
        try:
            evidence_fd = walk(scratch_fd, ['evidence'], create=True)
        except Rejected as exc:
            raise Refused(f'the evidence directory: {exc}') from None
        names = [f'{args.check}.{ext}' for ext in ('out', 'err', 'json')]
        for name in names:
            with contextlib.suppress(FileNotFoundError):
                os.stat(name, dir_fd=evidence_fd, follow_symlinks=False)
                raise Refused(f'check {args.check} already has evidence; choose another --check name')
        if args.inputs:
            # Opened from the locked scratch descriptor, without following a link, and checked as held.
            try:
                inputs_fd = walk(scratch_fd, split_relative(args.inputs), create=False)
            except (Rejected, FileNotFoundError) as exc:
                raise Refused(f'--inputs must be a plain directory inside the scratch directory: {exc}') from None
            binds.append((inputs_fd, '/input'))
            found = first_channel(inputs_fd, str(scratch / args.inputs), '--inputs')
            if found:
                raise Refused(f'--inputs must not hold {found}')
        source_fd, submodules, export = export_source(repo, args.ref, scratch_fd)
        try:
            binds.insert(0, (source_fd, '/src'))
            as_bytes = args.as_mb * 1024 * 1024 if args.as_mb else None
            result = sandboxed(args.command, env=env, binds=binds, chdir='/src', limits=limits, as_bytes=as_bytes)
        finally:
            for fd, _ in binds:
                if isinstance(fd, int):
                    os.close(fd)
            remove_export(*export)
    except Refused as exc:
        return fail(f'{exc}; nothing ran')
    record = {
        'check': args.check, 'ref': args.ref, 'command': args.command, 'limits': limits,
        'as_mb': args.as_mb, 'env_names': sorted(env), 'tools': tools, 'inputs': args.inputs,
        'network': 'loopback only', 'status': result['status'], 'killed': result['killed'],
        'duration_s': result['duration_s'], 'scope': f'{result["unit"]}.scope', 'submodules_omitted': submodules,
    }
    for stream in ('stdout', 'stderr'):
        data = result[stream]
        record[stream] = {'bytes': result[f'{stream}_bytes'], 'kept': len(data),
                          'truncated': result[f'{stream}_bytes'] > len(data),
                          'sha256': hashlib.sha256(data).hexdigest()}
    for name, data in zip(names, (result['stdout'], result['stderr'],
                                  (json.dumps(record, indent=2, sort_keys=True) + '\n').encode())):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=evidence_fd)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
    sys.stdout.buffer.write(result['stdout'])
    sys.stdout.flush()
    sys.stderr.buffer.write(result['stderr'])
    sys.stderr.flush()
    print(f'sandbox-run: {json.dumps({k: record[k] for k in ("check", "status", "killed", "duration_s")})}'
          f' — evidence in {evidence}/{args.check}.*', file=sys.stderr)
    return result['status']


# --------------------------------------------------------------------------- promote


class Rejected(Exception):
    pass


def split_relative(path: str) -> list[str]:
    parts = path.split('/')
    if not path or path.startswith('/') or any(not COMPONENT.fullmatch(part) for part in parts):
        raise Rejected('the path must be relative, with plain components (no ".", "..", empty or hidden parts)')
    return parts


def walk(root_fd: int, parts: list[str], create: bool, made: set[tuple[int, int]] | None = None) -> int:
    """A descriptor of the directory parts[...] below root_fd, never following a link; adds the identity of
    each directory on the way to made."""
    fd = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                child = open_dir(part, dir_fd=fd)
            except OSError as exc:
                if exc.errno == errno.ENOENT and not create:
                    raise FileNotFoundError(part) from None  # nothing was written there: missing, not unsafe
                raise Rejected(f'{part}: not a plain directory ({errno.errorcode.get(exc.errno, exc.errno)})')
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise Rejected(f'{part}: not a directory')
            if made is not None:
                made.add(identity(st))
        return fd
    except BaseException:
        os.close(fd)
        raise


def promote_one(scratch_fd: int, artifacts_fd: int, path: str, limit: int, budget: int) -> tuple[int, str]:
    """Copy one file; returns its size and sha256. Raises FileNotFoundError or Rejected."""
    parts = split_relative(path)
    source_dir = walk(scratch_fd, parts[:-1], create=False)
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=source_dir)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise FileNotFoundError(path) from None
            raise Rejected(f'cannot open without following a link ({errno.errorcode.get(exc.errno, exc.errno)})')
    finally:
        os.close(source_dir)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise Rejected('not a regular file')
        if before.st_nlink != 1:
            raise Rejected(f'has {before.st_nlink} links')
        if before.st_size > limit:
            raise Rejected(f'{before.st_size} bytes exceed its limit of {limit}')
        if before.st_size > budget:
            raise Rejected(f'{before.st_size} bytes exceed the {budget} bytes left of the total limit')
        data = bytearray()
        while len(data) <= before.st_size:
            chunk = os.read(fd, min(65536, before.st_size + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        after = os.fstat(fd)
        same = (before.st_dev, before.st_ino, before.st_mode, before.st_nlink, before.st_size) == \
            (after.st_dev, after.st_ino, after.st_mode, after.st_nlink, after.st_size)
        if len(data) != before.st_size or not same:
            raise Rejected('changed while it was read')
    finally:
        os.close(fd)
    dest_dir = walk(artifacts_fd, parts[:-1], create=True)
    try:
        try:
            out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                          dir_fd=dest_dir)
        except FileExistsError:
            # An apply that stopped after this copy promotes again: the same bytes are the same evidence.
            if same_copy(dest_dir, parts[-1], data):
                return len(data), hashlib.sha256(data).hexdigest()
            raise Rejected('the destination exists already with other content') from None
        try:
            info = os.fstat(out)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise Rejected('the destination is not a new regular file')
            view = memoryview(bytes(data))
            while view:
                view = view[os.write(out, view):]
        except OSError as exc:
            # A full disk or a file-size limit: no partial copy may stay behind as if it were evidence.
            os.close(out)
            out = None
            with contextlib.suppress(OSError):
                os.unlink(parts[-1], dir_fd=dest_dir)
            raise Rejected(f'the copy could not be written ({errno.errorcode.get(exc.errno, exc.errno)}); '
                           'nothing was kept') from None
        finally:
            if out is not None:
                os.close(out)
    finally:
        os.close(dest_dir)
    return len(data), hashlib.sha256(data).hexdigest()


def same_copy(dir_fd: int, name: str, data: bytes) -> bool:
    """Whether name below dir_fd is a regular file with one link that holds exactly data; nothing is changed."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(data):
            return False
        held = bytearray()
        while len(held) <= len(data):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            held += chunk
        return bytes(held) == data
    finally:
        os.close(fd)


def cmd_promote(args) -> int:
    allowed: list[tuple[str, int]] = []
    for item in args.allow:
        path, sep, size = item.rpartition(':')
        if not sep or not size.isdigit() or int(size) < 1:
            return fail(f'--allow takes <path>:<bytes>: {item}')
        allowed.append((path, int(size)))
    if args.total < 1:
        return fail('--total must be positive')
    try:
        # Both from / without following a link: a linked runs/scratch must not lead the copy elsewhere.
        scratch_fd = open_real(Path(args.scratch), '--scratch')
        artifacts_fd = open_real(Path(args.artifacts), '--artifacts')
    except Refused as exc:
        return fail(str(exc))
    try:
        fcntl.flock(scratch_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return fail('a sandbox run still holds the scratch directory; promote after every run has ended', 3)
    left, rejected = args.total, 0
    for path, limit in allowed:
        entry = {'path': path}
        try:
            size, digest = promote_one(scratch_fd, artifacts_fd, path, limit, left)
        except FileNotFoundError:
            entry['status'] = 'missing'
        except (Rejected, OSError) as exc:
            entry.update(status='rejected', reason=str(exc))
            rejected += 1
        else:
            left -= size
            entry.update(status='promoted', bytes=size, sha256=digest)
        print(json.dumps(entry, sort_keys=True))
    return 1 if rejected else 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 9):
        return fail('Python 3.9 or later is required')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('probe', help='report which controls are enforced here')
    p.add_argument('--json', action='store_true')
    r = sub.add_parser('run', help='run one check in the sandbox')
    r.add_argument('--scratch', required=True, help='this agent\'s scratch directory')
    r.add_argument('--ref', required=True, help='full id of the audited commit')
    r.add_argument('--check', required=True, help='evidence name, for example c01')
    r.add_argument('--inputs', help='a directory inside the scratch directory, mounted read-only at /input')
    r.add_argument('--tool', action='append', default=[], help='an absolute toolchain directory, mounted read-only at its own path '
                   '(also an entry of /etc beyond ETC_ENTRIES, a file or directory, as /etc/<name>)')
    r.add_argument('--env', action='append', default=[], help='NAME=VALUE with a dummy, non-secret value')
    for name, (default, maximum) in LIMITS.items():
        r.add_argument(f'--{name.replace("_", "-")}', type=int, help=f'default {default}, at most {maximum}')
    r.add_argument('--as-mb', type=int, help='address-space limit; breaks V8, the JVM, Go and sanitizers')
    r.add_argument('command', nargs=argparse.REMAINDER)
    m = sub.add_parser('promote', help='copy allowlisted evidence into the artifacts directory (parent only)')
    m.add_argument('--scratch', required=True)
    m.add_argument('--artifacts', required=True)
    m.add_argument('--allow', action='append', default=[], required=True, help='<scratch-relative path>:<bytes>')
    m.add_argument('--total', type=int, required=True, help='cumulative byte limit')
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 125 if exc.code else 0
    if args.action == 'run':
        if args.command[:1] == ['--']:
            args.command = args.command[1:]
        if args.as_mb is not None and not 64 <= args.as_mb <= 65536:
            return fail('--as-mb must be 64-65536')
    return {'probe': cmd_probe, 'run': cmd_run, 'promote': cmd_promote}[args.action](args)


if __name__ == '__main__':
    sys.exit(main())
