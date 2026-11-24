#!/usr/bin/env python3
"""Resolve the agent, model and effort of each wf role, its usage-limit fallbacks, and the round cap.

  resolve-roles.py --agents-md AGENTS.md --roles rev,fix TASK-7 rev=codex,,high fix-agent=claude
  -> rev: agent=codex model=default effort=high
     rev fallback 1: agent=claude model=model-b effort=medium
     fix: agent=claude model=default effort=default
     rounds: 3

Roles: test (wf test), rev (reviews), sec (security review), fix (review fixes), and the two audit roles
hunt (wf sec audit: reconnaissance and hunters) and verify (wf sec audit: coverage critics and verifiers),
which only wf sec audit asks for. Their settings are personal and never committed. Two files hold them: the user file
${XDG_CONFIG_HOME:-~/.config}/agentic-dev-workflow/settings.md for every project, and
development/wf-settings.md of the current repository, which overrides the user file key by key. Their
`- Key: value` lines (HTML comments are ignored) are:

  Test, Rev, Sec, Fix, Hunt, Verify: agent[,model[,effort]]
                                                an omitted, empty or `default` part follows Models
  <Role> fallback: agent[,model[,effort]]       up to 3 lines, tried in order after a usage limit; or none.
                                                A fallback names its agent; one equal to the role's choice
                                                or to an earlier fallback is left out
  Models: default | auto
  Max review rounds: 1-10

A role takes the argument first: the shorthand <role>=agent[,model[,effort]], where an omitted or empty
part is `default`, or the long form <role>-agent=, <role>-model=, <role>-effort=; never both for one
role. Otherwise each field comes from the project file, else the user file, else the deprecated role keys
of the wf block in AGENTS.md (a warning on stderr names those that are not default), else from
models=default|auto (argument, then settings, then the block's Models) for a model or effort, and `self`
for an agent. Fallbacks come only from the settings files; an argument for the role keeps them.
rounds= comes from the argument, else Max review rounds of the settings, then of the block, else 3, and
must be 1-10. The audit roles never read the wf block, neither role keys nor Models: it is committed,
and a repository must not choose who audits it. Words without `=` (task or wave IDs) are ignored. With
--before-init (spec and roadmap reviews of a new project), a missing AGENTS.md or wf block means built-in
defaults.

--sources ends each line with `(from <source>)`: a settings file, AGENTS.md, argument or default; a role
whose fields come partly from arguments reads `(from argument, <source>)`, `; models=auto from
<source>` names where Models came from when it filled a field, and `<role> fallback: none` shows a file
that turned the fallbacks off. Its first line, `settings: <sources>`, names where the human set a role
(test, rev, sec or fix, printed or not; fallbacks and the audit roles do not count), or reads
`settings: none` when none of them is set and the role setup should ask. --roles prints test, rev, sec and
fix unless it names others. --paths prints the absolute paths of
the user and project files (`user: <path>`, `project: <path>|none`) and needs no --agents-md.
A project file that git tracks is refused: the settings of one person must not reach the others.
Exit codes: 0 resolved, 2 invalid argument, settings file or block.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CORE_ROLES = ('test', 'rev', 'sec', 'fix')
# Only wf sec audit uses these; they never decide whether the role setup asks.
AUDIT_ROLES = ('hunt', 'verify')
ROLES = CORE_ROLES + AUDIT_ROLES
FIELDS = ('agent', 'model', 'effort')
AGENTS = {'self', 'claude', 'codex', 'gemini', 'opencode'}
MAX_ROUNDS = 10
DEFAULT_ROUNDS = 3
MAX_FALLBACKS = 3
PROJECT_SETTINGS = 'development/wf-settings.md'
# A model name never starts with `-` and has no spaces, so it cannot pass as another CLI option.
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]*')
EFFORT = re.compile(r'[a-z]+')
SETTING_KEYS = {key.lower(): key for key in (
    'Models', 'Max review rounds', *(role.capitalize() for role in ROLES),
    *(f'{role.capitalize()} fallback' for role in ROLES))}
BLOCK_ROLE_KEYS = [f'{role.capitalize()} {field}' for role in CORE_ROLES for field in FIELDS]
RENAMED_OPTIONS = {
    'reviewer': 'rev-agent= or rev=', 'model': 'rev-model=, test-model= or sec-model=',
    'effort': 'rev-effort=, test-effort= or sec-effort=', 'agent': 'test-agent= or sec-agent=',
    'security-reviewer': 'sec-agent= or sec=', 'security-model': 'sec-model=',
    'security-effort': 'sec-effort=',
}
RENAMED_KEYS = {
    'Reviewer': 'Rev', 'Review model': 'Rev', 'Review effort': 'Rev',
    'Security reviewer': 'Sec', 'Security model': 'Sec', 'Security effort': 'Sec',
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
            raise ValueError(f'the wf block uses the old key "{old}"; run wf init to move it to '
                             f'{PROJECT_SETTINGS} as "{new}"')
    return settings


def is_rounds(value: str) -> bool:
    """A round cap: ASCII digits only (`²` is a digit to str.isdigit), from 1 to MAX_ROUNDS."""
    return value.isascii() and value.isdigit() and 1 <= int(value) <= MAX_ROUNDS


def check_value(field: str, value: str, where: str) -> None:
    """An agent, model or effort value; empty means not set."""
    if value in {'', 'default'} or value == 'auto' and field != 'agent':
        return
    if field == 'agent' and value not in AGENTS:
        raise ValueError(f'unknown agent for {where}: {value} (use {"|".join(sorted(AGENTS))})')
    if field == 'model' and not MODEL.fullmatch(value):
        raise ValueError(f'invalid model for {where}: "{value}" (a name without spaces that does not '
                         'start with -)')
    if field == 'effort' and not EFFORT.fullmatch(value):
        raise ValueError(f'invalid effort for {where}: "{value}" (a lowercase level such as high)')


def parse_triple(value: str, where: str) -> tuple[str, str, str]:
    """agent[,model[,effort]]; a missing part is empty."""
    parts = [part.strip() for part in value.split(',')]
    if len(parts) > len(FIELDS):
        raise ValueError(f'{where} takes agent[,model[,effort]], got "{value}"')
    parts += [''] * (len(FIELDS) - len(parts))
    for field, part in zip(FIELDS, parts):
        check_value(field, part, where)
    return parts[0], parts[1], parts[2]


def read_settings(text: str, where: str) -> dict:
    """The keys of one settings file; a fallback key holds the list of its lines, in order."""
    body = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    settings: dict = {}
    # Every bullet that starts with a word and a colon is a key, so a misspelt key is never ignored.
    pattern = r'^[ \t]*[-*+][ \t]+([A-Za-z][^:\n]*?)[ \t]*:[ \t]*(.*?)[ \t]*$'
    for raw_key, value in re.findall(pattern, body, flags=re.MULTILINE):
        key = SETTING_KEYS.get(' '.join(raw_key.split()).lower())
        if key is None:
            known = ', '.join(sorted(SETTING_KEYS.values()))
            raise ValueError(f'{where}: unknown key "{raw_key}" (use {known})')
        if not value:
            raise ValueError(f'{where}: {key} is empty')
        if key.endswith(' fallback'):
            entries = settings.setdefault(key, [])
            if value == 'none' or 'none' in entries:
                if entries:
                    raise ValueError(f'{where}: {key}: none cannot be combined with other {key} lines')
                entries.append('none')
                continue
            entry = parse_triple(value, f'{where}: {key}')
            if entry[0] in {'', 'default'}:
                raise ValueError(f'{where}: {key} must name its agent, got "{value}"')
            entries.append(entry)
            if len(entries) > MAX_FALLBACKS:
                raise ValueError(f'{where}: at most {MAX_FALLBACKS} {key} lines')
            continue
        if key in settings:
            raise ValueError(f'{where}: {key} is set more than once')
        if key == 'Models' and value not in {'default', 'auto'}:
            raise ValueError(f'{where}: Models must be default or auto, got "{value}"')
        if key == 'Max review rounds' and not is_rounds(value):
            raise ValueError(f'{where}: Max review rounds must be an integer from 1 to {MAX_ROUNDS}, '
                             f'got "{value}"')
        settings[key] = parse_triple(value, f'{where}: {key}') if key.lower() in ROLES else value
    for key, entries in settings.items():
        if key.endswith(' fallback') and entries == ['none']:
            settings[key] = []
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
            if not item:
                raise ValueError(f'{name} is empty')
            if '-' in name:
                role, field = name.split('-', 1)
                check_value(field, item, role)
            given[name] = item
    return given


def deprecated_block_keys(block: dict[str, str]) -> list[str]:
    """Role keys and Models that the wf block still sets to something other than the default."""
    keys = [key for key in BLOCK_ROLE_KEYS if block.get(key, 'default') not in {'default', 'self'}]
    return keys + (['Models'] if block.get('Models', 'default') != 'default' else [])


def shown(path: Path) -> str:
    """A path as the human reads it, with ~ for the home directory."""
    home = str(Path.home())
    text = str(path)
    return '~' + text[len(home):] if home not in {'', '/'} and text.startswith(home + '/') else text


def resolve(given: dict[str, str], block: dict[str, str], roles: list[str], settings: dict,
            sources: dict[str, str] | None = None) -> list[str]:
    """The role, fallback and rounds lines; with sources (key -> file), each line says where it came from
    and a first `settings:` line names the sources of all role settings, or says none is set."""
    known = sources or {}

    def view(role: str) -> dict[str, str]:
        """The wf block as a role reads it. The block is committed, so the audit roles, which treat the
        repository as untrusted, never read it: a repository must not choose its own auditors."""
        return block if role in CORE_ROLES else {}

    def models_of(keys: dict[str, str]) -> tuple[str, str]:
        """Models and where it came from."""
        models = given.get('models') or settings.get('Models') or keys.get('Models') or 'default'
        if models not in {'default', 'auto'}:
            raise ValueError(f'models must be default or auto, got "{models}"')
        return models, ('argument' if 'models' in given else
                        known.get('Models', 'settings') if 'Models' in settings else
                        'AGENTS.md' if 'Models' in keys else 'default')

    def complete(field: str, value: str, used: list[bool], models: str) -> str:
        if value in {'', 'default'}:
            if field == 'agent':
                return 'self'
            used.append(models == 'auto')
            return models
        return value

    def origin(name: str, keys: dict[str, str]) -> str:
        """Where the role or fallback key `name` is set, apart from run arguments."""
        if name in settings:
            return known.get(name, 'settings')
        if not name.endswith(' fallback') and any(keys.get(f'{name} {field}', 'default') not in
                                                  {'', 'default', 'self'} for field in FIELDS):
            return 'AGENTS.md'
        return 'default'

    def line(text: str, source: str, used: list[bool], models_source: str = '') -> str:
        if sources is None:
            return text
        note = f'; models=auto from {models_source}' if any(used) else ''
        return f'{text}  (from {source}{note})'

    def triple(values: dict[str, str]) -> str:
        return ' '.join(f'{field}={values[field]}' for field in FIELDS)

    models_of(block)  # an invalid Models of the block is an error whichever roles are printed
    lines = []
    for role in roles:
        name, keys = role.capitalize(), view(role)
        models, models_source = models_of(keys)
        from_block = name not in settings
        base = (dict(zip(FIELDS, settings[name])) if not from_block
                else {field: keys.get(f'{name} {field}', '') for field in FIELDS})
        values, used = {}, []
        for field in FIELDS:
            value = given.get(f'{role}-{field}')
            if value is None:
                if from_block:  # an old block line counts only where no argument overrides it
                    check_value(field, base[field], f'the wf block\'s {name} {field}')
                value = complete(field, base[field], used, models)
            elif field == 'agent' and value == 'default':
                value = 'self'
            values[field] = value
        overridden = [field for field in FIELDS if f'{role}-{field}' in given]
        source = ('argument' if len(overridden) == len(FIELDS) else
                  f'argument, {origin(name, keys)}' if overridden else origin(name, keys))
        lines.append(line(f'{role}: {triple(values)}', source, used, models_source))
        # A fallback equal to the role's choice or to an earlier fallback would retry the same limit.
        tried = [values]
        if sources is not None and settings.get(f'{name} fallback') == []:
            lines.append(line(f'{role} fallback: none', origin(f'{name} fallback', keys), []))
        for entry in settings.get(f'{name} fallback', []):
            used = []
            fallback = {field: complete(field, part, used, models) for field, part in zip(FIELDS, entry)}
            if fallback in tried:
                continue
            tried.append(fallback)
            lines.append(line(f'{role} fallback {len(tried) - 1}: {triple(fallback)}',
                              origin(f'{name} fallback', keys), used, models_source))
    rounds = (given.get('rounds') or settings.get('Max review rounds') or block.get('Max review rounds')
              or str(DEFAULT_ROUNDS))
    if not is_rounds(rounds):
        raise ValueError(f'rounds must be an integer from 1 to {MAX_ROUNDS}, got "{rounds}"')
    rounds_source = ('argument' if 'rounds' in given else
                     known.get('Max review rounds', 'settings') if 'Max review rounds' in settings
                     else 'AGENTS.md' if 'Max review rounds' in block else 'default')
    lines.append(line(f'rounds: {int(rounds)}', rounds_source, []))
    if sources is not None:
        # Every core role counts, not only the printed ones: the question is whether the human chose any
        # role. A fallback alone is no choice of a role, and neither is an audit role, so they leave the
        # setup to ask.
        found = list(dict.fromkeys(origin(role.capitalize(), block) for role in CORE_ROLES
                                   if origin(role.capitalize(), block) != 'default'))
        lines.insert(0, f'settings: {", ".join(found) if found else "none"}')
    return lines


def user_settings_path() -> Path:
    config = os.environ.get('XDG_CONFIG_HOME', '')
    base = Path(config) if config and Path(config).is_absolute() else Path.home() / '.config'
    return base / 'agentic-dev-workflow' / 'settings.md'


def project_settings_path() -> Path | None:
    """development/wf-settings.md of the repository around the working directory, if there is one."""
    try:
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(top) / PROJECT_SETTINGS if top else None


def tracked(path: Path) -> bool:
    """Whether git tracks the file, or its directory (a committed development/ or link to it)."""
    for directory, name in ((path.parent, path.name), (path.parent.parent, path.parent.name)):
        try:
            listed = subprocess.run(['git', '-C', str(directory), 'ls-files', '--', name],
                                    capture_output=True, text=True)
        except OSError:
            return False
        if listed.returncode == 0 and listed.stdout.strip():
            return True
    return False


def load_settings(user: Path | None, project: Path | None) -> tuple[dict, dict[str, str]]:
    """The user file, overridden key by key by the project file, and the file each key came from; a
    missing file sets nothing, and a tracked project file is refused."""
    merged: dict = {}
    sources: dict[str, str] = {}
    if project is not None and (project.exists() or project.is_symlink()) and tracked(project):
        raise ValueError(f'{shown(project)} is tracked by git, but role settings are personal: remove it '
                         'from the repository (git rm --cached) and keep it ignored')
    for path in (user, project):
        if path is not None and path.exists():
            settings = read_settings(path.read_text(encoding='utf-8'), str(path))
            merged.update(settings)
            sources.update(dict.fromkeys(settings, shown(path)))
    return merged, sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agents-md', help='AGENTS.md with the wf block, or - for stdin (required)')
    parser.add_argument('--roles', default=','.join(CORE_ROLES),
                        help=f'comma-separated roles to print, of {",".join(ROLES)} '
                             f'(default: {",".join(CORE_ROLES)})')
    parser.add_argument('--before-init', action='store_true',
                        help='accept a missing AGENTS.md or wf block (new project before wf init)')
    parser.add_argument('--settings', type=Path,
                        help=f'project settings file (default: {PROJECT_SETTINGS} of the current repository)')
    parser.add_argument('--user-settings', type=Path,
                        help='user settings file (default: agentic-dev-workflow/settings.md in '
                             '$XDG_CONFIG_HOME, else ~/.config)')
    parser.add_argument('--sources', action='store_true',
                        help='say where each line comes from, after a first line naming the role settings')
    parser.add_argument('--paths', action='store_true',
                        help='print the absolute paths of the user and project settings files, and exit')
    parser.add_argument('args', nargs='*', help='the stage arguments, for example TASK-7 rev=codex,,high')
    args = parser.parse_args(argv)
    if args.paths:
        user = (args.user_settings or user_settings_path()).absolute()
        project = args.settings or project_settings_path()
        print(f'user: {user}\nproject: {project.absolute() if project else "none"}')
        return 0
    if args.agents_md is None:
        parser.error('--agents-md is required')
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
        given = parse_arguments(args.args)
        block = block_settings(text, args.before_init)
        settings, sources = load_settings(args.user_settings or user_settings_path(),
                                          args.settings or project_settings_path())
        lines = resolve(given, block, roles, settings, sources if args.sources else None)
    except (OSError, ValueError) as exc:
        print(f'resolve-roles: {exc}', file=sys.stderr)
        return 2
    deprecated = deprecated_block_keys(block)
    if deprecated:
        print(f'resolve-roles: warning: the wf block of AGENTS.md still sets {", ".join(deprecated)}; '
              f'move them to {PROJECT_SETTINGS} (wf init does it)', file=sys.stderr)
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
