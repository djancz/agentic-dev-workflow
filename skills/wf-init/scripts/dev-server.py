#!/usr/bin/env python3
"""Start a project's dev server, wait until it answers, and always stop it again.

Usage:
  dev-server.py run   --url URL --server CMD [--timeout S] -- TEST-COMMAND...
  dev-server.py start --url URL --server CMD [--timeout S]
  dev-server.py stop

run    start the server, run TEST-COMMAND against it, stop the server; exit with the test's code.
start  start the server in the background and return once it answers (for a browser check).
stop   stop the server that `start` left running; does nothing when there is none.

The server command runs through `sh -c` in its own process group and must stay in the foreground.
Stopping it stops the whole group, including the processes it spawned (bundlers, reloaders). The server
is ready once URL returns any HTTP response. If URL already answers before the start, nothing runs: a
server left over from earlier may serve old code and turn a failure into a false pass.

Exit codes: 0 ok (run: the test command's code), 2 usage error or URL already served,
            3 server exited before it was ready, 124 server not ready within the timeout
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

POLL_SECONDS = 0.5
STOP_GRACE_SECONDS = 10.0
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never ask a proxy about localhost


class Failure(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def answers(url: str) -> bool:
    """True once the URL returns any HTTP response, including 4xx and 5xx."""
    try:
        with OPENER.open(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, http.client.HTTPException):
        return False


def log_tail(log: Path, lines: int = 40) -> str:
    try:
        return '\n'.join(log.read_text(errors='replace').splitlines()[-lines:])
    except OSError:
        return ''


def refuse_if_served(url: str) -> None:
    if answers(url):
        raise Failure(2, f'{url} already answers. Stop that server first: a server this run did not '
                         'start may serve old code.')


def launch(command: str, log: Path) -> subprocess.Popen:
    with open(log, 'ab') as out:
        return subprocess.Popen(['sh', '-c', command], stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)


def wait_ready(url: str, proc: subprocess.Popen, timeout: float, log: Path) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise Failure(3, f'the server exited with code {proc.returncode} before {url} answered. '
                             f'Log tail:\n{log_tail(log)}')
        if answers(url):
            return
        if time.monotonic() >= deadline:
            raise Failure(124, f'{url} did not answer within {timeout:g} s. Log tail:\n{log_tail(log)}')
        time.sleep(POLL_SECONDS)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_group(pgid: int, proc: subprocess.Popen | None = None) -> None:
    """SIGTERM the whole process group, then SIGKILL whatever is left after the grace period."""
    for sig, grace in ((signal.SIGTERM, STOP_GRACE_SECONDS), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if proc is not None:
                proc.poll()  # reap the leader, or it lingers as a zombie in the group
            if not group_alive(pgid):
                return
            time.sleep(0.1)


def state_dir() -> Path:
    """Per-repository state for start/stop, outside the working tree so it never dirties it."""
    try:
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
        base = top.stdout.strip() if top.returncode == 0 else os.getcwd()
    except OSError:
        base = os.getcwd()
    key = hashlib.sha256(os.path.realpath(base).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f'wf-dev-server-{key}'


def cmd_run(args: argparse.Namespace, test: list[str]) -> int:
    if not test:
        raise Failure(2, 'run needs the test command after --')
    refuse_if_served(args.url)
    with tempfile.TemporaryDirectory(prefix='wf-dev-server-') as tmp:
        log = Path(tmp) / 'server.log'
        proc = launch(args.server, log)
        try:
            wait_ready(args.url, proc, args.timeout, log)
            try:
                code = subprocess.run(test).returncode
            except FileNotFoundError:
                raise Failure(2, f'test command not found: {test[0]}')
            if code != 0:
                print(f'dev-server: tests failed with code {code}. Server log tail:\n{log_tail(log)}',
                      file=sys.stderr)
            return code
        finally:
            stop_group(proc.pid, proc)


def cmd_start(args: argparse.Namespace) -> int:
    directory = state_dir()
    state = directory / 'state.json'
    if state.exists():
        old = json.loads(state.read_text())
        if group_alive(old['pgid']):
            raise Failure(2, f"the server started for {old['url']} is still running; run stop first")
        state.unlink()
    refuse_if_served(args.url)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = directory / 'server.log'
    log.write_bytes(b'')
    proc = launch(args.server, log)
    try:
        wait_ready(args.url, proc, args.timeout, log)
    except BaseException:
        stop_group(proc.pid, proc)
        raise
    state.write_text(json.dumps({'pgid': proc.pid, 'url': args.url, 'log': str(log)}))
    print(f'ready: {args.url}\nlog: {log}')
    return 0


def cmd_stop() -> int:
    state = state_dir() / 'state.json'
    if not state.exists():
        print('no server started by dev-server.py is running')
        return 0
    old = json.loads(state.read_text())
    stop_group(old['pgid'])
    state.unlink()
    print(f"stopped: {old['url']} (log kept at {old['log']})")
    return 0


def positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def http_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise argparse.ArgumentTypeError('must be an http:// or https:// URL')
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest='mode', required=True)
    for name in ('run', 'start'):
        mode = modes.add_parser(name)
        mode.add_argument('--url', required=True, type=http_url, help='URL that answers once the server is ready')
        mode.add_argument('--server', required=True, help='command that starts the server in the foreground')
        mode.add_argument('--timeout', type=positive, default=120.0, help='seconds to wait for URL (default 120)')
    modes.add_parser('stop')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    test: list[str] = []
    if '--' in argv:
        split = argv.index('--')
        argv, test = argv[:split], argv[split + 1:]
    args = parse_args(argv)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))  # run the cleanup
    try:
        if args.mode == 'run':
            return cmd_run(args, test)
        if test:
            raise Failure(2, f'{args.mode} takes no command after --')
        return cmd_start(args) if args.mode == 'start' else cmd_stop()
    except Failure as failure:
        print(f'dev-server: {failure}', file=sys.stderr)
        return failure.code
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
