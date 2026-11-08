#!/usr/bin/env python3
"""Name the next step of the wf workflow from the records in development/.

  next-step.py [--id TASK-N|WAVE-N|project] [--branch <current branch>] [--agents-md AGENTS.md] development

It reads record headers, the last `## Round` of each review file, the roadmap's two tables, and a task's
*Follow-ups* only when nothing is open. It never runs Git, never writes, and never guesses. Each open item
gets one line, `<item>: <action> — <reason>`. The last lines are `Next: <action>` and, when that step
runs on a branch other than --branch, `Where: <branch>`. An action is a `wf` command or a `human:`
answer or act (a gate, a decision on open findings, a merge, trying a prototype); an item line may also
say `waiting: <what it waits for>`, which is never the `Next:` line.

A review counts for the target its last round names (`Reviewed:`, else the header): the current plan,
spec or roadmap revision, or the current commit. A last round headed `— human` that requests changes
sends the item back to its loop. A wave review is current only while its last round is the last entry:
a `### Fix round` inside it or a `## Base sync` after it means the new HEAD still needs a review.

Project (development/project/, when prd.md exists)
- PRD `Draft`: the human approves or corrects it (it has no agent review). PRD approved, no spec:
  `wf spec`.
- Spec or roadmap `Draft`: without a review of its current revision, `wf spec` / `wf roadmap`; with
  `Changes requested` on it, the human decides on the open findings; approved, the human's gate.
- Spec approved, no roadmap: a prototype record that is `Scoping` waits for the human's answer to the
  scope, `Building` resumes with `wf prototype`, `Ready for feedback` waits for the human to try it;
  otherwise `wf roadmap`. Without a record, `wf prototype` is never named: it is optional.
- Roadmap approved: `wf roadmap` when the prototype is `Adopted` or `Reference` and the roadmap's
  `Prototype` line does not start with `revision <n>, adopt` or `reference`; `wf init` when --agents-md
  (default: AGENTS.md next to development/, in the main worktree) has no wf block; `wf roadmap` when the
  current milestone has no rows; `wf milestone M<n>` while one of its rows names no task;
  `wf milestone done M<n>` once all its tasks are `Done` or `Dropped` and its status does not start with
  `Done`. A `Current milestone` of `none` means the roadmap is finished.

Task (development/tasks/TASK-<N>-<slug>/, until `Done` or `Dropped`)
- No plan: `wf plan`. A plan is approved only with `Status: Approved` and `Approved: revision <its
  Revision> …`; `Approved` naming another revision is for the human to resolve. A Draft without a review
  of its current revision, or with a human change request, goes to `wf plan loop`; `Changes requested`
  by the reviewer waits for the human's decision, because a loop that stopped never restarts itself; an
  approving review is Gate 1.
- Plan approved: a wave with no record, or one still `Planned` without a branch: `wf wave start`, or it
  waits for an earlier wave the roadmap lists under *Depends on* that is not `Merged`; no `Branch`:
  `wf new-worktree`; `Tests: required` and no test report: `wf test`; no implementation summary, or one
  `In progress`: `wf impl`; `Checkpoint: pending`: the human commits the delegated run's changes.
- A plan revision approved after the tests or the implementation: a test report or summary whose
  `Plan: revision <n>` names an earlier revision belongs to that revision, so `wf test` (the test report)
  or `wf impl` (the summary) runs again, whatever the summary's status; the task is not ready. A record
  without a line of that form predates it and is not checked.
- Summary `Review requested`: without an implementation review of the Checkpoint, or with a human change
  request, `wf impl loop`; with the reviewer's `Changes requested`, the human decides; approved, and for
  `Sensitivity: high` without a security review of the Checkpoint, `wf sec`; all approved, `wf impl loop`
  marks it ready.
- Summary `Ready to integrate`: ready once its reviews cover the Checkpoint, whatever their verdict (the
  human accepted the open findings); otherwise `wf impl rev`, `wf sec`, or `wf impl fix` for a human
  change request. A ready task waits for its wave.

Wave (development/waves/WAVE-<N>.md, once started)
- While a task it lists that is not `Dropped` is not ready, it waits for those tasks.
- Otherwise, until `Approved`: the human's change request or a review that is not current is
  `wf wave integrate`; an approving review is Gate 2 at the commit it reviewed; `Changes requested` by
  the reviewer waits for the human's decision; no review yet is `wf wave integrate`.
- `Approved`: `wf finalize` (or `wf wave integrate` after a base sync). `PR opened`: the human merges the
  PR, then `wf wave done` (`wf finalize` handles review comments and failing CI).

Choosing: --id selects that item, else --branch (`task/TASK-<N>-…`, `wave/WAVE-<N>-…`, or
`prototype/…` for the project), else every item. An item that only waits stands for what it waits on,
and identical actions count once. One action left is the next step; several end with
`Next: choose one of …` and exit 1. When nothing can move by itself, the next step is the human's. A
selected item with nothing left falls back to every item. With nothing open, the next step is the
project's, or `human: start new work …`.

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
# Header keys the resolver reads; a record that sets one of them twice is ambiguous.
READ_KEYS = {'Status', 'Revision', 'Approved', 'Reviewed', 'Latest verdict', 'Verdict', 'Round', 'Checkpoint',
             'Tasks', 'Wave', 'Branch', 'Sensitivity', 'Tests', 'PR', 'Current milestone', 'Prototype', 'Plan'}
SHA = re.compile(r'[0-9a-f]{7,40}')
WORKTREE_COMMANDS = ('wf test ', 'wf impl ', 'wf sec ')
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
    branch: str = ''

    @property
    def waiting(self) -> bool:
        return self.action.startswith('waiting:')


# --------------------------------------------------------------------------- reading records


def uncomment(raw: str) -> str:
    """The text without HTML comments. A comment closed on its line goes wherever it is, outside inline
    code; one left open counts only when it starts a line, as the templates write it."""
    kept, open_comment = [], False
    for line in raw.splitlines():
        if open_comment:
            end = line.find('-->')
            if end < 0:
                continue
            line, open_comment = line[end + 3:], False
        out, pos = '', 0
        while True:
            start = line.find('<!--', pos)
            while start >= 0 and line[:start].count('`') % 2:
                start = line.find('<!--', start + 4)
            if start < 0:
                out += line[pos:]
                break
            end = line.find('-->', start + 4)
            if end < 0:
                if not line[:start].strip():
                    open_comment = True
                    out += line[pos:start]
                else:
                    out += line[pos:]
                break
            out, pos = out + line[pos:start], end + 3
        kept.append(out)
    return '\n'.join(kept)


def text_of(path: Path) -> str:
    """The record without HTML comments and fenced code blocks: templates and examples are not facts."""
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise InputError(f'cannot read {path}: {exc.strerror or exc}') from exc
    except UnicodeDecodeError as exc:
        raise InputError(f'cannot read {path}: not UTF-8') from exc
    kept, fence = [], ''
    for line in uncomment(raw).splitlines():
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


def fields_of(text: str, path: Path) -> dict[str, str]:
    """`Key: value` lines, with or without a leading `- `; a key the resolver reads may appear once."""
    fields: dict[str, str] = {}
    for key, value in re.findall(r'^(?:- )?([A-Z][A-Za-z -]*?):[ \t]*(.*?)[ \t]*$', text, flags=re.MULTILINE):
        if key in fields and key in READ_KEYS:
            raise InputError(f'{path}: {key} is set more than once')
        fields.setdefault(key, value)
    return fields


def header(path: Path) -> dict[str, str]:
    """The fields before the first `## ` section."""
    return fields_of(re.split(r'^## ', text_of(path), maxsplit=1, flags=re.MULTILINE)[0], path)


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


def plan_revision(fields: dict[str, str], path: Path, current: int) -> int | None:
    """The revision in a test report's or summary's `Plan: revision <n>`. None for a record without that form:
    one written before the line existed, perhaps with a free-text `Plan:` line its later updates did not change."""
    value = fields.get('Plan', '')
    if not value.startswith('revision '):
        return None
    match = re.match(r'revision (\d+)\b', value)
    if not match:
        raise InputError(f'{path}: Plan "{value}" does not name a plan revision')
    named = int(match[1])
    if named > current:
        raise InputError(f'{path}: Plan names revision {named}, but the plan is at revision {current}')
    return named


def token(value: str | None) -> str:
    words = (value or '').split()
    return words[0] if words else ''


def first_word(value: str | None) -> str:
    return token(value).lower()


def same_commit(a: str, b: str) -> bool:
    return bool(SHA.fullmatch(a) and SHA.fullmatch(b)) and (a.startswith(b) or b.startswith(a))


def blank(value: str | None) -> bool:
    return first_word(value) in {'', '—', '-', 'none', 'pending', 'n/a'}


def number(item_id: str) -> int:
    return int(item_id.split('-')[1])


def wave_of(ticket: dict[str, str]) -> str:
    named = re.search(r'WAVE-\d+', ticket.get('Wave', ''))
    return named[0] if named else ''


@dataclass
class Review:
    verdict: str
    reviewed: str
    round: str
    human: bool
    current: bool

    @property
    def human_changes(self) -> bool:
        return self.human and self.verdict == 'Changes requested'

    @property
    def approves(self) -> bool:
        return self.verdict != 'Changes requested'

    @property
    def commit(self) -> str:
        match = re.match(r'commit ([0-9a-fA-F]{7,40})\b', self.reviewed)
        return match[1].lower() if match else ''

    def names_revision(self, name: str, current: int) -> bool:
        return bool(re.match(rf'{name} revision {current}\b', self.reviewed))

    def label(self) -> str:
        return f'review round {self.round}' if self.round else 'review'


def review(path: Path) -> Review | None:
    """The review's last round, the header filling in what the round leaves out."""
    if not path.is_file():
        return None
    text = text_of(path)
    fields = header(path)
    verdict = fields.get('Latest verdict', fields.get('Verdict', ''))
    reviewed, round_no, human, current = fields.get('Reviewed', ''), fields.get('Round', ''), False, True
    rounds = list(re.finditer(r'^## Round (\d+)([^\n]*)\n(.*?)(?=^## |\Z)', text,
                              flags=re.MULTILINE | re.DOTALL))
    if rounds:
        last = rounds[-1]
        body = fields_of(last[3], path)
        verdict = body.get('Verdict', verdict)
        reviewed = body.get('Reviewed', reviewed)
        round_no, human = last[1], 'human' in last[2].lower()
        current = not re.search(r'^### Fix round', last[3], flags=re.MULTILINE) and not text[last.end():].strip()
    if verdict not in VERDICTS:
        raise InputError(f'{path}: the verdict "{verdict}" is not one of {", ".join(sorted(VERDICTS))}')
    return Review(verdict, reviewed, round_no if round_no.isdigit() else '', human, current)


# --------------------------------------------------------------------------- the project route


def document_step(name: str, command: str, path: Path, review_path: Path) -> tuple[str, str] | None:
    """The step for a spec or roadmap that is not approved; None when it is."""
    fields = header(path)
    if status(fields, 'document', path) == 'Approved':
        return None
    current = revision(fields, path)
    found = review(review_path)
    if not found or found.human_changes or not found.names_revision(name, current):
        return command, f'{name} revision {current} has no review of its own yet'
    if not found.approves:
        return (f'human: decide on the open findings of the {name} {found.label()}: fix them, accept them, '
                f'or change the approach',
                f'the review of {name} revision {current} requests changes and the loop stopped')
    return (f'human: approve the {name} revision {current} or ask for changes',
            f'its review approved revision {current}')


def project_step(dev: Path, agents_md: Path, tickets: dict[str, dict[str, str]],
                 tasks: dict[str, Path]) -> Item | None:
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
    proto_status = status(proto, 'prototype', prototype) if prototype.is_file() else ''
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
        reason = f'the spec is approved; the prototype is {proto_status}' if proto_status else \
            'the spec is approved (wf prototype before it is optional)'
        return Item('project', 'wf roadmap', reason)
    step = document_step('roadmap', 'wf roadmap', roadmap, project / 'roadmap-review.md')
    if step:
        return Item('project', *step)
    road = header(roadmap)
    if proto_status in {'Adopted', 'Reference'}:
        decision = 'adopt' if proto_status == 'Adopted' else 'reference'
        expected = f'revision {revision(proto, prototype)}, {decision}'
        if not re.match(rf'{re.escape(expected)}\b', road.get('Prototype', '').strip()):
            return Item('project', 'wf roadmap',
                        f'the prototype is {proto_status} and the roadmap does not plan "{expected}" yet')
    try:
        has_block = '<!-- wf:begin' in agents_md.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        has_block = False
    if not has_block:
        return Item('project', 'wf init', f'the roadmap is approved and {agents_md} has no wf block')
    return milestone_step(roadmap, road, tickets, tasks)


def milestone_step(roadmap: Path, road: dict[str, str], tickets: dict[str, dict[str, str]],
                   tasks: dict[str, Path]) -> Item | None:
    current = road.get('Current milestone', '').strip()
    if blank(current):
        return None
    milestones = {row.get('ID', ''): row for row in table(section(roadmap, 'Milestones'))}
    milestone_done = milestones.get(current, {}).get('Status', '').strip().startswith('Done')
    rows = [row for row in table(section(roadmap, 'Waves and tasks'))
            if row.get('Milestone', '').strip() == current]
    if not rows:
        return None if milestone_done else \
            Item('project', 'wf roadmap', f'milestone {current} has no tasks in the roadmap yet')
    if any(not re.search(r'TASK-\d+', row.get('Task', '')) for row in rows):
        return Item('project', f'wf milestone {current}', f'a row of milestone {current} has no task yet')
    named = [task for row in rows for task in re.findall(r'TASK-\d+', row.get('Task', ''))]
    for task in named:
        if task not in tasks:
            raise InputError(f'{roadmap}: milestone {current} names {task}, which has no task directory')
    if not milestone_done and all(tickets.get(task, {}).get('Status') in CLOSED for task in named):
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


def started(record: dict[str, str] | None) -> bool:
    return bool(record) and (record['Status'] != 'Planned' or not blank(record.get('Branch')))


# --------------------------------------------------------------------------- tasks


def decide(task: str, label: str, found: Review) -> str:
    return (f'human: decide on the open {label} findings of {task} ({found.label()}): fix them '
            f'(wf impl fix {task}, or wf impl loop {task} rounds=<n>), accept them, or change the approach')


def task_step(task: str, directory: Path, ticket: dict[str, str], waves: dict[str, dict[str, str]],
              deps: dict[str, set[str]]) -> tuple[Item | None, bool]:
    """The task's item (None when closed) and whether it is ready to integrate."""
    wave = wave_of(ticket)
    branch = '' if blank(ticket.get('Branch')) else token(ticket.get('Branch'))

    def item(action: str, reason: str, waits_for: list[str] | None = None) -> Item:
        where = branch if action.startswith(WORKTREE_COMMANDS) else ''
        return Item(task, action, reason, waits_for or [], wave, where)

    if ticket['Status'] in CLOSED:
        return None, ticket['Status'] == 'Done'
    files = {kind: directory / f'{task}-{kind}.md' for kind in
             ('plan', 'plan-review', 'test-report', 'impl-summary', 'impl-review', 'security-review')}
    if not files['plan'].is_file():
        return item(f'wf plan {task}', 'the task has no plan yet'), False
    plan = header(files['plan'])
    plan_status, current = status(plan, 'plan', files['plan']), revision(plan, files['plan'])
    sensitivity = first_word(plan.get('Sensitivity'))
    if sensitivity not in {'normal', 'high'}:
        raise InputError(f'{files["plan"]}: Sensitivity "{plan.get("Sensitivity", "")}" is not normal or high')
    if plan_status == 'Approved' and not re.match(rf'revision {current}\b', plan.get('Approved', '')):
        return item(f'human: the {task} plan is Approved but its approval does not name its revision {current}: '
                    f'set it back to Draft (wf plan fix does) or approve revision {current}',
                    'a new plan revision loses its approval'), False
    if plan_status == 'Draft':
        found = review(files['plan-review'])
        if found and found.human_changes:
            return item(f'wf plan loop {task}', 'the human asked for changes to the plan'), False
        if not found or not found.names_revision('plan', current):
            return item(f'wf plan loop {task}', f'plan revision {current} has no review yet'), False
        if not found.approves:
            return item(f'human: decide on the open plan findings of {task} ({found.label()}): fix them '
                        f'(wf plan fix {task}, or wf plan loop {task} rounds=<n>), accept them, change the '
                        f'approach, or wf task split',
                        f'the review of plan revision {current} requests changes and the loop stopped'), False
        return item(f'human: Gate 1 — approve the {task} plan revision {current} or ask for changes',
                    f'its review approved revision {current}'), False
    if not wave:
        recorded = ticket.get('Wave', '')
        return item(f'human: record the wave of {task} in its ticket',
                    f'the plan is approved but the ticket names no wave ("{recorded}")'), False
    record = waves.get(wave)
    if not started(record):
        open_deps = sorted((dep for dep in deps.get(wave, set()) if waves.get(dep, {}).get('Status') != 'Merged'),
                           key=number)
        if open_deps:
            return item(f'waiting: {", ".join(open_deps)} merged into the base first',
                        f'{wave} depends on {", ".join(open_deps)}', open_deps), False
        return item(f'wf wave start {wave}', f'the plan is approved and {wave} has not started'), False
    if record['Status'] == 'Merged':
        return item(f'wf wave done {wave}', f'{wave} is merged but {task} is not Done'), False
    if not branch:
        return item(f'wf new-worktree {task}', 'the plan is approved and the task has no branch yet'), False
    if not plan.get('Tests', 'required').strip().lower().startswith('n/a'):
        if not files['test-report'].is_file():
            return item(f'wf test {task}', 'the plan requires tests and no test report exists'), False
        tested = plan_revision(header(files['test-report']), files['test-report'], current)
        if tested is not None and tested < current:
            return item(f'wf test {task}', f'the test report covers plan revision {tested}, and revision {current} '
                                           f'is approved'), False
    if not files['impl-summary'].is_file():
        return item(f'wf impl {task}', 'the task has no implementation summary yet'), False
    summary = header(files['impl-summary'])
    summary_status = status(summary, 'summary', files['impl-summary'])
    implemented = plan_revision(summary, files['impl-summary'], current)
    if implemented is not None and implemented < current:
        return item(f'wf impl {task}', f'the implementation summary covers plan revision {implemented}, and '
                                       f'revision {current} is approved'), False
    checkpoint = first_word(summary.get('Checkpoint'))
    if summary_status == 'In progress':
        return item(f'wf impl {task}', 'the implementation summary is In progress'), False
    if checkpoint == 'pending':
        return item(f'human: commit the changes of the delegated run that the {task} summary lists and set its '
                    f'Checkpoint (loop.md → Delegated runs), then wf impl loop {task}',
                    'the summary has Checkpoint: pending'), False
    if not SHA.fullmatch(checkpoint):
        recorded = summary.get('Checkpoint', '')
        return item(f'wf impl {task}', f'the summary has no checkpoint commit ("{recorded}")'), False
    ready = summary_status == 'Ready to integrate'
    impl = review(files['impl-review'])
    sec = review(files['security-review']) if sensitivity == 'high' else None
    short = checkpoint[:7]

    def covers(found: Review | None) -> bool:
        return bool(found) and same_commit(found.commit, checkpoint)

    for label, found in (('implementation', impl), ('security', sec)):
        if found and found.human_changes:
            command = f'wf impl fix {task}' if ready else f'wf impl loop {task}'
            return item(command, f'the human asked for changes in the {label} review'), False
    if not covers(impl):
        command = f'wf impl rev {task}' if ready else f'wf impl loop {task}'
        return item(command, f'no implementation review of checkpoint {short} yet'), False
    if not ready and not impl.approves:
        return item(decide(task, 'implementation', impl),
                    f'the implementation review of {short} requests changes and the loop stopped'), False
    if sensitivity == 'high' and not covers(sec):
        return item(f'wf sec {task}', f'no security review of checkpoint {short} yet'), False
    if not ready and sec and not sec.approves:
        return item(decide(task, 'security', sec),
                    f'the security review of {short} requests changes and the loop stopped'), False
    if not ready:
        return item(f'wf impl loop {task}',
                    f'the reviews approved checkpoint {short}; the loop marks the task ready to integrate'), False
    return item(f'waiting: the integration of {wave}', f'ready to integrate at {short}', [wave]), True


# --------------------------------------------------------------------------- waves


def wave_step(wave: str, path: Path, record: dict[str, str], tasks: dict[str, Path],
              tickets: dict[str, dict[str, str]], ready: dict[str, bool]) -> Item | None:
    state = record['Status']
    if not started(record) or state == 'Merged':
        return None
    branch = '' if blank(record.get('Branch')) else token(record.get('Branch'))

    def item(action: str, reason: str, waits_for: list[str] | None = None) -> Item:
        where = branch if action.startswith(('wf wave integrate ', 'wf finalize ')) else ''
        return Item(wave, action, reason, waits_for or [], wave, where)

    listed = re.findall(r'TASK-\d+', record.get('Tasks', ''))
    if not listed:
        return item(f'human: list the tasks of {wave} in its record', 'its Tasks line names no task')
    for task in listed:
        if task not in tasks:
            raise InputError(f'{path}: Tasks names {task}, which has no task directory')
    if state == 'PR opened':
        pr = record.get('PR', '').strip()
        pr = f' ({pr})' if re.fullmatch(r'https?://\S+|[#!]\d+', pr) else ''
        return item(f'human: merge the PR of {wave}{pr}, then wf wave done {wave}',
                    f'the PR is open; wf finalize {wave} handles review comments and failing CI')
    found = review(path.with_name(f'{wave}-review.md'))
    if state == 'Approved':
        if found and not found.current:
            return item(f'wf wave integrate {wave}', 'a base sync after Gate 2 needs a new review and Gate 2')
        return item(f'wf finalize {wave}', 'Gate 2 approved the wave; it shows the PR and asks before pushing')
    live = [task for task in listed if tickets.get(task, {}).get('Status') != 'Dropped']
    if not live:
        return item(f'human: drop {wave} or add a task to it', 'every task of the wave is Dropped')
    missing = [task for task in live if not ready[task]]
    if missing:
        states = ', '.join(f'{task} ({tickets.get(task, {}).get("Status", "no ticket")})' for task in missing)
        return item(f'waiting: {states}', 'wf wave integrate needs every task of the wave ready', missing)
    if not found:
        return item(f'wf wave integrate {wave}', 'every task of the wave is ready to integrate')
    if found.human_changes:
        return item(f'wf wave integrate {wave}', 'the human asked for changes to the wave')
    if not found.current:
        return item(f'wf wave integrate {wave}', 'a fix or a base sync moved the wave after its last review')
    if not found.commit:
        return item(f'wf wave integrate {wave}', 'the last wave review names no commit')
    if not found.approves:
        return item(f'human: decide on the open findings of the {wave} {found.label()}: fix them '
                    f'(wf wave integrate {wave} rounds=<n>), accept them, or change the approach',
                    f'the review of commit {found.commit[:7]} requests changes and the loop stopped')
    return item(f'human: Gate 2 — approve {wave} at commit {found.commit[:7]} or ask for changes',
                f'the wave review approved commit {found.commit[:7]}')


def follow_ups(dev: Path, waves: dict[str, dict[str, str]], tasks: dict[str, Path]) -> list[str]:
    merged = [wave for wave, record in waves.items() if record['Status'] == 'Merged']
    if not merged:
        return []
    latest = max(merged, key=number)
    found = []
    for task in re.findall(r'TASK-\d+', waves[latest].get('Tasks', '')):
        if task not in tasks:
            continue
        for kind in ('impl-summary', 'plan'):
            path = tasks[task] / f'{task}-{kind}.md'
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
            [each for each in items if each.wave == target and each.id.startswith('TASK-') and each.id != item.id]
        for each in targets:
            out.extend(expand(each, by_id, items, seen))
    return out or [item]


def selected(branch: str | None) -> str | None:
    match = re.match(r'(?:task|wave)/((?:TASK|WAVE)-\d+)-', branch or '')
    if match:
        return match[1]
    return 'project' if (branch or '').startswith('prototype/') else None


def read_records(dev: Path) -> tuple[dict[str, Path], dict[str, dict[str, str]], dict[str, Path]]:
    tasks: dict[str, Path] = {}
    for directory in (dev / 'tasks').glob('TASK-*-*'):
        match = re.match(r'(TASK-\d+)-', directory.name)
        if match and directory.is_dir():
            if match[1] in tasks:
                names = sorted((tasks[match[1]].name, directory.name))
                raise InputError(f'{match[1]} has two task directories: {", ".join(names)}')
            tasks[match[1]] = directory
    waves: dict[str, dict[str, str]] = {}
    wave_paths: dict[str, Path] = {}
    for path in (dev / 'waves').glob('WAVE-*.md'):
        match = re.fullmatch(r'(WAVE-\d+)\.md', path.name)
        if not match:
            continue
        fields = {'Status': 'Planned'} if not text_of(path).strip() else header(path)
        status(fields, 'wave', path)
        waves[match[1]], wave_paths[match[1]] = fields, path
    task_order, wave_order = sorted(tasks, key=number), sorted(waves, key=number)
    return ({task: tasks[task] for task in task_order}, {wave: waves[wave] for wave in wave_order},
            {wave: wave_paths[wave] for wave in wave_order})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Name the next wf step from the records.')
    parser.add_argument('development', type=Path)
    parser.add_argument('--id', dest='item')
    parser.add_argument('--branch')
    parser.add_argument('--agents-md', type=Path)
    args = parser.parse_args(argv)
    dev: Path = args.development
    try:
        if args.item and not re.fullmatch(r'(?:TASK|WAVE)-\d+|project', args.item):
            raise InputError(f'--id must be TASK-<N>, WAVE-<N> or project, got "{args.item}"')
        if not dev.is_dir():
            raise InputError(f'{dev} is not a directory; run the command in the project, where install.py '
                             'created development/')
        tasks, waves, wave_paths = read_records(dev)
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
        known = {*tasks, *waves, *(wave_of(ticket) for ticket in tickets.values()), 'project'}
        if args.item and args.item not in known:
            raise InputError(f'{args.item} has no record in {dev}')
        project = project_step(dev, args.agents_md or dev.resolve().parent / 'AGENTS.md', tickets, tasks)
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
        nothing_open = not any(item.id.startswith(('TASK-', 'WAVE-')) for item in items)
        found = follow_ups(dev, waves, tasks) if nothing_open else []
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
            if each.waiting:
                what = each.action[len('waiting: '):]
                each = Item(each.id, f'human: unblock {each.id}, which waits for {what}', each.reason)
            if all(each.action != other.action for other in candidates):
                candidates.append(each)
    if found:
        print('Follow-ups: ' + ', '.join(found))
    if not candidates:
        print(f'Next: {START}')
        return 0
    if len(candidates) > 1:
        print('Next: choose one of ' + ', '.join(item.id for item in candidates))
        return 1
    print(f'Next: {candidates[0].action}')
    if candidates[0].branch and candidates[0].branch != (args.branch or ''):
        print(f'Where: {candidates[0].branch}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
