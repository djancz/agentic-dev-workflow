#!/usr/bin/env python3
"""Decide whether the autopilot (wf auto) may pass a human gate without the human.

  auto-gate.py plan --agents-md AGENTS.md development/tasks/TASK-7-csv-export
  auto-gate.py wave --agents-md AGENTS.md --base origin/main --head HEAD development/waves/WAVE-3.md

The policy is deliberately narrow and reads only recorded facts. Gate 1 (plan) passes when the plan is a
Draft with a Tests line and no open decisions, and its latest review approved the current revision with
no must-fix findings; any Size and Sensitivity qualify, because a high-sensitivity task gets its
security review in wf impl loop and Gate 2 requires that review approved. Gate 2 (wave) passes when the
wave review approved the given HEAD with no must-fix findings, every task's implementation review (and
security review, if any) approved its Checkpoint, which is the tip of the task branch, the full checks
table has exactly one result per check of the wf block, at least one PASS and otherwise only N/A or a
proven `FAIL (pre-existing: <evidence>)`, --base is the wave's base branch, is merged into HEAD and
contains none of the wave's own commits, and the wave diff (renames counted on both sides) touches no
protected path: dependency manifests and lockfiles, migrations, CI and repository configuration, agent
instructions, check, lint and scanner configuration, build and deploy files, files a check command
names, and the globs of the block's `Autopilot paths`. Every review header must agree with its last
round. A block line `- Autopilot: disabled` refuses every gate; any other value than allowed or disabled
is invalid.

Exit codes: 0 eligible, 1 not eligible (one reason per line), 2 invalid input.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('resolve_roles', HERE / 'resolve-roles.py')
resolve_roles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolve_roles)

APPROVED = {'Approved', 'Approved with comments'}
REVIEWED_COMMIT = re.compile(r'commit ([0-9a-f]{7,40})')
PROTECTED = (
    # dependencies
    'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock',
    'bun.lock', 'bun.lockb', '.npmrc', '.yarnrc*', 'requirements*', '*/requirements/*', 'constraints*.txt',
    'pyproject.toml', 'setup.py', 'setup.cfg', 'poetry.lock', 'uv.lock', 'pipfile', 'pipfile.lock',
    'cargo.toml', 'cargo.lock', 'go.mod', 'go.sum', 'go.work*', 'gemfile', 'gemfile.lock',
    'composer.json', 'composer.lock', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'settings.gradle*',
    'gradle.lockfile', '*.csproj', 'packages.lock.json', 'mix.exs', 'mix.lock', 'flake.nix', 'flake.lock',
    # database migrations
    '*/migrations/*', 'migrations/*', '*/migrate/*', 'alembic/*', '*/alembic/*',
    # CI, forge and repository configuration
    '.github/*', '*/.github/*', '.gitlab-ci.yml', '.gitlab/*', '.circleci/*', '.buildkite/*',
    'jenkinsfile', 'azure-pipelines.yml', '.woodpecker*', '.drone.yml', 'codeowners',
    '.gitignore', '.gitattributes', '.gitmodules', '.pre-commit-config.yaml',
    # agent instructions and installed skills
    'agents.md', 'claude.md', 'gemini.md', '.cursorrules', '.cursor/*', '.windsurfrules',
    '.claude/*', '.agents/*', '.gemini/*', '.opencode/*', '.codex/*',
    # check, lint, type and scanner configuration and ignores
    '.gitleaksignore', '.gitleaks.toml', '.semgrepignore', '.semgrep*', '.bandit', '.trivyignore',
    '.snyk', '.secrets.baseline', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.mypy.ini', '.flake8',
    '.pylintrc', 'pylintrc', 'pytest.ini', 'tox.ini', 'noxfile.py', '.coveragerc',
    '.eslintrc*', 'eslint.config.*', '.prettierrc*', 'prettier.config.*', 'biome.json*', 'tsconfig*.json',
    'jest.config.*', 'vitest.config.*', 'playwright.config.*', 'cypress.config.*', '.golangci*',
    'deny.toml', 'clippy.toml', 'rustfmt.toml', '.rustfmt.toml',
    # build, deploy and secrets
    'makefile', 'gnumakefile', 'justfile', 'taskfile.y*ml', 'dockerfile*', 'containerfile*',
    '.dockerignore', 'docker-compose*', 'compose.y*ml', '.env*',
)


class InputError(ValueError):
    """The records or arguments cannot be read; not a policy refusal."""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc.strerror or exc}') from exc


def uncomment(text: str) -> str:
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)


def header(text: str) -> dict[str, str]:
    """`Key: value` lines (with or without a leading `- `) before the first `## ` section."""
    head = re.split(r'^## ', uncomment(text), maxsplit=1, flags=re.MULTILINE)[0]
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^(?:- )?([A-Z][A-Za-z -]*?): *(.*?) *$', head, flags=re.MULTILINE):
        fields.setdefault(key, value)
    return fields


def sections(text: str, title: str) -> list[str]:
    return [body.strip() for body in re.findall(rf'^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |\Z)',
                                                uncomment(text), flags=re.MULTILINE | re.DOTALL)]


def same_commit(actual: str, expected: str) -> bool:
    """`commit <sha>` that names `expected` (full or abbreviated, at least 7 hex digits)."""
    match = REVIEWED_COMMIT.fullmatch(actual.strip())
    if not match or not re.fullmatch(r'[0-9a-f]{7,40}', expected):
        return False
    return match[1].startswith(expected) or expected.startswith(match[1])


def block(agents_md: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """The wf block's settings and its `| Check | Command |` rows."""
    text = read(agents_md)
    try:
        settings = resolve_roles.block_settings(text)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    body = uncomment(re.search(r'<!-- wf:begin.*?-->(.*?)<!-- wf:end -->', text, flags=re.DOTALL)[1])
    values = re.findall(r'^- Autopilot: *(.*?) *$', body, flags=re.MULTILINE)
    if len(values) > 1:
        raise InputError('the wf block sets Autopilot more than once')
    if values and values[0] not in {'allowed', 'disabled'}:
        raise InputError(f'Autopilot must be allowed or disabled, got "{values[0]}"')
    checks = []
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if not line.strip().startswith('|') or len(cells) < 2:
            continue
        if cells[0].lower() == 'check' or set(''.join(cells)) <= set('-: '):
            continue
        checks.append((cells[0], '|'.join(cells[1:]).strip().strip('`')))
    return settings, checks


def disabled(settings: dict[str, str]) -> list[str]:
    if settings.get('Autopilot') == 'disabled':
        return ['autopilot is disabled in the wf block of AGENTS.md (Autopilot: disabled)']
    return []


def one_file(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern))
    if len(found) > 1:
        raise InputError(f'more than one {pattern} in {directory}')
    return found[0] if found else None


def round_fields(text: str) -> tuple[int, dict[str, str]] | None:
    """Number and `- Key: value` fields of the last `## Round <n>` section."""
    rounds = re.findall(r'^## Round (\d+)\b[^\n]*\n(.*?)(?=^## |\Z)', uncomment(text),
                        flags=re.MULTILINE | re.DOTALL)
    if not rounds:
        return None
    number, body = rounds[-1]
    if int(number) != max(int(n) for n, _ in rounds):
        return None
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^- ([A-Z][A-Za-z -]*?): *(.*?) *$', body, flags=re.MULTILINE):
        fields.setdefault(key, value)
    return int(number), fields


def review_reasons(path: Path | None, label: str, target: str, check) -> list[str]:
    """Reasons a review file does not approve `target`; `check(reviewed)` compares the Reviewed field.

    The header must agree with the last round, so a header edited by hand cannot approve anything."""
    if path is None:
        return [f'{label}: no review file']
    text = read(path)
    head = header(text)
    reasons = []
    verdict = head.get('Latest verdict') or head.get('Verdict')
    if verdict not in APPROVED:
        reasons.append(f'{label}: latest verdict is "{verdict or "missing"}", not Approved')
    if head.get('Must-fix open', '').strip() != '0':
        reasons.append(f'{label}: Must-fix open is "{head.get("Must-fix open", "missing")}", not 0')
    reviewed = head.get('Reviewed', '').split(' (', 1)[0].strip()
    if not check(reviewed):
        reasons.append(f'{label}: reviewed "{reviewed or "missing"}", not {target}')
    last = round_fields(text)
    if last is None:
        return reasons + [f'{label}: no numbered review round to confirm the header']
    number, body = last
    if head.get('Round', '').strip() != str(number):
        reasons.append(f'{label}: header Round "{head.get("Round", "missing")}" is not the last round {number}')
    for field, value in (('Verdict', verdict), ('Must-fix open', head.get('Must-fix open', '').strip()),
                         ('Reviewed', reviewed)):
        actual = body.get(field, '').strip()
        if field == 'Reviewed':
            actual = actual.split(' (', 1)[0].strip()
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
    plan = header(plan_text)
    reasons = disabled(settings)

    ticket = task_dir / f'{task}.md'
    if ticket.exists() and header(read(ticket)).get('Status') == 'Dropped':
        reasons.append(f'{task} is Dropped')
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
    review = task_dir / f'{task}-plan-review.md'
    reasons += review_reasons(review if review.exists() else None, 'plan review', target,
                              lambda reviewed: reviewed == target)
    return reasons, f'Gate 1 for {task} {target}'


def git(repo: Path, *args: str, check: bool = True) -> str | None:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        if not check:
            return None
        raise InputError(f'git {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout.strip()


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return git(repo, 'merge-base', '--is-ancestor', older, newer, check=False) is not None


def command_files(checks: list[tuple[str, str]], repo: Path, base: str) -> list[str]:
    """Repository files that a check command names (scripts, manage.py, config passed as arguments)."""
    tracked = set(git(repo, 'ls-tree', '-r', '--name-only', base).splitlines())
    found = []
    for _, command in checks:
        for token in re.split(r'[\s;&|()<>`\'"=]+', command):
            token = token.removeprefix('./')
            if token in tracked:
                found.append(token)
    return found


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
RESULT_PRE_EXISTING = re.compile(r'FAIL \(pre-existing(?:: [^)]+)?\)')


def checks_reasons(text: str, checks: list[tuple[str, str]]) -> list[str]:
    found = sections(text, 'Full checks')
    if len(found) != 1:
        return ['the wave record needs exactly one Full checks section']
    rows: dict[str, list[tuple[str, str]]] = {}
    reasons = []
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
            continue
        reasons.append(f'check {name}: {result or "no result"}')
    if not any(RESULT_PASS.fullmatch(results[0][0]) for results in rows.values()):
        reasons.append('no check passed, so nothing has been proven')
    return reasons


def wave_gate(record: Path, settings: dict[str, str], checks: list[tuple[str, str]], base: str,
              head: str, repo: Path) -> tuple[list[str], str]:
    text = read(record)
    wave = header(text)
    match = re.match(r'(WAVE-\d+)\.md$', record.name)
    if not match:
        raise InputError(f'{record} is not a WAVE-<N>.md record')
    wave_id = match[1]
    head_sha = git(repo, 'rev-parse', '--verify', f'{head}^{{commit}}')
    base_sha = git(repo, 'rev-parse', '--verify', f'{base}^{{commit}}')
    reasons = disabled(settings)

    branch = wave.get('Base branch', '')
    if base not in {branch, f'origin/{branch}'} or not branch:
        reasons.append(f'--base {base} is not the wave record\'s Base branch "{branch or "missing"}"')
    if settings.get('Base branch') and branch != settings['Base branch']:
        reasons.append(f'the wave Base branch "{branch}" is not the wf block\'s "{settings["Base branch"]}"')
    base_commit = wave.get('Base commit', '')
    if not re.fullmatch(r'[0-9a-f]{7,40}', base_commit) or git(
            repo, 'rev-parse', '--verify', '--quiet', f'{base_commit}^{{commit}}', check=False) is None:
        reasons.append(f'the wave record has no valid Base commit ("{base_commit or "missing"}")')
    elif not (is_ancestor(repo, base_commit, base_sha) and is_ancestor(repo, base_commit, head_sha)):
        reasons.append(f'the recorded Base commit {base_commit} is not an ancestor of {base} and HEAD')
    if not is_ancestor(repo, base_sha, head_sha):
        reasons.append(f'{base} is not merged into HEAD; sync with the base and review again')
    elif re.fullmatch(r'[0-9a-f]{7,40}', base_commit) and is_ancestor(repo, base_commit, head_sha):
        # The wave's own commits are its first-parent chain since the recorded base. None of them may be
        # reachable from the base ref, or a moved base ref would hide them from the diff below.
        own = git(repo, 'rev-list', '--first-parent', f'{base_commit}..{head_sha}').splitlines()
        on_base = [sha for sha in own if is_ancestor(repo, sha, base_sha)]
        if on_base:
            reasons.append(f'{base} already contains wave commit {on_base[0][:12]}; the base ref does not '
                           'match the recorded wave')

    if wave.get('Status') not in {'In progress', 'Review requested'}:
        reasons.append(f'wave Status is "{wave.get("Status", "missing")}", not In progress or Review requested')
    review = record.with_name(f'{wave_id}-review.md')
    reasons += review_reasons(review if review.exists() else None, 'wave review', f'commit {head_sha[:12]}',
                              lambda reviewed: same_commit(reviewed, head_sha))

    tasks = re.findall(r'TASK-\d+', wave.get('Tasks', ''))
    if not tasks:
        reasons.append('the wave record lists no tasks')
    tasks_root = record.parent.parent / 'tasks'
    for task in tasks:
        task_dir = one_file(tasks_root, f'{task}-*') if tasks_root.is_dir() else None
        if task_dir is None or not task_dir.is_dir():
            reasons.append(f'{task}: no task directory')
            continue
        summary = task_dir / f'{task}-impl-summary.md'
        fields = header(read(summary)) if summary.exists() else {}
        checkpoint = fields.get('Checkpoint', '')
        if not re.fullmatch(r'[0-9a-f]{7,40}', checkpoint):
            reasons.append(f'{task}: the implementation summary has no Checkpoint')
            continue
        full = git(repo, 'rev-parse', '--verify', '--quiet', f'{checkpoint}^{{commit}}', check=False)
        if full is None:
            reasons.append(f'{task}: Checkpoint {checkpoint} is not a commit in this repository')
            continue
        task_branch = fields.get('Branch', '')
        tip = git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{task_branch}', check=False) \
            if task_branch else None
        if tip != full:
            reasons.append(f'{task}: Checkpoint {checkpoint[:12]} is not the tip of its branch '
                           f'"{task_branch or "missing"}"')
        impl = task_dir / f'{task}-impl-review.md'
        reasons += review_reasons(impl if impl.exists() else None, f'{task} implementation review',
                                  f'commit {checkpoint[:12]}', lambda r, c=full: same_commit(r, c))
        security = task_dir / f'{task}-security-review.md'
        if security.exists():
            reasons += review_reasons(security, f'{task} security review', f'commit {checkpoint[:12]}',
                                      lambda r, c=full: same_commit(r, c))

    reasons += checks_reasons(text, checks)

    extra = [p.strip() for p in settings.get('Autopilot paths', '').split(',') if p.strip()]
    # --no-renames lists both sides of a rename, so moving a protected file away is caught too.
    changed = git(repo, 'diff', '--no-renames', '--name-only', f'{base_sha}..{head_sha}').splitlines()
    for path in protected_paths(changed, extra):
        reasons.append(f'the wave changes a protected path: {path}')
    for path in sorted(set(changed) & set(command_files(checks, repo, base_sha))):
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
