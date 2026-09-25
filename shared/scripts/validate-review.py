#!/usr/bin/env python3
"""Validate one delegated wf review round before the orchestrator appends it."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERDICTS = {'Approved', 'Approved with comments', 'Changes requested'}
PREFIX = {'plan': 'P', 'impl': 'I', 'security': 'S', 'doc': 'D'}
FENCE = re.compile(r'^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$', flags=re.MULTILINE | re.DOTALL)
SHA = re.compile(r'commit ([0-9a-f]{7,40})')


def one(pattern: str, text: str, label: str) -> str:
    values = re.findall(pattern, text, flags=re.MULTILINE)
    if len(values) != 1:
        raise ValueError(f'expected exactly one {label}, found {len(values)}')
    return values[0].strip()


def same_target(actual: str, expected: str) -> bool:
    """Equal, or the same commit written as a full and an abbreviated SHA."""
    a, e = SHA.fullmatch(actual), SHA.fullmatch(expected)
    if a and e:
        return a[1].startswith(e[1]) or e[1].startswith(a[1])
    return actual == expected


def validate(text: str, kind: str, round_no: int, reviewed: str) -> tuple[str, int]:
    if not re.match(rf'^## Round {round_no} — .+\n', text):
        raise ValueError(f'output must start with "## Round {round_no} —"')
    # Code blocks inside findings (evidence, command output) are fine; ignore them for structure checks.
    text = FENCE.sub('', text)
    body = text.split('\n', 1)[1]
    if re.search(r'^#{1,2} ', body, flags=re.MULTILINE):
        raise ValueError('output must be exactly one round section, not a complete artifact')

    verdict = one(r'^- Verdict: (.+)$', text, 'Verdict field')
    if verdict not in VERDICTS:
        raise ValueError(f'unknown verdict: {verdict}')
    count_text = one(r'^- Must-fix open: (.+)$', text, 'Must-fix open field')
    if not count_text.isdigit():
        raise ValueError('Must-fix open must be a non-negative integer')
    must_fix = int(count_text)

    actual_reviewed = one(r'^- Reviewed: (.+)$', text, 'Reviewed field')
    actual_target = actual_reviewed.split(' (', 1)[0]
    if not same_target(actual_target, reviewed):
        raise ValueError(f'Reviewed must be "{reviewed}", got "{actual_target}"')

    prefix = PREFIX[kind]
    headings = re.findall(r'^### \[([PISHD])(\d+)-(\d+)\] .+$', text, flags=re.MULTILINE)
    if len(headings) != len(set(headings)):
        raise ValueError('finding IDs must be unique')
    for actual_prefix, actual_round, _ in headings:
        if actual_prefix != prefix or int(actual_round) != round_no:
            raise ValueError(f'finding IDs must use {prefix}{round_no}-<n>')

    finding_severities = re.findall(
        r'^### \[[PISHD]\d+-\d+\].*?^- Severity: (Blocker|Major|Minor|Nit)(?:\s|$)',
        text, flags=re.MULTILINE | re.DOTALL,
    )
    if len(finding_severities) != len(headings):
        raise ValueError('every finding heading must have one valid Severity')
    carried = len(re.findall(r'^\|\s*[PISHD]\d+-\d+\s*\|\s*not resolved\b[^|]*\|', text,
                             flags=re.MULTILINE | re.IGNORECASE))
    calculated = carried + sum(s in {'Blocker', 'Major'} for s in finding_severities)
    if calculated != must_fix:
        raise ValueError(f'Must-fix open is {must_fix}, but the round describes {calculated}')
    if (must_fix > 0) != (verdict == 'Changes requested'):
        raise ValueError('Changes requested must be used exactly when must-fix findings are open')
    if verdict == 'Approved' and any(s in {'Minor', 'Nit'} for s in finding_severities):
        raise ValueError('Approved cannot contain open Minor or Nit findings')
    return verdict, must_fix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=PREFIX, required=True)
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('--reviewed', required=True, help='exact target, e.g. "commit abc123"')
    parser.add_argument('file', type=Path)
    args = parser.parse_args(argv)
    if args.round < 1:
        parser.error('--round must be positive')
    try:
        verdict, must_fix = validate(args.file.read_text(encoding='utf-8'), args.kind,
                                     args.round, args.reviewed)
    except (OSError, ValueError) as exc:
        print(f'validate-review: {exc}', file=sys.stderr)
        return 2
    print(f'{args.kind} round {args.round}: {verdict}; {must_fix} must-fix')
    return 0


if __name__ == '__main__':
    sys.exit(main())
