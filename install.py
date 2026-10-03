#!/usr/bin/env python3
"""
Install the wf-* skills of this repository into one project's agent discovery directories. Symlinks
mean a `git pull` here updates every installation. Also keep the private development/ records in the
main worktree (ignored through info/exclude) and link development/ in every other worktree to them, so
all worktrees of the clone share one set of records outside the protected .git directory.
`--migrate-state` moves records from the legacy <common-git-dir>/wf-state location there; a normal
install that finds the legacy location changes nothing and prints that command.

  project scope (--project)  claude  <project>/.claude/skills/<skill>
                             codex   <project>/.agents/skills/<skill>
                             gemini  <project>/.gemini/skills/<skill>
                             opencode <project>/.opencode/skills/<skill>
                             (the links are added to the repository's info/exclude, never committed)

Only skill symlinks whose raw target points into this repository's skills/ are changed or removed.
Uninstall preserves the private development/ records and their links.
Anything else under the same name, including an unowned dangling link, is a collision and stops the
install before any destination changes. `--uninstall-user` is a migration aid for old user-scope links.

Exit codes: 0 done (dry run: nothing to do), 1 dry run: changes pending, 2 error.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILLS_SRC = REPO / 'skills'
AGENTS = ('claude', 'codex', 'gemini', 'opencode')
NAME_RE = re.compile(r'^wf-[a-z0-9]+(-[a-z0-9]+)*$')
MAX_DESCRIPTION = 1024
EXCLUDE_MARK = '# wf skills (install.py)'


# --------------------------------------------------------------------------- frontmatter


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the top-level scalars of a SKILL.md frontmatter, including folded (>) and literal (|)
    block scalars. Enough for name/description; not a general YAML parser."""
    if not text.startswith('---\n'):
        raise ValueError('SKILL.md must start with a --- frontmatter line')
    end = text.find('\n---', 4)
    if end == -1:
        raise ValueError('frontmatter is not closed with ---')
    lines = text[4:end].splitlines()
    data: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = re.match(r'^([A-Za-z][\w-]*):\s*(.*)$', lines[i])
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value[:1] in ('>', '|') or value == '':
            block = []
            while i < len(lines) and (lines[i].startswith((' ', '\t')) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = '\n' if value.startswith('|') else ' '
            value = joiner.join(part for part in block if part).strip()
        else:
            value = value.strip('"\'')
        data[key] = value
    return data


def validate_skill(skill_dir: Path) -> str:
    """Return the skill name, or raise ValueError describing what is wrong."""
    skill_md = skill_dir / 'SKILL.md'
    if not skill_md.is_file():
        raise ValueError('SKILL.md not found')
    fm = parse_frontmatter(skill_md.read_text(encoding='utf-8'))
    name = fm.get('name', '')
    description = fm.get('description', '')
    if name != skill_dir.name:
        raise ValueError(f"frontmatter name '{name}' does not match directory '{skill_dir.name}'")
    if not NAME_RE.match(name):
        raise ValueError(f"name '{name}' must match {NAME_RE.pattern}")
    if not description:
        raise ValueError('frontmatter description is empty')
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f'description is {len(description)} characters (max {MAX_DESCRIPTION})')
    return name


def collect_skills() -> dict[str, Path]:
    skills: dict[str, Path] = {}
    errors = []
    for entry in sorted(SKILLS_SRC.iterdir()):
        if not entry.is_dir():
            continue
        try:
            skills[validate_skill(entry)] = entry
        except ValueError as exc:
            errors.append(f'  {entry.name}: {exc}')
    if errors:
        print('ERROR: invalid skills:', *errors, sep='\n', file=sys.stderr)
        sys.exit(2)
    return skills


# --------------------------------------------------------------------------- targets


def target_dirs(agents: list[str], project: Path | None) -> dict[str, Path]:
    home = Path.home()
    if project is not None:
        rel = {'claude': '.claude/skills', 'codex': '.agents/skills',
               'gemini': '.gemini/skills', 'opencode': '.opencode/skills'}
        return {a: project / rel[a] for a in agents}
    user = {
        'claude': Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude')) / 'skills',
        'codex': Path(os.environ.get('CODEX_HOME', home / '.codex')) / 'skills',
        'gemini': home / '.gemini' / 'skills',
        'opencode': home / '.config' / 'opencode' / 'skills',
    }
    return {a: user[a] for a in agents}


def points_into_repo(link: Path) -> bool:
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = link.parent / target
    return os.path.normpath(target).startswith(str(SKILLS_SRC) + os.sep)


def absolute_link_target(link: Path) -> Path:
    target = Path(os.readlink(link))
    return target if target.is_absolute() else link.parent / target


# --------------------------------------------------------------------------- actions


def plan_install(skills: dict[str, Path], dest: Path) -> tuple[list[tuple[str, Path, Path]], list[str]]:
    """Return (actions, collisions). An action is (verb, link, target)."""
    actions, collisions = [], []
    for name, src in skills.items():
        link = dest / name
        if link.is_symlink():
            current = Path(os.readlink(link))
            if os.path.normpath(absolute_link_target(link)) == os.path.normpath(src):
                continue
            if points_into_repo(link):
                actions.append(('update', link, src))
            else:
                collisions.append(f'{link} -> {current} (not from this repository)')
        elif link.exists():
            collisions.append(f'{link} exists and is not a symlink')
        else:
            actions.append(('create', link, src))
    return actions, collisions


def plan_prune(skills: dict[str, Path], dest: Path, uninstall: bool) -> list[tuple[str, Path, Path]]:
    """Return links owned by this repository that should be removed."""
    actions = []
    if not dest.is_dir():
        return actions
    for entry in sorted(dest.iterdir()):
        if not entry.is_symlink() or not entry.name.startswith('wf-'):
            continue
        ours = points_into_repo(entry)
        if ours and (uninstall or entry.name not in skills):
            actions.append(('remove', entry, Path(os.readlink(entry))))
    return actions


def apply(actions: list[tuple[str, Path, Path]]) -> None:
    for verb, link, target in actions:
        if verb in ('update', 'remove'):
            link.unlink()
        if verb in ('create', 'update'):
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)


def git_exclude_path(project: Path) -> Path:
    """Return the repository's exclude file (shared by its worktrees); ValueError if not a repo root."""
    try:
        top = subprocess.run(
            ['git', '-C', str(project), 'rev-parse', '--show-toplevel'],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        exclude = subprocess.run(
            ['git', '-C', str(project), 'rev-parse', '--git-path', 'info/exclude'],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f'not a git repository: {project}') from exc
    if Path(top).resolve() != project:
        raise ValueError(f'--project must name the repository root: {project} (root is {top})')
    path = Path(exclude)
    return path if path.is_absolute() else project / path


class LegacyState(Exception):
    """The records still live in the legacy <common-git-dir>/wf-state location."""


def git_output(project: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(project), *args],
                          check=True, capture_output=True, text=True).stdout


def worktrees(project: Path) -> list[tuple[Path, bool]]:
    """Return (path, is_bare) for every worktree of the repository, the main worktree first."""
    records = []
    for block in git_output(project, 'worktree', 'list', '--porcelain').split('\n\n'):
        lines = block.splitlines()
        if lines and lines[0].startswith('worktree '):
            records.append((Path(lines[0][len('worktree '):]), 'bare' in lines))
    return records


def main_worktree(project: Path) -> Path:
    """The main worktree holds the records; never guess when it cannot be determined."""
    records = worktrees(project)
    if not records or records[0][1]:
        raise ValueError('cannot determine the main worktree (bare repository); '
                         'rerun with --no-state and manage development/ by hand')
    main = records[0][0]
    if not main.is_dir():
        raise ValueError(f'main worktree {main} not found; rerun with --no-state')
    return main.resolve()


def legacy_state_dir(project: Path) -> Path:
    common = git_output(project, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
    return Path(common).resolve() / 'wf-state'


def links_to(link: Path, target: Path) -> bool:
    """True if link is a symlink that leads to target, even while target is missing."""
    return link.is_symlink() and os.path.realpath(link) == os.path.realpath(target)


def linked_worktrees(project: Path, main: Path) -> list[Path]:
    paths = [path.resolve() for path, bare in worktrees(project) if not bare and path.is_dir()]
    return [path for path in paths if path != main]


def is_tracked(main: Path) -> bool:
    return bool(git_output(main, 'ls-files', '--', 'development').strip())


def legacy_in_use(project: Path, main: Path, legacy: Path) -> bool:
    if legacy.exists() or legacy.is_symlink():
        return True
    return any(links_to(path / 'development', legacy) for path in [main, *linked_worktrees(project, main)])


def plan_worktree_link(project: Path, state: Path) -> list[tuple]:
    link = project / 'development'
    if links_to(link, state):
        return []
    if link.is_symlink():
        raise ValueError(f'{link} is a link to another location; move it manually')
    if link.exists():
        raise ValueError(f'{link} already exists; move its contents to {state} before installing')
    return [('link', link, Path(os.path.relpath(state, project)))]


def plan_state(project: Path, owned: bool, migrate_hint: str) -> list[tuple]:
    """Keep private records in the main worktree's development/ and link every other worktree to it.
    owned: the exclude file already ignores /development, so an existing directory there is ours."""
    main = main_worktree(project)
    legacy = legacy_state_dir(project)
    if legacy_in_use(project, main, legacy):
        raise LegacyState(
            f'the records still live in {legacy} inside the Git directory, where agents cannot write '
            f'without a prompt. Nothing was changed. Preview the migration with\n  {migrate_hint} --dry-run\n'
            'then run the same command without --dry-run.')
    state = main / 'development'
    actions: list[tuple] = []
    if state.is_symlink():
        raise ValueError(f'{state} is a link to another location; move it manually')
    if state.exists():
        if not state.is_dir() or is_tracked(main):
            raise ValueError(f'{state} is tracked by Git or not a directory; rename it before installing')
        if not owned:
            raise ValueError(f'{state} already exists and was not created by install.py; '
                             'move its contents away before installing')
    else:
        actions.append(('mkdir', state, None))
    if project != main:
        actions += plan_worktree_link(project, state)
    return actions


def plan_migration(project: Path, owned: bool) -> list[tuple]:
    """Move <common-git-dir>/wf-state to <main>/development with one rename and relink the worktrees.
    Every check runs before any change, nothing is ever overwritten, and a rerun resumes an
    interrupted migration."""
    main = main_worktree(project)
    legacy = legacy_state_dir(project)
    state = main / 'development'
    if not legacy_in_use(project, main, legacy):
        return plan_state(project, owned, '')
    if legacy.is_symlink() or (legacy.exists() and not legacy.is_dir()):
        raise ValueError(f'{legacy} is not a plain directory; migrate it by hand')
    moving = legacy.is_dir()
    actions: list[tuple] = []
    if links_to(state, legacy):
        if not moving:
            raise ValueError(f'{state} points to the missing {legacy}; restore the records first')
        actions.append(('unlink', state, None))
    elif state.is_symlink():
        raise ValueError(f'{state} is a link to another location; move it manually')
    elif state.exists():
        if moving:
            raise ValueError(f'both {legacy} and {state} exist; merge them by hand (nothing was changed)')
        if not state.is_dir():
            raise ValueError(f'{state} is not a directory')
    elif not moving:
        raise ValueError(f'worktrees link to the missing {legacy}; restore the records first')
    if moving:
        if is_tracked(main):
            raise ValueError(f'{state} is tracked by Git; rename it before migrating')
        if os.stat(legacy).st_dev != os.stat(main).st_dev:
            raise ValueError(f'{legacy} and {main} are on different filesystems; move the records by hand')
        actions.append(('rename', legacy, state))
    # Only links that lead to the legacy directory, absolute or relative, are re-pointed. A worktree
    # without development/ stays without it; anything else is reported and left alone.
    for path in linked_worktrees(project, main):
        link = path / 'development'
        if links_to(link, legacy):
            temp = path / '.development.wf-new'
            if temp.exists() and not temp.is_symlink():
                raise ValueError(f'{temp} exists and is not a leftover link; remove it by hand')
            actions.append(('relink', link, Path(os.path.relpath(state, path))))
        elif link.is_symlink() and not links_to(link, state):
            actions.append(('skip', link, Path(os.readlink(link))))
        elif link.exists() and not link.is_symlink():
            actions.append(('skip', link, None))
    return actions


def apply_state(actions: list[tuple], dry_run: bool) -> None:
    labels = {'mkdir': 'create directory', 'link': 'link', 'unlink': 'remove legacy link',
              'rename': 'move', 'relink': 'relink'}
    for verb, path, target in actions:
        # Checked after the exclude update and before any change to the records.
        if verb == 'rename' and not dry_run and subprocess.run(
                ['git', '-C', str(target.parent), 'check-ignore', '-q', 'development']).returncode != 0:
            raise ValueError(f'{target} would not be ignored by Git; fix the ignore rules first')
    for verb, path, target in actions:
        arrow = f' -> {target}' if target is not None else ''
        if verb == 'skip':
            print(f'Left alone (not a link to the legacy records): {path}{arrow}')
            continue
        print(f"{'Would ' + labels[verb] if dry_run else labels[verb].capitalize()}: {path}{arrow}")
        if dry_run:
            continue
        if verb == 'mkdir':
            path.mkdir(parents=True)
        elif verb == 'link':
            path.symlink_to(target, target_is_directory=True)
        elif verb == 'unlink':
            path.unlink()
        elif verb == 'rename':
            if target.exists() or target.is_symlink():
                raise ValueError(f'{target} appeared during the migration; nothing was moved')
            os.rename(path, target)
        elif verb == 'relink':
            temp = path.parent / '.development.wf-new'
            if temp.is_symlink():
                temp.unlink()
            temp.symlink_to(target, target_is_directory=True)
            os.replace(temp, path)


def plan_exclude(project: Path, dests: list[Path], uninstall: bool) -> tuple[Path, str] | None:
    """Return the exclude file and its new content, or None when no update is needed."""
    exclude = git_exclude_path(project)
    text = exclude.read_text(encoding='utf-8') if exclude.exists() else ''
    lines = text.splitlines()
    wanted = [f'/{d.relative_to(project).as_posix()}/wf-*' for d in dests]
    if not uninstall:
        wanted.append('/development')
    if uninstall:
        new = [line for line in lines if line not in wanted]
        all_patterns = {f'/{d.relative_to(project).as_posix()}/wf-*'
                        for d in target_dirs(list(AGENTS), project).values()}
        if not any(line in all_patterns for line in new):
            new = [line for line in new if line != EXCLUDE_MARK]
    else:
        missing = [w for w in wanted if w not in lines]
        if not missing:
            return None
        new = lines + ([EXCLUDE_MARK] if EXCLUDE_MARK not in lines else []) + missing
    if new == lines:
        return None
    return exclude, '\n'.join(new) + '\n'


def apply_exclude(change: tuple[Path, str], dry_run: bool) -> None:
    exclude, text = change
    print(f"{'Would update' if dry_run else 'Updated'} {exclude}")
    if not dry_run:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(text, encoding='utf-8')


# --------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--agents', help=f'comma-separated subset of {",".join(AGENTS)} '
                        '(default: those whose CLI is on PATH)')
    parser.add_argument('--project', type=Path, help='repository root to install into (required)')
    parser.add_argument('--dry-run', action='store_true', help='show what would change; exit 1 if anything would')
    parser.add_argument('--uninstall', action='store_true', help='remove the links this repository installed')
    parser.add_argument('--uninstall-user', action='store_true',
                        help='remove legacy user-scope links owned by this repository')
    parser.add_argument('--no-state', action='store_true',
                        help='do not create or link the private development/ records')
    parser.add_argument('--migrate-state', action='store_true',
                        help='move the records from the legacy <common-git-dir>/wf-state into the main '
                             'worktree and relink every worktree (combine with --dry-run to preview)')
    args = parser.parse_args(argv)
    if args.migrate_state and (args.uninstall or args.uninstall_user or args.no_state):
        parser.error('--migrate-state cannot be combined with --uninstall, --uninstall-user or --no-state')
    if args.uninstall_user and (args.project or args.uninstall):
        parser.error('--uninstall-user cannot be combined with --project or --uninstall')
    if not args.uninstall_user and args.project is None:
        parser.error('--project is required (skills are installed per project)')
    return args


def resolve_agents(value: str | None) -> list[str]:
    if value:
        agents = [a.strip() for a in value.split(',') if a.strip()]
        unknown = sorted(set(agents) - set(AGENTS))
        if unknown:
            sys.exit(f'ERROR: unknown agent(s): {", ".join(unknown)}')
        return agents
    found = [a for a in AGENTS if shutil.which(a)]
    if not found:
        sys.exit(f'ERROR: none of {", ".join(AGENTS)} is on PATH; choose with --agents')
    return found


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    agents = resolve_agents(args.agents)
    project = args.project.resolve() if args.project else None
    if project is not None and not project.is_dir():
        sys.exit(f'ERROR: project directory not found: {project}')
    skills = collect_skills()
    dests = target_dirs(agents, project)

    planned: dict[str, list[tuple[str, Path, Path]]] = {}
    all_collisions: list[str] = []
    for agent, dest in dests.items():
        actions = plan_prune(skills, dest, args.uninstall or args.uninstall_user)
        if not args.uninstall and not args.uninstall_user:
            install, collisions = plan_install(skills, dest)
            replaced = {link for _, link, _ in install}
            actions = [a for a in actions if a[1] not in replaced] + install
            all_collisions += collisions
        planned[agent] = actions

    try:
        exclude_change = plan_exclude(project, list(dests.values()), args.uninstall) if project else None
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

    state_actions: list[tuple] = []
    if project and not args.no_state and not args.uninstall:
        exclude = git_exclude_path(project)
        owned = exclude.is_file() and '/development' in exclude.read_text(encoding='utf-8').splitlines()
        try:
            if args.migrate_state:
                state_actions = plan_migration(project, owned)
            else:
                hint = (f'python3 {REPO / "install.py"} --project {main_worktree(project)} '
                        f'--agents {",".join(agents)} --migrate-state')
                state_actions = plan_state(project, owned, hint)
        except LegacyState as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2
        except (ValueError, subprocess.CalledProcessError) as exc:
            all_collisions.append(str(exc))

    if all_collisions:
        print('ERROR: collisions (resolve them by hand):', *all_collisions, sep='\n  ', file=sys.stderr)
        return 2

    changed = exclude_change is not None
    for agent, dest in dests.items():
        actions = planned[agent]
        for verb, link, target in actions:
            print(f"{'Would ' + verb if args.dry_run else verb.capitalize()}: {link} -> {target}")
        if actions:
            changed = True
            if not args.dry_run:
                apply(actions)
        print(f'{agent}: {dest} ({len(actions)} change(s))')

    if exclude_change is not None:
        apply_exclude(exclude_change, args.dry_run)
    if state_actions:
        changed = changed or any(verb != 'skip' for verb, _, _ in state_actions)
        try:
            apply_state(state_actions, args.dry_run)
        except (OSError, ValueError) as exc:
            print(f'ERROR: {exc}. Rerun the same command to resume; no record was deleted.', file=sys.stderr)
            return 2
    if args.dry_run:
        print('Dry run: changes pending.' if changed else 'Dry run: nothing to do.')
        return 1 if changed else 0
    if not changed:
        print('Everything up to date.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
