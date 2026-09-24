#!/usr/bin/env python3
"""Validate a delegated wave review before it can affect workflow state."""

import argparse
import re
from pathlib import Path


def validate(text: str, revision: str, round_number: int) -> None:
    if not text.strip().startswith(f'## Round {round_number}'):
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
    findings = re.findall(r'^- (?:Blocker|Major): .+', text, re.M)
    if len(findings) != int(count.group(1)):
        raise ValueError('must-fix count does not match severity-ranked findings')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviewed', required=True)
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.round < 1:
        parser.error('round must be positive')
    try:
        validate(args.path.read_text(encoding='utf-8'), args.reviewed, args.round)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'invalid wave review: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
