#!/usr/bin/env python3
"""Validate a delegated wave review before it can affect workflow state."""

import argparse
import re
from pathlib import Path


def method_is_described(text: str) -> bool:
    """The round has one *Method* section with real content, not only template placeholders."""
    sections = re.findall(r'^### Method[ \t]*\n(.*?)(?=^#{1,3} |\Z)', text, re.M | re.S)
    if len(sections) != 1:
        return False
    body = re.sub(r'<!--.*?-->', '', sections[0], flags=re.S)
    return any(line.strip() and not re.fullmatch(r'<[^>]*>', line.strip()) for line in body.splitlines())


def validate(text: str, revision: str, round_number: int) -> None:
    if not re.match(rf'## Round {round_number}(?:[ \t]|$)', text.strip(), re.M):
        raise ValueError('review round heading is missing or mismatched')
    reviewed = re.search(r'^- Reviewed: commit ([0-9a-f]{7,40})$', text, re.M)
    if not reviewed or not revision.startswith(reviewed.group(1)):
        raise ValueError('reviewed commit does not match wave HEAD')
    verdict = re.search(r'^- Verdict: (Approved|Changes requested)$', text, re.M)
    count = re.search(r'^- Must-fix open: (\d+)$', text, re.M)
    if not verdict or not count:
        raise ValueError('review verdict or must-fix count is missing')
    if verdict.group(1) == 'Approved' and int(count.group(1)):
        raise ValueError('approved review has unresolved must-fix findings')
    if verdict.group(1) == 'Changes requested' and not int(count.group(1)):
        raise ValueError('changes requested without a must-fix finding')
    if not method_is_described(text):
        raise ValueError('the round needs one ### Method section that says what was read and run')
    findings = re.findall(r'^- (?:Blocker|Major): .+', text, re.M)
    if len(findings) != int(count.group(1)):
        raise ValueError('must-fix count does not match severity-ranked findings')


def not_run(text: str) -> list[str]:
    """The checks the round's tables mark NOT RUN: the first cell of each row whose second cell starts so."""
    names: list[str] = []
    for line in re.sub(r'^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$', '', text, flags=re.M | re.S).splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) > 1 and cells[1].startswith('NOT RUN') and cells[0] not in names:
            names.append(cells[0])
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', required=True)
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.round < 1:
        parser.error('round must be positive')
    try:
        text = args.path.read_text(encoding='utf-8')
        validate(text, args.reviewed, args.round)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'invalid wave review: {exc}\n')
    unrun = not_run(text)
    if unrun:  # loop.md → Delegated runs: the orchestrator runs these before the verdict counts
        print('not run: ' + ', '.join(unrun))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
