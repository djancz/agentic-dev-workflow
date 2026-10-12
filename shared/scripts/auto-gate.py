#!/usr/bin/env python3
"""Decide whether the autopilot (wf auto) may pass a human gate without the human.

  auto-gate.py plan --agents-md AGENTS.md development/tasks/TASK-7-csv-export
  auto-gate.py wave --agents-md AGENTS.md --base origin/main --head HEAD development/waves/WAVE-3.md

The policy is deliberately narrow and reads only recorded facts. Gate 1 (plan) passes when the plan is
a Draft of Size S or M with Sensitivity normal, a Tests line and no open decisions, and its latest
review approved the current revision with no must-fix findings. Gate 2 (wave) passes when the wave
review approved the given HEAD with no must-fix findings, every task's implementation review (and
security review, if any) approved its current Checkpoint, the full checks table has at least one PASS
and otherwise only N/A or a proven `FAIL (pre-existing ...)`, and the wave diff against --base touches
no protected path (dependency manifests and lockfiles, migrations, CI, agent instructions, scanner
ignores, build and deploy files, or the globs of the block's `Autopilot paths`). A block line
`- Autopilot: disabled` refuses every gate.

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
SHA = re.compile(r'commit ([0-9a-f]{7,40})')
PROTECTED = (
    # dependencies
    'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock',
    'bun.lock', 'bun.lockb', 'requirements*.txt', 'constraints*.txt', 'pyproject.toml', 'setup.py',
    'setup.cfg', 'poetry.lock', 'uv.lock', 'Pipfile', 'Pipfile.lock', 'Cargo.toml', 'Cargo.lock',
    'go.mod', 'go.sum', 'Gemfile', 'Gemfile.lock', 'composer.json', 'composer.lock', 'pom.xml',
    'build.gradle', 'build.gradle.kts', 'settings.gradle*', 'gradle.lockfile', '*.csproj',
    'packages.lock.json', 'mix.exs', 'mix.lock',
    # database migrations
    '*/migrations/*', 'migrations/*', '*/migrate/*', 'alembic/*', '*/alembic/*',
    # CI and forge configuration
    '.github/*', '.gitlab-ci.yml', '.gitlab/*', '.circleci/*', '.buildkite/*', 'Jenkinsfile',
    'azure-pipelines.yml', '.woodpecker*', '.drone.yml',
    # agent instructions and installed skills
    'AGENTS.md', 'CLAUDE.md', 'GEMINI.md', '*/AGENTS.md', '*/CLAUDE.md', '*/GEMINI.md',
    '.claude/*', '.agents/*', '.gemini/*', '.opencode/*', '.codex/*',
    # scanner configuration and ignores, repository hygiene
    '.gitleaksignore', '.gitleaks.toml', '.semgrepignore', '.semgrep*', '.bandit', '.trivyignore',
    '.snyk', '.gitignore', '.gitattributes', '.pre-commit-config.yaml',
    # build, deploy and secrets
    'Dockerfile*', '*/Dockerfile*', 'docker-compose*', 'compose.y*ml', '.env*', '*/.env*',
)


class InputError(ValueError):
    """The records or arguments cannot be read; not a policy refusal."""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc.strerror or exc}') from exc


def header(text: str) -> dict[str, str]:
    """`Key: value` lines (with or without a leading `- `) before the first `## ` section."""
    head = re.split(r'^## ', text, maxsplit=1, flags=re.MULTILINE)[0]
    head = re.sub(r'<!--.*?-->', '', head, flags=re.DOTALL)
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^(?:- )?([A-Z][A-Za-z -]*?): *(.*?) *$', head, flags=re.MULTILINE):
        fields.setdefault(key, value)
    return fields


def section(text: str, title: str) -> str | None:
    match = re.search(rf'^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |\Z)', text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return None
    return re.sub(r'<!--.*?-->', '', match[1], flags=re.DOTALL).strip()


def same_commit(actual: str, expected: str) -> bool:
    a = SHA.match(actual.strip())
    if not a or not re.fullmatch(r'[0-9a-f]{7,40}', expected):
        return False
    return a[1].startswith(expected) or expected.startswith(a[1])


def placeholder(value: str | None) -> bool:
    return not value or bool(re.fullmatch(r'[<—–-].*', value.strip()))


def block(agents_md: Path) -> dict[str, str]:
    try:
        return resolve_roles.block_settings(read(agents_md))
    except InputError:
        raise
    except ValueError as exc:
        raise InputError(str(exc)) from exc


def disabled(settings: dict[str, str]) -> list[str]:
    if settings.get('Autopilot', 'allowed').strip().lower() == 'disabled':
        return ['autopilot is disabled in the wf block of AGENTS.md (Autopilot: disabled)']
    return []


def one_file(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern))
    if len(found) > 1:
        raise InputError(f'more than one {pattern} in {directory}')
    return found[0] if found else None


def review_reasons(path: Path | None, label: str, target: str, check) -> list[str]:
    """Reasons a review file does not approve `target`; `check(reviewed)` compares the Reviewed field."""
    if path is None:
        return [f'{label}: no review file']
    fields = header(read(path))
    reasons = []
    verdict = fields.get('Latest verdict') or fields.get('Verdict')
    if verdict not in APPROVED:
        reasons.append(f'{label}: latest verdict is "{verdict or "missing"}", not Approved')
    if fields.get('Must-fix open', '').strip() != '0':
        reasons.append(f'{label}: Must-fix open is "{fields.get("Must-fix open", "missing")}", not 0')
    reviewed = fields.get('Reviewed', '').split(' (', 1)[0].strip()
    if not check(reviewed):
        reasons.append(f'{label}: reviewed "{reviewed or "missing"}", not {target}')
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
    if plan.get('Size') not in {'S', 'M'}:
        reasons.append(f'plan Size is "{plan.get("Size", "missing")}"; the autopilot takes only S or M')
    if plan.get('Sensitivity') != 'normal':
        reasons.append(f'plan Sensitivity is "{plan.get("Sensitivity", "missing")}"; '
                       'the autopilot takes only normal')
    if placeholder(plan.get('Tests')):
        reasons.append('plan has no Tests line (required, or N/A with a reason)')
    decisions = section(plan_text, 'Open decisions')
    if decisions is None or not re.fullmatch(r'(?i)none\.?', decisions):
        reasons.append('plan has open decisions (the section must say "None")')

    target = f'plan revision {revision}'
    review = task_dir / f'{task}-plan-review.md'
    reasons += review_reasons(review if review.exists() else None, 'plan review', target,
                              lambda reviewed: reviewed == target)
    return reasons, f'Gate 1 for {task} {target}'


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise InputError(f'git {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout.strip()


def protected_paths(paths: list[str], extra: list[str]) -> list[str]:
    patterns = (*PROTECTED, *extra)
    hits = []
    for path in paths:
        name = path.rsplit('/', 1)[-1]
        if any(fnmatch.fnmatchcase(path, p) or ('/' not in p and fnmatch.fnmatchcase(name, p))
               for p in patterns):
            hits.append(path)
    return hits


def check_rows(text: str) -> list[tuple[str, str, str]] | None:
    body = section(text, 'Full checks')
    if body is None:
        return None
    rows = []
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 3 or not line.strip().startswith('|'):
            continue
        if cells[0].lower() == 'check' or set(''.join(cells)) <= set('-: '):
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def checks_reasons(rows: list[tuple[str, str, str]] | None) -> list[str]:
    if not rows:
        return ['the wave record has no Full checks results']
    reasons = []
    for check, result, evidence in rows:
        upper = result.upper()
        if upper.startswith('PASS') or upper.startswith('N/A'):
            continue
        if re.match(r'FAIL \(pre-existing\b', result, flags=re.IGNORECASE):
            if not evidence.strip():
                reasons.append(f'check {check}: a pre-existing failure needs the base evidence')
            continue
        reasons.append(f'check {check}: {result or "no result"}')
    if not any(result.upper().startswith('PASS') for _, result, _ in rows):
        reasons.append('no check passed, so nothing has been proven')
    return reasons


def wave_gate(record: Path, settings: dict[str, str], base: str, head: str,
              repo: Path) -> tuple[list[str], str]:
    text = read(record)
    wave = header(text)
    match = re.match(r'(WAVE-\d+)\.md$', record.name)
    if not match:
        raise InputError(f'{record} is not a WAVE-<N>.md record')
    wave_id = match[1]
    head_sha = git(repo, 'rev-parse', '--verify', f'{head}^{{commit}}')
    reasons = disabled(settings)

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
        task_dir = one_file(tasks_root, f'{task}-*')
        if task_dir is None or not task_dir.is_dir():
            reasons.append(f'{task}: no task directory')
            continue
        summary = task_dir / f'{task}-impl-summary.md'
        checkpoint = header(read(summary)).get('Checkpoint', '') if summary.exists() else ''
        if not re.fullmatch(r'[0-9a-f]{7,40}', checkpoint):
            reasons.append(f'{task}: the implementation summary has no Checkpoint')
            continue
        impl = task_dir / f'{task}-impl-review.md'
        reasons += review_reasons(impl if impl.exists() else None, f'{task} implementation review',
                                  f'commit {checkpoint[:12]}', lambda r, c=checkpoint: same_commit(r, c))
        security = task_dir / f'{task}-security-review.md'
        if security.exists():
            reasons += review_reasons(security, f'{task} security review', f'commit {checkpoint[:12]}',
                                      lambda r, c=checkpoint: same_commit(r, c))

    reasons += checks_reasons(check_rows(text))

    extra = [p.strip() for p in settings.get('Autopilot paths', '').split(',') if p.strip()]
    changed = git(repo, 'diff', '--name-only', f'{base}...{head_sha}').splitlines()
    for path in protected_paths(changed, extra):
        reasons.append(f'the wave changes a protected path: {path}')
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
    wave.add_argument('--base', required=True, help='base branch ref, e.g. origin/main')
    wave.add_argument('--head', default='HEAD')
    wave.add_argument('--repo', type=Path, default=Path('.'))
    wave.add_argument('record', type=Path, help='development/waves/WAVE-<N>.md')
    args = parser.parse_args(argv)
    try:
        settings = block(args.agents_md)
        if args.gate == 'plan':
            reasons, target = plan_gate(args.task_dir, settings)
        else:
            reasons, target = wave_gate(args.record, settings, args.base, args.head, args.repo)
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
