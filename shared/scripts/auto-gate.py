#!/usr/bin/env python3
"""Decide whether the autopilot (wf auto) may pass a human gate without the human.

  auto-gate.py plan --agents-md AGENTS.md development/tasks/TASK-7-csv-export
  auto-gate.py wave --agents-md AGENTS.md --base origin/main --head HEAD development/waves/WAVE-3.md

The policy is deliberately narrow and reads only recorded facts. An authorization reads
`<timestamp> — "<the human's words>"`; the words ask for the autopilot by name (`wf auto`, `/wf-auto`,
"autopilot"), and when they name a task or wave, it must be this one.

Gate 1 (plan) passes when the latest authorization in the ticket's Notes qualifies, the plan is a Draft
with a Tests line and no open decisions, and its latest review approved the current revision with no
must-fix findings. Any Size and Sensitivity qualify, because a high-sensitivity task gets its security
review in wf impl loop and Gate 2 requires that review approved.

Gate 2 (wave) passes when the wave record's Autopilot line qualifies and the record lists exactly one
task; that task's plan is recorded as `Approved: revision <current> …` and its plan review approved that
revision with no must-fix findings (a plan passed with accepted findings keeps Gate 2 with the human);
the wave review approved the given HEAD with no must-fix findings; the task's implementation review (and
its security review, required unless the plan says `Sensitivity: normal`) approved its Checkpoint, which
is the tip of the `task/TASK-<N>-<slug>` branch that the ticket records; the full checks table has
exactly one result per check of the wf block, no check is planned, at least one PASS and otherwise only
N/A or `FAIL (pre-existing: <evidence>)` whose result or Evidence cell names the base commit by SHA;
--base is the wave's base branch, is merged into HEAD and contains none of the wave's own commits; every
check command reads statically, without a change of directory or a shell glob where the shell reads it,
a `..` segment, or a path through a symbolic link or into a submodule; and the wave diff (renames
counted on both sides) touches no protected path: dependency manifests and lockfiles, migrations, CI and
repository configuration, agent instructions, check, lint and scanner configuration, build and deploy
files, symbolic links and submodules, files a check command names on the base or HEAD by path or by
file name alone, and the globs of the block's `Autopilot paths`.

The latest round of every review must pass the validator of a delegated round, and the review header must
agree with it. Code fences and HTML comments are read together, so neither hides the other; a comment
opens only at the start of a line, one per line, and a `<!--` in inline code is text. A SHA counts only
when it names exactly one commit.

A block line `- Autopilot: disabled` refuses every gate. Any other value than allowed or disabled, an
indented or repeated Autopilot, Autopilot paths or Base branch line, a repeated check name, a repeated
record header field, a code fence or HTML comment left open or a comment that opens inside a line in a
record, or a record that is not UTF-8 is invalid input.

Exit codes: 0 eligible, 1 not eligible (one reason per line), 2 invalid input.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_roles = load('resolve_roles', 'resolve-roles.py')
validate_review = load('validate_review', 'validate-review.py')
validate_wave_review = load('validate_wave_review', 'validate-wave-review.py')

APPROVED = {'Approved', 'Approved with comments'}
SHA = re.compile(r'[0-9a-f]{7,40}')
REVIEWED_COMMIT = re.compile(r'commit ([0-9a-f]{7,40})')
SAFETY_KEYS = ('Autopilot', 'Autopilot paths', 'Base branch')
SHELL_SEPARATORS = re.compile(r'[\s;&|()<>`\'"=]+')
# The human's words ask for the autopilot by name: `wf auto`, `/wf-auto`, autopilot (also inflected).
ASKS_FOR_AUTOPILOT = re.compile(r'(?i)\bwf[ -]auto\b|\bautopilot')
CODE_SPAN = re.compile(r'(`+)(?!`).*?(?<!`)\1(?!`)')
PROTECTED = (
    # dependencies and toolchain pins
    'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml',
    'yarn.lock', 'bun.lock', 'bun.lockb', '.npmrc', '.yarnrc*', 'lerna.json', 'nx.json', 'turbo.json',
    'requirements*', '*/requirements/*', 'constraints*.txt', 'pyproject.toml', 'setup.py', 'setup.cfg',
    'poetry.lock', 'uv.lock', 'pipfile', 'pipfile.lock', 'cargo.toml', 'cargo.lock', 'go.mod', 'go.sum',
    'go.work*', 'gemfile', 'gemfile.lock', 'composer.json', 'composer.lock', 'pom.xml', 'build.gradle',
    'build.gradle.kts', 'settings.gradle*', 'gradle.lockfile', '*.csproj', 'packages.lock.json', 'mix.exs',
    'mix.lock', 'flake.nix', 'flake.lock', '.nvmrc', '.node-version', '.python-version', '.tool-versions',
    'rust-toolchain*',
    # database migrations
    'migrations/*', '*/migrations/*', 'migrate/*', '*/migrate/*', 'alembic/*', '*/alembic/*', 'alembic.ini',
    # CI, forge and repository configuration
    '.github/*', '*/.github/*', '.gitlab-ci.yml', '.gitlab/*', '.circleci/*', '.buildkite/*',
    'jenkinsfile', 'azure-pipelines.yml', '.woodpecker*', '.drone.yml', 'codeowners',
    '.gitignore', '.gitattributes', '.gitmodules', '.pre-commit-config.yaml',
    # agent instructions and skills
    'agents.md', 'agents.override.md', 'claude.md', 'gemini.md', 'skill.md', '.cursorrules', '.cursor/*',
    '.windsurfrules', '.claude/*', '.agents/*', '.gemini/*', '.opencode/*', '.codex/*',
    # check, lint, type and scanner configuration and ignores
    '.gitleaksignore', '.gitleaks.toml', '.semgrepignore', '.semgrep*', '.bandit', '.trivyignore',
    '.snyk', '.secrets.baseline', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.mypy.ini', '.flake8',
    '.pylintrc', 'pylintrc', 'pytest.ini', 'tox.ini', 'noxfile.py', '.coveragerc',
    '.eslintrc*', 'eslint.config.*', '.eslintignore', '.prettierrc*', 'prettier.config.*', '.prettierignore',
    '.stylelintrc*', 'stylelint.config.*', 'biome.json*', 'tsconfig*.json', 'jest.config.*',
    'vitest.config.*', 'playwright.config.*', 'cypress.config.*', '.golangci*', 'deny.toml', 'clippy.toml',
    'rustfmt.toml', '.rustfmt.toml',
    # build, deploy and secrets
    'makefile', 'gnumakefile', 'justfile', 'taskfile.y*ml', 'cmakelists.txt', '*.cmake', 'meson.build',
    'meson_options.txt', 'build.zig', 'build.zig.zon', 'vite.config.*', 'webpack.config.*',
    'rollup.config.*', 'babel.config.*', '.babelrc*', 'dockerfile*', 'containerfile*', '.dockerignore',
    'docker-compose*', 'compose.y*ml', 'procfile', 'vercel.json', 'netlify.toml', 'fly.toml', '.env*',
)


class InputError(ValueError):
    """The records or arguments cannot be read; not a policy refusal."""


def read(path: Path, record: bool = True) -> str:
    """A file's text; a record must not leave a code fence open, which would hide what follows it."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc.strerror or exc}') from exc
    except UnicodeDecodeError as exc:
        raise InputError(f'cannot read {path}: not UTF-8 ({exc.reason} at byte {exc.start})') from exc
    if record and (problem := scan(text)[1]):
        raise InputError(f'{path}: {problem}')
    return text


def uncomment(text: str) -> str:
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)


def scan(text: str) -> tuple[str, str]:
    """The text without fenced code blocks and HTML comments, and why it cannot be read, if it cannot.

    One pass reads both, so a fence marker inside a comment and a `<!--` inside a fence or an inline code
    span stay literal. A fence closes with at least as many of its own characters and nothing else on the
    line. A comment opens only at the start of a line, as the templates write it, and nothing after it on
    that line opens another; one that opens inside a line, or a fence or comment left open, could hide
    part of the record."""
    kept, fence, comment = [], '', False
    for line in text.splitlines(keepends=True):
        if fence:
            if re.fullmatch(rf'[ \t]*{re.escape(fence)}{re.escape(fence[0])}*[ \t]*', line.rstrip('\r\n')):
                fence = ''
            continue
        if comment:
            end = line.find('-->')
            if end < 0:
                continue
            line, comment = line[end + 3:], False
        elif opening := re.match(r'[ \t]*(`{3,}(?=[^`]*$)|~{3,})', line):
            fence = opening[1]
            continue
        elif line.lstrip().startswith('<!--'):
            start = line.index('<!--')
            end = line.find('-->', start + 4)
            line, comment = (line[:start] + '\n', True) if end < 0 else (line[:start] + line[end + 3:], False)
        if '<!--' in CODE_SPAN.sub('', line):
            return ''.join(kept), 'an HTML comment opens inside a line'
        kept.append(line)
    return ''.join(kept), ('a code fence is not closed' if fence else
                           'an HTML comment is not closed' if comment else '')


def clean(text: str) -> str:
    """The text without fenced code blocks and HTML comments: examples are not records."""
    return scan(text)[0]


def header(text: str, path: Path) -> dict[str, str]:
    """`Key: value` lines (with or without a leading `- `) before the first `## ` section."""
    head = re.split(r'^## ', clean(text), maxsplit=1, flags=re.MULTILINE)[0]
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^(?:- )?([A-Z][A-Za-z -]*?): *(.*?) *$', head, flags=re.MULTILINE):
        if key in fields:
            raise InputError(f'{path}: the header sets {key} more than once')
        fields[key] = value
    return fields


def sections(text: str, title: str) -> list[str]:
    return [body.strip() for body in re.findall(rf'^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |\Z)',
                                                clean(text), flags=re.MULTILINE | re.DOTALL)]


def block(agents_md: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """The wf block's settings and its `| Check | Command |` rows."""
    text = read(agents_md, record=False)
    try:
        settings = resolve_roles.block_settings(text)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    body = uncomment(re.search(r'<!-- wf:begin.*?-->(.*?)<!-- wf:end -->', text, flags=re.DOTALL)[1])
    # resolve-roles reads only `- Key: value` at the start of a line; any other spelling would be ignored.
    for key in SAFETY_KEYS:
        lines = re.findall(rf'^[ \t]*[-*+][ \t]+{re.escape(key)}[ \t]*:.*$', body,
                           flags=re.MULTILINE | re.IGNORECASE)
        if any(not line.startswith(f'- {key}:') for line in lines):
            raise InputError(f'the wf block writes its {key} line differently; write it as "- {key}: …"')
        if len(lines) > 1:
            raise InputError(f'the wf block sets {key} more than once')
    if settings.get('Autopilot', 'allowed') not in {'allowed', 'disabled'}:
        raise InputError(f'Autopilot must be allowed or disabled, got "{settings["Autopilot"]}"')
    checks = []
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if not line.strip().startswith('|') or len(cells) < 2:
            continue
        if cells[0].lower() == 'check' or set(''.join(cells)) <= set('-: '):
            continue
        checks.append((cells[0], '|'.join(cells[1:]).strip().strip('`')))
    names = [name for name, _ in checks]
    for name in names:
        if names.count(name) > 1:
            raise InputError(f'the wf block lists the check {name} more than once')
    return settings, checks


def authorization_reasons(value: str | None, where: str, allowed: set[str]) -> list[str]:
    """Reasons `<timestamp> — "<the human's words>"` is not an authorization for this task or wave.

    The words ask for the autopilot by name, so a request to continue is never one; when they name a
    task or wave (`wf auto TASK-7`), it must be one of `allowed`."""
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}\b[^"]*?) +[—-] +"(.*\w.*)"', (value or '').strip())
    if not match:
        return [f'{where} records no Autopilot authorization as <timestamp> — "<the human\'s words>"']
    if not ASKS_FOR_AUTOPILOT.search(match[2]):
        return [f'the Autopilot authorization in {where} ("{match[2]}") does not ask for wf auto or the '
                'autopilot']
    other = sorted(set(re.findall(r'\b(?:TASK|WAVE)-\d+\b', match[2])) - allowed)
    if other:
        return [f'the Autopilot authorization in {where} is for {", ".join(other)}, not this task']
    return []


def disabled(settings: dict[str, str]) -> list[str]:
    if settings.get('Autopilot') == 'disabled':
        return ['autopilot is disabled in the wf block of AGENTS.md (Autopilot: disabled)']
    return []


def one_file(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern))
    if len(found) > 1:
        raise InputError(f'more than one {pattern} in {directory}')
    return found[0] if found else None


def last_round(text: str) -> tuple[int, str] | str:
    """Number and section of the last `## Round <n>`, or why the rounds cannot be trusted."""
    body = clean(text)
    if re.search(r'^## Round\b(?! \d+\b)', body, flags=re.MULTILINE):
        return 'a Round heading has no number'
    rounds = [(int(m[1]), m[0]) for m in re.finditer(r'^## Round (\d+)\b[^\n]*(?:\n|\Z).*?(?=^## |\Z)',
                                                     body, flags=re.MULTILINE | re.DOTALL)]
    if not rounds:
        return 'no numbered review round to confirm the header'
    numbers = [number for number, _ in rounds]
    if len(set(numbers)) != len(numbers):
        return 'a round number appears more than once'
    if numbers[-1] != max(numbers):
        return 'the last round is not the highest-numbered one'
    return rounds[-1]


def review_reasons(path: Path, label: str, target: str, matches, validate) -> list[str]:
    """Reasons a review file does not approve `target`.

    `matches(reviewed)` compares a Reviewed value with the target, and `validate(section, number)` is the
    validator of a delegated round. The last round must be valid and the header must agree with it, so a
    header or a round edited by hand cannot approve anything."""
    if not path.exists():
        return [f'{label}: no review file']
    text = read(path)
    head = header(text, path)
    reasons = []
    if 'Latest verdict' in head and 'Verdict' in head:
        reasons.append(f'{label}: the header has both Latest verdict and Verdict')
    verdict = head.get('Latest verdict') or head.get('Verdict')
    if verdict not in APPROVED:
        reasons.append(f'{label}: latest verdict is "{verdict or "missing"}", not Approved')
    if head.get('Must-fix open', '').strip() != '0':
        reasons.append(f'{label}: Must-fix open is "{head.get("Must-fix open", "missing")}", not 0')
    reviewed = head.get('Reviewed', '').split(' (', 1)[0].strip()
    if not matches(reviewed):
        reasons.append(f'{label}: reviewed "{reviewed or "missing"}", not {target}')
    last = last_round(text)
    if isinstance(last, str):
        return reasons + [f'{label}: {last}']
    number, section = last
    try:
        validate(section, number)
    except ValueError as exc:
        reasons.append(f'{label}: round {number} is not a valid review round: {exc}')
    if head.get('Round', '').strip() != str(number):
        reasons.append(f'{label}: header Round "{head.get("Round", "missing")}" is not the last round {number}')
    for field, value in (('Verdict', verdict), ('Must-fix open', head.get('Must-fix open', '').strip()),
                         ('Reviewed', reviewed)):
        values = re.findall(rf'^- {re.escape(field)}: *(.*?) *$', section, flags=re.MULTILINE)
        if len(values) != 1:
            reasons.append(f'{label}: round {number} has {len(values)} {field} fields, not one')
            continue
        actual = values[0].split(' (', 1)[0].strip() if field == 'Reviewed' else values[0]
        if actual != value:
            reasons.append(f'{label}: round {number} says {field} "{actual or "missing"}", '
                           f'but the header says "{value or "missing"}"')
    return reasons


def plan_gate(task_dir: Path, settings: dict[str, str]) -> tuple[list[str], str]:
    if not task_dir.is_dir():
        raise InputError(f'{task_dir} is not a task directory')
    match = re.match(r'(TASK-\d+)', task_dir.name)
    if not match:
        raise InputError(f'{task_dir} is not a TASK-<N>-<slug> directory')
    task = match[1]
    plan_path = task_dir / f'{task}-plan.md'
    plan_text = read(plan_path)
    plan = header(plan_text, plan_path)
    reasons = disabled(settings)

    ticket = task_dir / f'{task}.md'
    ticket_text = read(ticket) if ticket.exists() else ''
    if ticket_text and header(ticket_text, ticket).get('Status') == 'Dropped':
        reasons.append(f'{task} is Dropped')
    # The latest authorization in the Notes counts; `wf auto TASK-<N>` adds a new one below the first.
    authorizations = [value for notes in sections(ticket_text, 'Notes')
                      for value in re.findall(r'^(?:- )?Autopilot authorization: *(.*?) *$', notes,
                                              flags=re.MULTILINE)]
    allowed = {task} | ({plan['Wave']} if re.fullmatch(r'WAVE-\d+', plan.get('Wave', '')) else set())
    reasons += authorization_reasons(authorizations[-1] if authorizations else None,
                                     f'the {task} ticket\'s Notes', allowed)
    if plan.get('Status') != 'Draft':
        reasons.append(f'plan Status is "{plan.get("Status", "missing")}", not Draft')
    revision = plan.get('Revision', '')
    if not revision.isdigit():
        raise InputError(f'{plan_path}: Revision must be a number, got "{revision}"')
    if not re.fullmatch(r'required|N/A\b.*\w.*', plan.get('Tests', '')):
        reasons.append('plan Tests must be "required" or "N/A" with a reason')
    decisions = sections(plan_text, 'Open decisions')
    if len(decisions) != 1 or not re.fullmatch(r'(?i)none\.?', decisions[0]):
        reasons.append('plan has open decisions (one Open decisions section that says "None")')

    target = f'plan revision {revision}'
    reasons += review_reasons(task_dir / f'{task}-plan-review.md', 'plan review', target,
                              lambda reviewed: reviewed == target,
                              lambda section, n: validate_review.validate(section, 'plan', n, target))
    return reasons, f'Gate 1 for {task} {target}'


def git(repo: Path, *args: str, check: bool = True, strip: bool = True) -> str | None:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                            encoding='utf-8', errors='surrogateescape')
    if result.returncode:
        if not check:
            return None
        raise InputError(f'git {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout.strip() if strip else result.stdout


def commit_named(repo: Path, sha: str) -> str | None:
    """The one commit that a full or abbreviated SHA names; None when it names none or several.

    Unlike `rev-parse <sha>`, a branch or tag that happens to be called like the SHA cannot stand in."""
    if not SHA.fullmatch(sha):
        return None
    objects = (git(repo, 'rev-parse', f'--disambiguate={sha}', check=False) or '').split()
    commits = [obj for obj in objects if git(repo, 'cat-file', '-t', obj, check=False) == 'commit']
    return commits[0] if len(commits) == 1 else None


def same_commit(repo: Path, reviewed: str, expected: str) -> bool:
    """`commit <sha>` whose SHA names exactly the commit `expected`."""
    match = REVIEWED_COMMIT.fullmatch(reviewed.strip())
    return bool(match) and commit_named(repo, match[1]) == expected


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return git(repo, 'merge-base', '--is-ancestor', older, newer, check=False) is not None


def tree(repo: Path, rev: str) -> dict[str, tuple[str, str]]:
    """Path -> (mode, object) of every file in `rev`, read NUL-separated so that no name is quoted."""
    entries = {}
    for item in git(repo, 'ls-tree', '-r', '-z', '--full-tree', rev, strip=False).split('\0'):
        if item:
            meta, path = item.split('\t', 1)
            mode, _, obj = meta.split(' ')
            entries[path] = (mode, obj)
    return entries


def command_words(command: str, depth: int = 3) -> set[str]:
    """Every word a shell could pass on from `command`, also inside quoted `sh -c` strings."""
    words = set(SHELL_SEPARATORS.split(command))
    try:
        quoted = shlex.split(command)
    except ValueError:
        quoted = []
    for word in quoted:
        words.add(word)
        words.update(part.strip() for part in re.split(r'[;&|()<>`=]+', word))
        if depth and word != command and re.search(r'[\s\'"]', word):
            words |= command_words(word, depth - 1)
    return words


def runnable(checks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The checks whose command runs something (not an N/A or planned row)."""
    return [(name, command) for name, command in checks if not re.match(r'(?i)(N/A|planned)\b', command)]


def shell_parts(command: str, depth: int = 3) -> list[str]:
    """What the shell itself reads in `command`: the text with quoted strings blanked out, and the quoted
    script of an `sh -c`-style call read the same way, since that shell runs it."""
    text, scripts = [], []
    for match in re.finditer(r"'([^']*)'|\"((?:[^\"\\]|\\.)*)\"|(.)", command, flags=re.DOTALL):
        if match[3] is not None:
            text.append(match[3])
            continue
        if depth and re.search(r'(?:^|\s)-c\s*$', ''.join(text)):
            scripts.append(match[1] if match[1] is not None else match[2])
        text.append(' ' * len(match[0]))
    return [''.join(text)] + [part for script in scripts for part in shell_parts(script, depth - 1)]


def special_entries(words: set[str], trees: list[dict[str, tuple[str, str]]]) -> list[str]:
    """Symbolic links and submodules on the way to, or at, a path the words name."""
    parts = [posixpath.normpath(word.removeprefix('./')).split('/') for word in words]
    prefixes = {'/'.join(part[:i]) for part in parts for i in range(1, len(part) + 1)}
    return sorted(f'the {"symbolic link" if mode == "120000" else "submodule"} {prefix}'
                  for prefix in prefixes for entries in trees
                  for mode in [entries.get(prefix, ('',))[0]] if mode in {'120000', '160000'})


def command_reasons(checks: list[tuple[str, str]], trees: list[dict[str, tuple[str, str]]]) -> list[str]:
    """Reasons the policy cannot tell which files a check command runs.

    It reads commands statically, so a change of directory or a shell glob where the shell reads it (not
    in a quoted argument), a `..` segment, or a path through a symbolic link or into a submodule (on the
    base or HEAD), also for a file the command names by file name alone, keeps Gate 2 with the human."""
    reasons = []
    for name, command in runnable(checks):
        words = {word for word in command_words(command) if word}
        shell = shell_parts(command)
        found = []
        if any(re.search(r'(?:^|[;&|(){}]|\b(?:then|do|else)\b)\s*(?:cd|pushd)\b', part) for part in shell):
            found.append('a change of directory')
        if any(re.search(r'[*?[]', part) for part in shell):
            found.append('a shell glob')
        if any('..' in word.split('/') for word in words):
            found.append('a ".." path segment')
        named = command_files([(name, command)], trees)
        found += sorted(set(special_entries(words | named, trees)))[:1]
        if found:
            reasons.append(f'check {name}: the command uses {", ".join(found)}, which the policy cannot follow, '
                           'so Gate 2 stays with the human')
    return reasons


def command_files(checks: list[tuple[str, str]], trees: list[dict[str, tuple[str, str]]]) -> set[str]:
    """Files in any of `trees` that a check command names by path, or by file name alone, since a command
    may reach a file in ways a policy cannot follow. This errs towards the human gate."""
    words = {posixpath.normpath(word.removeprefix('./')) for _, command in runnable(checks)
             for word in command_words(command) if word}
    names = {posixpath.basename(word) for word in words if word.strip('.')}
    return {path for entries in trees for path in entries if path in words or posixpath.basename(path) in names}


def protected_paths(paths: list[str], extra: list[str]) -> list[str]:
    patterns = [p.lower() for p in (*PROTECTED, *extra)]
    hits = []
    for path in paths:
        lowered = path.lower()
        name = lowered.rsplit('/', 1)[-1]
        if any(fnmatch.fnmatchcase(lowered, p) or ('/' not in p and fnmatch.fnmatchcase(name, p))
               for p in patterns):
            hits.append(path)
    return hits


RESULT_PASS = re.compile(r'PASS')
RESULT_NA = re.compile(r'N/A \([^)]*\w[^)]*\)')
RESULT_PRE_EXISTING = re.compile(r'FAIL \(pre-existing: [^)]*\w[^)]*\)')


def names_base(text: str, repo: Path, bases: set[str]) -> bool:
    """Whether `text` names one of the base commits by SHA. What the output shows is the wave review's
    to verify; the policy checks only what it can: that the check ran on the base."""
    return any(commit_named(repo, sha) in bases for sha in re.findall(r'\b[0-9a-f]{7,40}\b', text))


def checks_reasons(text: str, checks: list[tuple[str, str]], repo: Path, bases: set[str]) -> list[str]:
    reasons = [f'check {name} is planned, so it is NOT RUN (planned)'
               for name, command in checks if command.lower().startswith('planned')]
    found = sections(text, 'Full checks')
    if len(found) != 1:
        return reasons + ['the wave record needs exactly one Full checks section']
    rows: dict[str, list[tuple[str, str]]] = {}
    for line in found[0].splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if set(''.join(cells)) <= set('-: ') or cells[0].lower() == 'check':
            continue
        if len(cells) < 3:
            reasons.append(f'malformed Full checks row: {line.strip()}')
            continue
        rows.setdefault(cells[0], []).append((cells[1], '|'.join(cells[2:]).strip()))
    expected = [name for name, _ in checks]
    for name in expected:
        if name not in rows:
            reasons.append(f'check {name}: no result in the wave record')
    for name, results in rows.items():
        if len(results) > 1:
            reasons.append(f'check {name}: more than one result')
        if name not in expected:
            reasons.append(f'check {name}: not a check of the wf block')
        result, evidence = results[0]
        if RESULT_PASS.fullmatch(result) or RESULT_NA.fullmatch(result):
            continue
        if RESULT_PRE_EXISTING.fullmatch(result):
            if not evidence.strip(' —-'):
                reasons.append(f'check {name}: a pre-existing failure needs the base evidence')
            elif not names_base(f'{result} {evidence}', repo, bases):
                reasons.append(f'check {name}: the pre-existing failure does not name the base commit it ran on')
            continue
        reasons.append(f'check {name}: {result or "no result"}')
    if not any(RESULT_PASS.fullmatch(results[0][0]) for results in rows.values()):
        reasons.append('no check passed, so nothing has been proven')
    return reasons


def task_reasons(task: str, tasks_root: Path, repo: Path) -> list[str]:
    """Reasons the task's reviews do not approve the tip of its task branch."""
    task_dir = one_file(tasks_root, f'{task}-*') if tasks_root.is_dir() else None
    if task_dir is None or not task_dir.is_dir():
        return [f'{task}: no task directory']
    reasons = []
    plan_path = task_dir / f'{task}-plan.md'
    plan = header(read(plan_path), plan_path) if plan_path.exists() else {}
    # The plan's Sensitivity decides the security review, so only an approved current revision counts.
    # The approval must start with the revision it approves (workflow.md -> Human gates), and the plan
    # review must approve that revision; a plan whose open findings the human accepted keeps Gate 2 with
    # the human, since only the human accepts findings.
    revision = plan.get('Revision', '')
    approved = re.match(r'revision (\d+)\b', plan.get('Approved', ''))
    if plan.get('Status') != 'Approved' or not approved or approved[1] != revision:
        reasons.append(f'{task}: the plan is not approved as "Approved: revision {revision or "<n>"} …" '
                       f'(Status "{plan.get("Status", "missing")}", Approved "{plan.get("Approved", "missing")}")')
    target = f'plan revision {revision}'
    reasons += review_reasons(task_dir / f'{task}-plan-review.md', f'{task} plan review', target,
                              lambda reviewed: reviewed == target,
                              lambda section, n: validate_review.validate(section, 'plan', n, target))
    ticket = task_dir / f'{task}.md'
    ticket_branch = header(read(ticket), ticket).get('Branch', '') if ticket.exists() else ''
    summary = task_dir / f'{task}-impl-summary.md'
    fields = header(read(summary), summary) if summary.exists() else {}
    checkpoint = fields.get('Checkpoint', '')
    if not SHA.fullmatch(checkpoint):
        return reasons + [f'{task}: the implementation summary has no Checkpoint']
    full = commit_named(repo, checkpoint)
    if full is None:
        return reasons + [f'{task}: Checkpoint {checkpoint} does not name exactly one commit in this repository']
    task_branch = fields.get('Branch', '')
    if not task_branch.startswith(f'task/{task}-'):
        reasons.append(f'{task}: Branch "{task_branch or "missing"}" is not a task/{task}-<slug> branch')
    elif task_branch != ticket_branch:
        reasons.append(f'{task}: Branch "{task_branch}" is not the branch the ticket records '
                       f'("{ticket_branch or "missing"}")')
    elif git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{task_branch}', check=False) != full:
        reasons.append(f'{task}: Checkpoint {checkpoint[:12]} is not the tip of its branch "{task_branch}"')
    reviews = [('impl', f'{task}-impl-review.md', 'implementation')]
    security = task_dir / f'{task}-security-review.md'
    if plan.get('Sensitivity', '').strip() != 'normal' or security.exists():
        reviews.append(('security', security.name, 'security'))
    for kind, name, label in reviews:
        reasons += review_reasons(task_dir / name, f'{task} {label} review', f'commit {checkpoint[:12]}',
                                  lambda reviewed: same_commit(repo, reviewed, full),
                                  lambda section, n, kind=kind: validate_review.validate(
                                      section, kind, n, f'commit {full}'))
    return reasons


def wave_gate(record: Path, settings: dict[str, str], checks: list[tuple[str, str]], base: str,
              head: str, repo: Path) -> tuple[list[str], str]:
    text = read(record)
    wave = header(text, record)
    match = re.match(r'(WAVE-\d+)\.md$', record.name)
    if not match:
        raise InputError(f'{record} is not a WAVE-<N>.md record')
    wave_id = match[1]
    head_sha = git(repo, 'rev-parse', '--verify', f'{head}^{{commit}}')
    reasons = disabled(settings)

    # Resolve the base fully qualified, so a local branch called origin/<base> cannot stand in for it.
    branch = wave.get('Base branch', '')
    qualified = {branch: f'refs/heads/{branch}', f'origin/{branch}': f'refs/remotes/origin/{branch}'}
    if not branch or base not in qualified:
        reasons.append(f'--base {base} is not the wave record\'s Base branch "{branch or "missing"}"')
    base_sha = git(repo, 'rev-parse', '--verify', f'{qualified.get(base, base)}^{{commit}}')
    if settings.get('Base branch') and branch != settings['Base branch']:
        reasons.append(f'the wave Base branch "{branch}" is not the wf block\'s "{settings["Base branch"]}"')
    base_commit = commit_named(repo, wave.get('Base commit', ''))
    if base_commit is None:
        reasons.append(f'the wave record has no valid Base commit ("{wave.get("Base commit") or "missing"}")')
    elif not (is_ancestor(repo, base_commit, base_sha) and is_ancestor(repo, base_commit, head_sha)):
        reasons.append(f'the recorded Base commit {base_commit[:12]} is not an ancestor of {base} and HEAD')
    if not is_ancestor(repo, base_sha, head_sha):
        reasons.append(f'{base} is not merged into HEAD; sync with the base and review again')
    elif base_commit and is_ancestor(repo, base_commit, head_sha):
        # The wave's own commits are its first-parent chain since the recorded base. None of them may be
        # reachable from the base ref, or a moved base ref would hide them from the diff below.
        own = git(repo, 'rev-list', '--first-parent', f'{base_commit}..{head_sha}').splitlines()
        on_base = [sha for sha in own if is_ancestor(repo, sha, base_sha)]
        if on_base:
            reasons.append(f'{base} already contains wave commit {on_base[0][:12]}; the base ref does not '
                           'match the recorded wave')

    if wave.get('Status') not in {'In progress', 'Review requested'}:
        reasons.append(f'wave Status is "{wave.get("Status", "missing")}", not In progress or Review requested')
    reasons += review_reasons(record.with_name(f'{wave_id}-review.md'), 'wave review',
                              f'commit {head_sha[:12]}', lambda reviewed: same_commit(repo, reviewed, head_sha),
                              lambda section, n: validate_wave_review.validate(section, head_sha, n))

    tasks = wave.get('Tasks', '').strip()
    if re.fullmatch(r'TASK-\d+', tasks):
        reasons += task_reasons(tasks, record.parent.parent / 'tasks', repo)
    else:
        reasons.append(f'the autopilot covers a wave of exactly one task, but Tasks is "{tasks or "missing"}"')
    reasons += authorization_reasons(wave.get('Autopilot'), 'the wave record', {wave_id, tasks})

    reasons += checks_reasons(text, checks, repo, {base_sha, base_commit} - {None})

    extra = [p.strip() for p in settings.get('Autopilot paths', '').split(',') if p.strip()]
    # --no-renames lists both sides of a rename, so moving a protected file away is caught too; -z keeps
    # names with unusual characters unquoted.
    changed = [path for path in git(repo, 'diff', '--no-renames', '--name-only', '-z',
                                    f'{base_sha}..{head_sha}', strip=False).split('\0') if path]
    for path in protected_paths(changed, extra):
        reasons.append(f'the wave changes a protected path: {path}')
    trees = [tree(repo, base_sha), tree(repo, head_sha)]
    reasons += command_reasons(checks, trees)
    files = command_files(checks, trees)
    for path in changed:
        if any(entries.get(path, ('',))[0] == '120000' for entries in trees):
            reasons.append(f'the wave changes a symbolic link: {path}')
        elif any(entries.get(path, ('',))[0] == '160000' for entries in trees):
            reasons.append(f'the wave changes a submodule: {path}')
        elif path in files:
            reasons.append(f'the wave changes a file that a check command runs: {path}')
    if not changed:
        reasons.append(f'the wave has no changes against {base}')
    return reasons, f'Gate 2 for {wave_id} commit {head_sha[:12]}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='gate', required=True)
    plan = sub.add_parser('plan', help='Gate 1: an approved plan review of the current revision')
    plan.add_argument('--agents-md', type=Path, required=True)
    plan.add_argument('task_dir', type=Path)
    wave = sub.add_parser('wave', help='Gate 2: an approved integrated wave')
    wave.add_argument('--agents-md', type=Path, required=True)
    wave.add_argument('--base', required=True,
                      help="the wave record's Base branch, or origin/<it> after git fetch")
    wave.add_argument('--head', default='HEAD')
    wave.add_argument('--repo', type=Path, default=Path('.'))
    wave.add_argument('record', type=Path, help='development/waves/WAVE-<N>.md')
    args = parser.parse_args(argv)
    try:
        settings, checks = block(args.agents_md)
        if args.gate == 'plan':
            reasons, target = plan_gate(args.task_dir, settings)
        else:
            reasons, target = wave_gate(args.record, settings, checks, args.base, args.head, args.repo)
    except InputError as exc:
        print(f'auto-gate: {exc}', file=sys.stderr)
        return 2
    if reasons:
        print(f'not eligible: {target}')
        print('\n'.join(f'- {reason}' for reason in reasons))
        return 1
    print(f'eligible: {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
