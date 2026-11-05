#!/usr/bin/env python3
"""Name the next step of the wf workflow from the record headers in development/.

  next-step.py [--id TASK-N|WAVE-N] [--branch <current branch>] [--agents-md AGENTS.md] development

It reads record headers, the roadmap's two tables, and a task's *Follow-ups* only when nothing is
open. It never runs Git, never writes, and never guesses. Each open item gets one line,
`<item>: <action> — <reason>`, and the last line is `Next: <action>`. An action is a `wf` command, a
`human:` answer or act (a gate, a decision on open findings, a merge, trying a prototype), or, on an
item line only, `waiting: <what it waits for>`. A review counts only for the target its `Reviewed`
line names: the current plan, spec or roadmap revision, or the current commit.

Project (development/project/, when prd.md exists)
- PRD `Draft`: the human approves or corrects it (it has no agent review). PRD approved, no spec:
  `wf spec`.
- Spec or roadmap `Draft`: without a review of its current revision, `wf spec` / `wf roadmap`; with
  `Changes requested` on it, the human decides on the open findings; approved, the human's gate.
- Spec approved, no roadmap: a prototype record that is `Scoping` waits for the human's answer to the
  scope, `Building` resumes with `wf prototype`, `Ready for feedback` waits for the human to try it;
  otherwise `wf roadmap`. Without a record, `wf prototype` is never named: it is optional.
- Roadmap approved: `wf roadmap` when the prototype is `Adopted` or `Reference` and the roadmap's
  `Prototype` line does not name `revision <n>, adopt` or `reference`; `wf init` when --agents-md has
  no wf block; `wf roadmap` when the current milestone has no rows; `wf milestone M<n>` while one of its
  rows names no task; `wf milestone done M<n>` once all its tasks are `Done` or `Dropped` and the
  milestone is not `Done`.

Task (development/tasks/TASK-<N>-<slug>/, until `Done` or `Dropped`)
- No plan: `wf plan`. A plan is approved only with `Status: Approved` and `Approved: revision <its
  Revision> …`. Otherwise: without a plan review of its current revision, `wf plan loop`; with
  `Changes requested` on it, the human decides, because a loop that stopped never restarts itself;
  approved, Gate 1.
- Plan approved: its wave not started (an empty reservation or `Planned`): `wf wave start`, or it waits
  for an earlier wave the roadmap lists under *Depends on* that is not `Merged`; no `Branch`:
  `wf new-worktree`; `Tests: required` and no test report: `wf test`; no implementation summary, or
  one `In progress`: `wf impl`.
- Summary with its `Checkpoint`: without an implementation review of that commit (and a security
  review, for `Sensitivity: high`), `wf impl loop`; with `Changes requested` on it, the human decides;
  all approved, `wf impl loop` until the summary is `Ready to integrate`; then it waits for its wave.

Wave (development/waves/WAVE-<N>.md)
- `In progress`: `wf wave integrate` only when every task it lists that is not `Dropped` is ready to
  integrate or `Done`; otherwise it waits for the tasks that are not.
- `Review requested`: Gate 2 when WAVE-<N>-review.md approved the record's `Reviewed commit`; the human
  decides on `Changes requested` for that commit; otherwise `wf wave integrate`.
- `Approved`: `wf finalize`. `PR opened`: the human merges the PR, then `wf wave done` (`wf finalize`
  handles review comments and failing CI).

Choosing: --id selects that item, else --branch (`task/TASK-<N>-…`, `wave/WAVE-<N>-…`, or
`prototype/…` for the project), else every item. An item that only waits stands for what it waits on,
and identical actions count once. One action left is the next step; several end with
`Next: choose one of …` and exit 1. A selected item with nothing left falls back to every item. With
nothing open, the next step is the project's, or `human: start new work …`.

Follow-ups are never an action. The tasks of the latest merged wave whose *Follow-ups* say more than
"None" are listed on a `Follow-ups:` line, and only when no task or wave is open.

Exit codes: 0 one next step, 1 several items could continue, 2 invalid records or arguments.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = {
    'ticket': {'Open', 'In progress', 'Ready to integrate', 'Done', 'Dropped'},
    'plan': {'Draft', 'Approved'},
    'summary': {'In progress', 'Review requested', 'Ready to integrate'},
    'wave': {'Planned', 'In progress', 'Review requested', 'Approved', 'PR opened', 'Merged'},
    'document': {'Draft', 'Approved'},
    'prototype': {'Scoping', 'Building', 'Ready for feedback', 'Adopted', 'Reference', 'Discarded'},
}
VERDICTS = {'Approved', 'Approved with comments', 'Changes requested'}
CLOSED = {'Done', 'Dropped'}
SHA = re.compile(r'[0-9a-f]{7,40}')
START = 'human: start new work with wf task <goal>, or wf prd <idea> for a new project or major initiative'


class InputError(ValueError):
    """The records or arguments cannot be read."""


@dataclass
class Item:
    id: str
    action: str
    reason: str
    waits_for: list[str] = field(default_factory=list)
    wave: str = ''

    @property
    def waiting(self) -> bool:
        return self.action.startswith('waiting:')


# --------------------------------------------------------------------------- reading records


def text_of(path: Path) -> str:
    """The record without HTML comments and fenced code blocks: templates and examples are not facts."""
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc.strerror or exc}') from exc
    except UnicodeDecodeError as exc:
        raise InputError(f'cannot read {path}: not UTF-8') from exc
    raw = re.sub(r'<!--.*?-->', '', raw, flags=re.DOTALL)
    kept, fence = [], ''
    for line in raw.splitlines():
        marker = re.match(r'[ \t]*(`{3,}|~{3,})', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) \
                    and not line.strip()[len(marker[1]):]:
                fence = ''
            continue
        if marker:
            fence = marker[1]
            continue
        kept.append(line)
    return '\n'.join(kept) + '\n'


def header(path: Path) -> dict[str, str]:
    """`Key: value` lines, with or without a leading `- `, before the first `## ` section."""
    head = re.split(r'^## ', text_of(path), maxsplit=1, flags=re.MULTILINE)[0]
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^(?:- )?([A-Z][A-Za-z -]*?):[ \t]*(.*?)[ \t]*$', head, flags=re.MULTILINE):
        if key in fields:
            raise InputError(f'{path}: the header sets {key} more than once')
        fields[key] = value
    return fields


def section(path: Path, title: str) -> str:
    match = re.search(rf'^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |\Z)', text_of(path),
                      flags=re.MULTILINE | re.DOTALL)
    return match[1].strip() if match else ''


def table(body: str) -> list[dict[str, str]]:
    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip('|').split('|')]

    lines = [line for line in body.splitlines() if line.strip().startswith('|')]
    if not lines:
        return []
    names, rows = cells(lines[0]), []
    for line in lines[1:]:
        values = cells(line)
        if all(set(value) <= set('-: ') for value in values):
            continue
        rows.append(dict(zip(names, values)))
    return rows


def status(fields: dict[str, str], kind: str, path: Path) -> str:
    value = fields.get('Status', '')
    if value not in STATUSES[kind]:
        raise InputError(f'{path}: Status "{value}" is not one of {", ".join(sorted(STATUSES[kind]))}')
    return value


def revision(fields: dict[str, str], path: Path) -> int:
    value = fields.get('Revision', '')
    if not value.isdigit():
        raise InputError(f'{path}: Revision "{value}" is not a number')
    return int(value)


def same_commit(a: str, b: str) -> bool:
    return bool(SHA.fullmatch(a) and SHA.fullmatch(b)) and (a.startswith(b) or b.startswith(a))


@dataclass
class Review:
    verdict: str
    reviewed: str
    round: str


def review(path: Path) -> Review | None:
    if not path.is_file():
        return None
    fields = header(path)
    verdict = fields.get('Latest verdict', fields.get('Verdict', ''))
    if verdict not in VERDICTS:
        raise InputError(f'{path}: the verdict "{verdict}" is not one of {", ".join(sorted(VERDICTS))}')
    return Review(verdict, fields.get('Reviewed', ''), fields.get('Round', '?'))


def reviewed_commit(found: Review | None) -> str:
    match = re.match(r'commit ([0-9a-f]{7,40})\b', found.reviewed) if found else None
    return match[1] if match else ''


def names_revision(found: Review | None, name: str, current: int) -> bool:
    return bool(found and re.match(rf'{name} revision {current}\b', found.reviewed))


def blank(value: str | None) -> bool:
    return (value or '').strip().lower() in {'', '—', '-', 'none', 'pending', 'n/a'}


# --------------------------------------------------------------------------- the project route


def document_step(name: str, command: str, path: Path, review_path: Path) -> tuple[str, str] | None:
    """The step for a spec or roadmap that is not approved; None when it is."""
    fields = header(path)
    if status(fields, 'document', path) == 'Approved':
        return None
    current = revision(fields, path)
    found = review(review_path)
    if not names_revision(found, name, current):
        return command, f'{name} revision {current} has no review yet'
    if found.verdict == 'Changes requested':
        return (f'human: decide on the open findings of the {name} review (round {found.round}): fix them, '
                f'accept them, or change the approach',
                f'the review of {name} revision {current} requests changes and the loop stopped')
    return (f'human: approve the {name} revision {current} or ask for changes',
            f'its review approved revision {current}')


def project_step(dev: Path, agents_md: Path, tickets: dict[str, dict[str, str]]) -> Item | None:
    project = dev / 'project'
    prd = project / 'prd.md'
    if not prd.is_file():
        return None
    fields = header(prd)
    if status(fields, 'document', prd) == 'Draft':
        current = revision(fields, prd)
        return Item('project', f'human: approve the PRD revision {current} or ask for changes',
                    'the PRD has no agent review; wf prd shows its digest')
    spec = project / 'spec.md'
    if not spec.is_file():
        return Item('project', 'wf spec', 'the PRD is approved and there is no spec')
    step = document_step('spec', 'wf spec', spec, project / 'spec-review.md')
    if step:
        return Item('project', *step)
    roadmap = project / 'roadmap.md'
    prototype = project / 'prototype.md'
    proto = header(prototype) if prototype.is_file() else {}
    proto_status = status(proto, 'prototype', prototype) if proto else ''
    if not roadmap.is_file():
        if proto_status == 'Scoping':
            return Item('project', 'human: answer the prototype scope question of wf prototype',
                        'the prototype record is Scoping')
        if proto_status == 'Building':
            return Item('project', 'wf prototype', 'the prototype is Building')
        if proto_status == 'Ready for feedback':
            return Item('project',
                        'human: try the prototype, then wf prototype <feedback> or wf prototype done',
                        'the prototype is Ready for feedback')
        reason = f'the spec is approved; the prototype is {proto_status}' if proto else \
            'the spec is approved (wf prototype before it is optional)'
        return Item('project', 'wf roadmap', reason)
    step = document_step('roadmap', 'wf roadmap', roadmap, project / 'roadmap-review.md')
    if step:
        return Item('project', *step)
    road = header(roadmap)
    if proto_status in {'Adopted', 'Reference'}:
        decision = 'adopt' if proto_status == 'Adopted' else 'reference'
        expected = f'revision {revision(proto, prototype)}, {decision}'
        if road.get('Prototype', '').strip() != expected:
            return Item('project', 'wf roadmap',
                        f'the prototype is {proto_status} and the roadmap does not plan "{expected}" yet')
    try:
        has_block = '<!-- wf:begin' in agents_md.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        has_block = False
    if not has_block:
        return Item('project', 'wf init', f'the roadmap is approved and {agents_md} has no wf block')
    return milestone_step(roadmap, road, tickets)


def milestone_step(roadmap: Path, road: dict[str, str], tickets: dict[str, dict[str, str]]) -> Item | None:
    current = road.get('Current milestone', '').strip()
    milestones = {row.get('ID', ''): row for row in table(section(roadmap, 'Milestones'))}
    milestone_done = milestones.get(current, {}).get('Status', '').strip() == 'Done'
    rows = [row for row in table(section(roadmap, 'Waves and tasks'))
            if row.get('Milestone', '').strip() == current]
    if not current or (not rows and milestone_done):
        return None
    if not rows:
        return Item('project', 'wf roadmap', f'milestone {current} has no tasks in the roadmap yet')
    if any(not re.search(r'TASK-\d+', row.get('Task', '')) for row in rows):
        return Item('project', f'wf milestone {current}', f'a row of milestone {current} has no task yet')
    tasks = [task for row in rows for task in re.findall(r'TASK-\d+', row.get('Task', ''))]
    for task in tasks:
        if task not in tickets:
            raise InputError(f'{roadmap}: milestone {current} names {task}, which has no ticket')
    if not milestone_done and all(tickets[task]['Status'] in CLOSED for task in tasks):
        return Item('project', f'wf milestone done {current}', f'every task of milestone {current} is closed')
    return None


def dependencies(dev: Path) -> dict[str, set[str]]:
    roadmap = dev / 'project' / 'roadmap.md'
    deps: dict[str, set[str]] = {}
    if roadmap.is_file():
        for row in table(section(roadmap, 'Waves and tasks')):
            for wave in re.findall(r'WAVE-\d+', row.get('Wave', '')):
                deps.setdefault(wave, set()).update(re.findall(r'WAVE-\d+', row.get('Depends on', '')))
    return deps


# --------------------------------------------------------------------------- tasks and waves


def task_step(task: str, directory: Path, ticket: dict[str, str], waves: dict[str, dict[str, str]],
              deps: dict[str, set[str]]) -> tuple[Item | None, bool]:
    """The task's item (None when closed) and whether it is ready to integrate."""
    named = re.search(r'WAVE-\d+', ticket.get('Wave', ''))
    wave = named[0] if named else ''

    def item(action: str, reason: str, waits_for: list[str] | None = None) -> Item:
        return Item(task, action, reason, waits_for or [], wave)

    if ticket['Status'] in CLOSED:
        return None, ticket['Status'] == 'Done'
    files = {kind: directory / f'{task}-{kind}.md' for kind in
             ('plan', 'plan-review', 'test-report', 'impl-summary', 'impl-review', 'security-review')}
    if not files['plan'].is_file():
        return item(f'wf plan {task}', 'the task has no plan yet'), False
    plan = header(files['plan'])
    plan_status, current = status(plan, 'plan', files['plan']), revision(plan, files['plan'])
    sensitivity = plan.get('Sensitivity', '')
    if sensitivity not in {'normal', 'high'}:
        raise InputError(f'{files["plan"]}: Sensitivity "{sensitivity}" is not normal or high')
    approved = plan_status == 'Approved' and re.match(rf'revision {current}\b', plan.get('Approved', ''))
    if not approved:
        found = review(files['plan-review'])
        stale = ' (its approval names another revision)' if plan_status == 'Approved' else ''
        if not names_revision(found, 'plan', current):
            return item(f'wf plan loop {task}', f'plan revision {current} has no review yet{stale}'), False
        if found.verdict == 'Changes requested':
            return item(f'human: decide on the open plan findings of {task} (review round {found.round}): fix '
                        f'them (wf plan fix {task}, or wf plan loop {task} rounds=<n>), accept them, change '
                        f'the approach, or wf task split',
                        f'the review of plan revision {current} requests changes and the loop stopped'), False
        return item(f'human: Gate 1 — approve the {task} plan revision {current} or ask for changes',
                    f'its review approved revision {current}'), False
    if wave not in waves:
        recorded = ticket.get('Wave', '')
        return item(f'human: record the wave of {task} in its ticket',
                    f'the plan is approved but the ticket names no wave record ("{recorded}")'), False
    wave_status = waves[wave]['Status']
    if wave_status == 'Planned':
        open_deps = sorted(dep for dep in deps.get(wave, set())
                           if waves.get(dep, {}).get('Status') != 'Merged')
        if open_deps:
            return item(f'waiting: {", ".join(open_deps)} merged into the base first',
                        f'{wave} depends on {", ".join(open_deps)}', open_deps), False
        return item(f'wf wave start {wave}', f'the plan is approved and {wave} has not started'), False
    if wave_status == 'Merged':
        return item(f'wf wave done {wave}', f'{wave} is merged but {task} is not Done'), False
    if blank(ticket.get('Branch')):
        return item(f'wf new-worktree {task}', 'the plan is approved and the task has no branch yet'), False
    if not plan.get('Tests', 'required').strip().startswith('N/A') and not files['test-report'].is_file():
        return item(f'wf test {task}', 'the plan requires tests and no test report exists'), False
    if not files['impl-summary'].is_file():
        return item(f'wf impl {task}', 'the task has no implementation summary yet'), False
    summary = header(files['impl-summary'])
    summary_status = status(summary, 'summary', files['impl-summary'])
    checkpoint = summary.get('Checkpoint', '')
    if summary_status == 'In progress' or not SHA.fullmatch(checkpoint):
        return item(f'wf impl {task}', f'the implementation summary is {summary_status} with Checkpoint '
                    f'"{checkpoint}"'), False
    kinds = [('implementation', 'impl-review')]
    if sensitivity == 'high':
        kinds.append(('security', 'security-review'))
    for label, kind in kinds:
        found = review(files[kind])
        if not same_commit(reviewed_commit(found), checkpoint):
            return item(f'wf impl loop {task}', f'no {label} review of checkpoint {checkpoint[:7]} yet'), False
        if found.verdict == 'Changes requested':
            return item(f'human: decide on the open {label} findings of {task} (review round {found.round}): '
                        f'fix them (wf impl fix {task}, or wf impl loop {task} rounds=<n>), accept them, or '
                        f'change the approach',
                        f'the {label} review of checkpoint {checkpoint[:7]} requests changes and the loop '
                        f'stopped'), False
    if summary_status != 'Ready to integrate':
        reason = f'the reviews approved checkpoint {checkpoint[:7]}; the loop marks the task ready to integrate'
        return item(f'wf impl loop {task}', reason), False
    return item(f'waiting: the integration of {wave}', f'ready to integrate at {checkpoint[:7]}', [wave]), True


def wave_step(wave: str, path: Path, record: dict[str, str], tasks: dict[str, Path],
              tickets: dict[str, dict[str, str]], ready: dict[str, bool]) -> Item | None:
    state = record['Status']
    if state in {'Planned', 'Merged'}:
        return None
    listed = re.findall(r'TASK-\d+', record.get('Tasks', ''))
    for task in listed:
        if task not in tasks:
            raise InputError(f'{path}: Tasks names {task}, which has no ticket')
    if state == 'In progress':
        live = [task for task in listed if tickets.get(task, {}).get('Status') != 'Dropped']
        missing = [task for task in live if not ready[task]]
        if not live:
            return Item(wave, f'human: drop {wave} or add a task to it', 'every task of the wave is Dropped')
        if missing:
            states = ', '.join(f'{task} ({tickets.get(task, {}).get("Status", "no ticket")})'
                               for task in missing)
            return Item(wave, f'waiting: {states}', 'wf wave integrate needs every task of the wave ready',
                        missing)
        return Item(wave, f'wf wave integrate {wave}', 'every task of the wave is ready to integrate')
    if state == 'Review requested':
        target = record.get('Reviewed commit', '')
        found = review(path.with_name(f'{wave}-review.md'))
        if found and SHA.fullmatch(target) and same_commit(reviewed_commit(found), target):
            if found.verdict == 'Changes requested':
                return Item(wave, f'human: decide on the open findings of the {wave} review (round '
                            f'{found.round}): fix them (wf wave integrate {wave} rounds=<n>), accept them, or '
                            f'change the approach',
                            f'the review of commit {target[:7]} requests changes and the loop stopped')
            return Item(wave, f'human: Gate 2 — approve {wave} at commit {target[:7]} or ask for changes',
                        f'the wave review approved commit {target[:7]}')
        return Item(wave, f'wf wave integrate {wave}', 'the integration or its review is not finished')
    if state == 'Approved':
        return Item(wave, f'wf finalize {wave}',
                    'Gate 2 approved the wave; it shows the PR and asks before pushing')
    pr = '' if blank(record.get('PR')) else f' ({record["PR"]})'
    return Item(wave, f'human: merge the PR of {wave}{pr}, then wf wave done {wave}',
                f'the PR is open; wf finalize {wave} handles review comments and failing CI')


def follow_ups(dev: Path, waves: dict[str, dict[str, str]], tasks: dict[str, Path]) -> list[str]:
    merged = [wave for wave, record in waves.items() if record['Status'] == 'Merged']
    if not merged:
        return []
    latest = max(merged, key=lambda wave: number((wave, None)))
    found = []
    for task in re.findall(r'TASK-\d+', waves[latest].get('Tasks', '')):
        for kind in ('impl-summary', 'plan'):
            path = tasks.get(task, dev) / f'{task}-{kind}.md'
            body = section(path, 'Follow-ups') if path.is_file() else ''
            if body and not re.fullmatch(r'(?i)none\.?|<[^>]*>', body):
                found.append(f'{task} ({path})')
    return found


# --------------------------------------------------------------------------- choosing


def expand(item: Item, by_id: dict[str, Item], items: list[Item], seen: set[str]) -> list[Item]:
    """What an item stands for: itself, or for a waiting item, what it waits on."""
    if not item.waiting:
        return [item]
    out: list[Item] = []
    for target in item.waits_for:
        if target in seen:
            continue
        seen.add(target)
        targets = [by_id[target]] if target in by_id else \
            [each for each in items if each.wave == target and each.id != item.id]
        for each in targets:
            out.extend(expand(each, by_id, items, seen))
    return out or [item]


def number(entry: tuple[str, object]) -> int:
    return int(entry[0].split('-')[1])


def selected(branch: str | None) -> str | None:
    match = re.match(r'(?:task|wave)/((?:TASK|WAVE)-\d+)-', branch or '')
    if match:
        return match[1]
    return 'project' if (branch or '').startswith('prototype/') else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Name the next wf step from the records.')
    parser.add_argument('development', type=Path)
    parser.add_argument('--id', dest='item')
    parser.add_argument('--branch')
    parser.add_argument('--agents-md', type=Path)
    args = parser.parse_args(argv)
    dev: Path = args.development
    try:
        if args.item and not re.fullmatch(r'(TASK|WAVE)-\d+', args.item):
            raise InputError(f'--id must be TASK-<N> or WAVE-<N>, got "{args.item}"')
        if not dev.is_dir():
            raise InputError(f'{dev} is not a directory; install.py creates development/')
        tasks: dict[str, Path] = {}
        for directory in (dev / 'tasks').glob('TASK-*-*'):
            match = re.match(r'(TASK-\d+)-', directory.name)
            if match and directory.is_dir():
                tasks[match[1]] = directory
        tasks = dict(sorted(tasks.items(), key=number))
        waves: dict[str, dict[str, str]] = {}
        wave_paths: dict[str, Path] = {}
        for path in (dev / 'waves').glob('WAVE-*.md'):
            match = re.fullmatch(r'(WAVE-\d+)\.md', path.name)
            if not match:
                continue
            fields = {'Status': 'Planned'} if not text_of(path).strip() else header(path)
            status(fields, 'wave', path)
            waves[match[1]], wave_paths[match[1]] = fields, path
        waves = dict(sorted(waves.items(), key=number))
        if args.item and args.item not in tasks and args.item not in waves:
            raise InputError(f'{args.item} has no record in {dev}')

        items: list[Item] = []
        tickets: dict[str, dict[str, str]] = {}
        unwritten = []
        for task, directory in tasks.items():
            path = directory / f'{task}.md'
            if not path.is_file():
                unwritten.append(task)
                continue
            tickets[task] = header(path)
            status(tickets[task], 'ticket', path)
        project = project_step(dev, args.agents_md or dev.parent / 'AGENTS.md', tickets)
        if project:
            items.append(project)
        deps = dependencies(dev)
        ready: dict[str, bool] = {}
        for task in tasks:
            if task in unwritten:
                items.append(Item(task, f'human: write the ticket of {task} with wf task, or remove its empty '
                                  'directory', f'{tasks[task]} has no {task}.md'))
                ready[task] = False
                continue
            item, ready[task] = task_step(task, tasks[task], tickets[task], waves, deps)
            if item:
                items.append(item)
        for wave, record in waves.items():
            item = wave_step(wave, wave_paths[wave], record, tasks, tickets, ready)
            if item:
                items.append(item)
    except InputError as exc:
        print(f'next-step: {exc}', file=sys.stderr)
        return 2

    for item in items:
        print(f'{item.id}: {item.action} — {item.reason}')
    by_id = {item.id: item for item in items}
    target = args.item or selected(args.branch)
    pool = items
    if target:
        if target in by_id:
            pool = [by_id[target]]
        elif any(item.wave == target for item in items):
            pool = [item for item in items if item.wave == target]
        else:
            print(f'{target}: nothing left to do')
    candidates: list[Item] = []
    for item in pool:
        for each in expand(item, by_id, items, {item.id}):
            if all(each.action != other.action for other in candidates):
                candidates.append(each)
    if not any(item.id.startswith(('TASK-', 'WAVE-')) for item in items):
        found = follow_ups(dev, waves, tasks)
        if found:
            print('Follow-ups: ' + ', '.join(found))
    if not candidates:
        print(f'Next: {START}')
        return 0
    if len(candidates) > 1:
        print('Next: choose one of ' + ', '.join(item.id for item in candidates))
        return 1
    print(f'Next: {candidates[0].action}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
