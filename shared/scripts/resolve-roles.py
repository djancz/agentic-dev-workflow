#!/usr/bin/env python3
"""Resolve the agent, model and effort of each wf role, and the review round cap.

  resolve-roles.py --agents-md AGENTS.md --roles rev,fix TASK-7 rev=codex,,high fix-agent=claude
  -> rev: agent=codex model=default effort=high
     fix: agent=claude model=default effort=default
     rounds: 3

Roles: test (wf test), rev (reviews), sec (security review), fix (review fixes). Each role takes either
the shorthand <role>=agent[,model[,effort]], where an omitted or empty part is `default`, or the long
form <role>-agent=, <role>-model=, <role>-effort=; never both for one role. Each field comes from the
argument, else from the wf block in AGENTS.md unless it says `default`, else from models=default|auto
(argument, then the block's Models) for a model or effort, and `self` for an agent. rounds= comes from
the argument, else Max review rounds, else 3, and must be 1-10. Words without `=` (task or wave IDs)
are ignored. With --before-init (spec and roadmap reviews of a new project), a missing AGENTS.md or wf
block means built-in defaults. Exit codes: 0 resolved, 2 invalid argument or block.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROLES = ('test', 'rev', 'sec', 'fix')
FIELDS = ('agent', 'model', 'effort')
AGENTS = {'self', 'claude', 'codex', 'gemini', 'opencode'}
MAX_ROUNDS = 10
DEFAULT_ROUNDS = 3
RENAMED_OPTIONS = {
    'reviewer': 'rev-agent= or rev=', 'model': 'rev-model=, test-model= or sec-model=',
    'effort': 'rev-effort=, test-effort= or sec-effort=', 'agent': 'test-agent= or sec-agent=',
    'security-reviewer': 'sec-agent= or sec=', 'security-model': 'sec-model=',
    'security-effort': 'sec-effort=',
}
RENAMED_KEYS = {
    'Reviewer': 'Rev agent', 'Review model': 'Rev model', 'Review effort': 'Rev effort',
    'Security reviewer': 'Sec agent', 'Security model': 'Sec model', 'Security effort': 'Sec effort',
}


def block_settings(text: str, before_init: bool = False) -> dict[str, str]:
    """`- Key: value` lines of the managed wf block, without HTML comments."""
    match = re.search(r'<!-- wf:begin.*?-->(.*?)<!-- wf:end -->', text, flags=re.DOTALL)
    if not match:
        if before_init:
            return {}
        raise ValueError('AGENTS.md has no wf block; run wf init')
    body = re.sub(r'<!--.*?-->', '', match[1], flags=re.DOTALL)
    settings = dict(re.findall(r'^- ([A-Z][A-Za-z ]*?): *(.*?) *$', body, flags=re.MULTILINE))
    for old, new in RENAMED_KEYS.items():
        if old in settings:
            raise ValueError(f'the wf block uses the old key "{old}"; run wf init to rename it to "{new}"')
    return settings


def parse_arguments(args: list[str]) -> dict[str, str]:
    """key=value arguments; the shorthand is expanded to the long form."""
    given: dict[str, str] = {}
    for arg in args:
        if '=' not in arg:
            continue
        key, value = arg.split('=', 1)
        if key in RENAMED_OPTIONS:
            raise ValueError(f'{key}= was renamed; use {RENAMED_OPTIONS[key]}')
        if not value and key in {'models', 'rounds'}:
            raise ValueError(f'{key}= is empty')
        if key in ROLES:
            parts = value.split(',')
            if len(parts) > len(FIELDS):
                raise ValueError(f'{key}= takes agent[,model[,effort]], got "{value}"')
            parts += [''] * (len(FIELDS) - len(parts))
            pairs = [(f'{key}-{field}', part or 'default') for field, part in zip(FIELDS, parts)]
        elif key in {f'{role}-{field}' for role in ROLES for field in FIELDS} | {'models', 'rounds'}:
            pairs = [(key, value)]
        else:
            raise ValueError(f'unknown option {key}=')
        for name, item in pairs:
            if name in given:
                role = name.split('-')[0]
                if role in ROLES:
                    raise ValueError(f'{name} is set twice; for one role use either {role}= or '
                                     f'{role}-agent=/{role}-model=/{role}-effort=')
                raise ValueError(f'{name}= is set twice')
            given[name] = item
    return given


def resolve(given: dict[str, str], block: dict[str, str], roles: list[str]) -> list[str]:
    models = given.get('models') or block.get('Models') or 'default'
    if models not in {'default', 'auto'}:
        raise ValueError(f'models must be default or auto, got "{models}"')
    lines = []
    for role in roles:
        values = {}
        for field in FIELDS:
            value = given.get(f'{role}-{field}')
            if value is None:
                value = block.get(f'{role.capitalize()} {field}', 'default')
                if value == 'default':
                    value = 'self' if field == 'agent' else models
            if not value:
                raise ValueError(f'{role}-{field} is empty')
            if field == 'agent' and value == 'default':
                value = 'self'
            if field == 'agent' and value not in AGENTS:
                raise ValueError(f'unknown agent for {role}: {value} (use {"|".join(sorted(AGENTS))})')
            values[field] = value
        lines.append(f'{role}: ' + ' '.join(f'{field}={values[field]}' for field in FIELDS))
    rounds = given.get('rounds') or block.get('Max review rounds') or str(DEFAULT_ROUNDS)
    if not rounds.isdigit() or not 1 <= int(rounds) <= MAX_ROUNDS:
        raise ValueError(f'rounds must be an integer from 1 to {MAX_ROUNDS}, got "{rounds}"')
    return lines + [f'rounds: {int(rounds)}']


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agents-md', required=True, help='AGENTS.md with the wf block, or - for stdin')
    parser.add_argument('--roles', default=','.join(ROLES), help='comma-separated roles to print')
    parser.add_argument('--before-init', action='store_true',
                        help='accept a missing AGENTS.md or wf block (new project before wf init)')
    parser.add_argument('args', nargs='*', help='the stage arguments, for example TASK-7 rev=codex,,high')
    args = parser.parse_args(argv)
    roles = args.roles.split(',')
    if not set(roles) <= set(ROLES):
        parser.error(f'--roles takes {",".join(ROLES)}')
    try:
        path = Path(args.agents_md)
        if args.agents_md == '-':
            text = sys.stdin.read()
        elif args.before_init and not path.exists():
            text = ''
        else:
            text = path.read_text(encoding='utf-8')
        lines = resolve(parse_arguments(args.args), block_settings(text, args.before_init), roles)
    except (OSError, ValueError) as exc:
        print(f'resolve-roles: {exc}', file=sys.stderr)
        return 2
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
