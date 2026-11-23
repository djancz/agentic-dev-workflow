# Upstream: Cloudflare security-audit

`wf sec audit` adapts the `security-audit` skill of https://github.com/cloudflare/security-audit-skill,
commit `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8` (`skills/security-audit/`). To take a later upstream
change, diff that directory between this commit and the new one and apply it to the files below.

| Upstream file | Here | Changes |
|---|---|---|
| `ATTACK-CLASSES.md` and the ten companion files | `references/classes/` | none |
| `report-schema.json` | `scripts/report-schema.json` | none |
| `SKILL.md` (body) | `references/AUDIT.md` | see below |
| `RECONNAISSANCE.md`, `HUNTING.md`, `VALIDATION-AND-REPORTING.md` | `references/` | see below |
| `validate-findings.cjs`, `validate-coverage-ledger.cjs` and their tests | `scripts/validate-findings.py`, `scripts/validate-coverage-ledger.py`, `tests/test_sec_audit_validators.py` | ported to Python with the same checks, limits, messages and exit codes; `--id` added to the ledger validator |
| — | `scripts/audit.py`, `scripts/sandbox-run.py`, `SKILL.md` | new: the orchestration, the sandbox and the wf stage |

Changes to the upstream text and contract:

- Guidance mode is dropped: `wf sec audit` always runs the full audit, and `wf sec` serves focused
  reviews. The frontmatter and the operating modes of `SKILL.md` are replaced by the wf skill.
- The output directory is `development/audits/sec-audit-<YYYY-MM-DD>[-<n>]/`, inside the private,
  git-ignored records, instead of `~/security-audit-skill/<repo>/run-<N>`. Scratch directories are
  `runs/scratch/<agent-id>/` and artifacts `agents/<agent-id>/artifacts/`; `state.json` holds the
  parent's bookkeeping.
- Delegation runs through `audit.py prompt` and `run-agent.sh`, with the roles `hunt` (reconnaissance,
  hunters) and `verify` (critics, verifiers). Every run is a fresh headless session in this clone, so the
  agent CLIs load the project's agent configuration: the audit gate lists what the target commits. The
  tree check of `run-agent.sh` detects a write outside `runs/` after the run; Write isolation in
  `AUDIT.md` says what it does not cover. `run-agent.sh` drops one code fence around a whole message
  before `audit.py apply` reads it; anything else around the JSON object discards the result.
- A budget is always set (profile defaults 25, 60 and 150, at most 500) and every profile has a wave cap.
  A unit is reassigned at most twice, and a material Phase 5 replacement is re-verified at most twice;
  then the run follows the upstream incomplete path.
- The sandbox is `sandbox-run.py`. Target code writes only to a size-limited tmpfs; the evidence of a
  local check is its bounded stdout and stderr, which the trusted runner writes to the scratch directory.
  The source is a fresh export of the committed bytes for every check, so no attribute of the target
  applies and nothing left in a scratch directory stands in for the commit. A `--tool` mount refuses
  sockets, FIFOs and runtime directories such as `/run`, and the cgroup limits are read back inside the
  scope before the command starts.
- A unit the wave cap leaves unhunted is `deferred` with the reason `wave_cap_reached`, and `final`
  refuses `complete` while a cap or the budget deferred units. `audit.py done` ends a run once the Phase 6
  reports exist; until then it stays `in_progress`.
- The validators take Unicode properties from the running Python's `unicodedata`, as upstream's take them
  from Node's ICU: two runtimes with different Unicode versions can classify a newly assigned character
  differently.
- Promotion opens the scratch and artifacts roots when it starts, without following links, instead of
  before the agent run: `audit.py` is a separate process. The artifacts directories are hashed by
  `run-agent.sh`, so a run that touched one fails before anything is promoted.
- Reconnaissance agent 1d does not probe the host for the sandbox and promotion controls (its items 6 and
  7): `sandbox-run.py probe` answers them, and the agent names the toolchains a local check needs.
- The hunter result has one more field, `sandbox_probe`, and the candidate verifier result two,
  `checks` (its own source and local checks, recorded in the ledger under its agent ID) and
  `sandbox_probe`.

## License of the upstream files

```
MIT License

Copyright (c) 2025-2026 Cloudflare, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
