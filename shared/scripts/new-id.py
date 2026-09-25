#!/usr/bin/env python3
"""Reserve the next TASK or WAVE ID in development/ so parallel sessions never share one.

  new-id.py --kind task --slug csv-export development   -> development/tasks/TASK-8-csv-export
  new-id.py --kind wave development                     -> development/waves/WAVE-3.md

The number is the highest existing one plus one; for waves, IDs named in project/roadmap.md count too.
The reservation is an empty file created exclusively: tasks/.ids/TASK-<N> (task directories differ by
slug, so they cannot reserve a number themselves) or waves/WAVE-<N>.md. On a collision the next number
is tried. Prints the reserved path. Exit codes: 0 reserved, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+){0,4}$')
PATTERNS = {'task': re.compile(r'^TASK-(\d+)(-|$)'), 'wave': re.compile(r'^WAVE-(\d+)(\.md|-review\.md)?$')}
MAX_ATTEMPTS = 100


def highest(development: Path, directory: Path, kind: str) -> int:
    entries = list(directory.iterdir())
    if kind == 'task':
        entries += list((directory / '.ids').iterdir())
    numbers = [int(m.group(1)) for entry in entries if (m := PATTERNS[kind].match(entry.name))]
    roadmap = development / 'project' / 'roadmap.md'
    if kind == 'wave' and roadmap.is_file():
        numbers += [int(n) for n in re.findall(r'\bWAVE-(\d+)\b', roadmap.read_text(encoding='utf-8'))]
    return max(numbers, default=0)


def create_exclusive(path: Path) -> None:
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))


def reserve(development: Path, kind: str, slug: str | None) -> Path:
    directory = development / ('tasks' if kind == 'task' else 'waves')
    directory.mkdir(parents=True, exist_ok=True)
    if kind == 'task':
        (directory / '.ids').mkdir(exist_ok=True)
    number = highest(development, directory, kind) + 1
    for _ in range(MAX_ATTEMPTS):
        try:
            if kind == 'task':
                create_exclusive(directory / '.ids' / f'TASK-{number}')
                path = directory / f'TASK-{number}-{slug}'
                path.mkdir()
            else:
                path = directory / f'WAVE-{number}.md'
                create_exclusive(path)
            return path
        except FileExistsError:
            number = max(number + 1, highest(development, directory, kind) + 1)
    raise RuntimeError(f'no free {kind} ID after {MAX_ATTEMPTS} attempts in {directory}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', choices=PATTERNS, required=True)
    parser.add_argument('--slug', help='task slug: lowercase words joined by hyphens, at most 5 words')
    parser.add_argument('development', type=Path, help='the development/ directory')
    args = parser.parse_args(argv)
    if args.kind == 'task' and not (args.slug and SLUG_RE.match(args.slug)):
        parser.error('--kind task needs --slug of 1-5 lowercase words joined by hyphens')
    if args.kind == 'wave' and args.slug:
        parser.error('--slug applies only to tasks')
    if not args.development.is_dir():
        parser.error(f'not a directory: {args.development}')
    try:
        print(reserve(args.development, args.kind, args.slug))
    except (OSError, RuntimeError) as exc:
        print(f'new-id: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
