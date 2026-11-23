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
  agent CLIs load the project's agent configuration (including a linked configuration root and Codex's
  `.agents/skills`): the audit gate lists it, and committed configuration needs the human's explicit
  `--trust-agent-config`. Each run is pinned to the audited commit. The
  tree check of `run-agent.sh` detects a write outside `runs/` after the run; Write isolation in
  `AUDIT.md` says what it does not cover. `run-agent.sh` drops one code fence that opens and closes
  around a whole message before `audit.py apply` reads it; anything else around the JSON object discards
  the result.
- A budget is always set (profile defaults 25, 60 and 150, at most 500) and every profile has a wave cap.
  A unit is reassigned at most twice, and a material Phase 5 replacement is re-verified at most twice;
  then the run follows the upstream incomplete path.
- The sandbox is `sandbox-run.py`. Target code writes only to size-limited tmpfs mounts; the evidence of a
  local check is its bounded stdout and stderr, which the trusted runner writes to the scratch directory.
  The source is a fresh export of the committed bytes for every check, so no attribute of the target
  applies and nothing left in a scratch directory stands in for the commit. The source, `--inputs` and
  every `--tool` directory are mounted by descriptor, so what was checked is what the command sees. A
  `--tool` mount refuses sockets, FIFOs, runtime directories such as `/run` and the sandbox's own mount
  points, `--inputs` refuses sockets and FIFOs, and the cgroup v2 limits are read back inside the scope
  before the command starts. The sandbox's `/etc` holds only the entries toolchains read (`ETC_ENTRIES`: of
  `ssl` and `pki` only the certificates and the OpenSSL configuration), where a host may keep user-readable
  credentials too, and of those only the ones that lead to `/etc` or `/usr` and that every user may read
  throughout, each mounted by the descriptor that was checked: one with a file only some users may read,
  such as a JMX password file, stays out with a note. Another entry is a `--tool /etc/<name>`, a file or a
  directory, under the same rules wherever it is asked for or leads in `/etc`; `passwd`, `group`, `hosts`,
  `hostname` and `machine-id` cannot be mounted. Its `passwd`, `group` and `hosts` name only the sandbox,
  as its host name does, and its root and `/dev` are read-only: only `/work` and `/dev/shm` take writes, each
  with its size. A Python launcher inside the finished sandbox starts the command with exactly its
  environment, which acts on nothing before, and reports the start only once the exec succeeded: a check
  whose command never started within its time limit, or whose exec failed (a missing interpreter, for
  example), leaves no record. No `--tool` may cover a credential store of the home directory, or the
  destination of one that is a link. The export is made only of directories it created and removed through
  its own descriptor and only in those; by name only an empty directory goes, so a directory moved into its
  place stays.
- A unit the wave cap leaves unhunted is `deferred` with the reason `wave_cap_reached`, and `final`
  refuses `complete` while a cap or the budget deferred units, while a proposed unit is neither seeded
  nor dismissed with a reason, or when no clean final-clean critic pass followed the latest hunted wave,
  archived attempts included; the final-clean critic of a wave follows its applied post-wave critic. A pass whose only work was proposals the parent rejected (out of scope, not
  applicable, a unit already there, or dismissed) counts as clean, as upstream's parent rejects them. `seed` resolves a proposal only with a unit of its file that has the
  proposal's labels and covers its starting paths. `audit.py done` ends a run once the Phase 6 reports
  exist; until then it stays `in_progress`, and a new `final` needs the reports of the last one removed.
- The validators take Unicode properties from the running Python's `unicodedata`, as upstream's take them
  from Node's ICU: two runtimes with different Unicode versions can classify a newly assigned character
  differently.
- Promotion opens the scratch and artifacts roots when it starts, from `/` without following a link on
  the way, instead of before the agent run: `audit.py` is a separate process. `sandbox-run.py run`
  opens its scratch directory the same way. The artifacts directories are hashed by
  `run-agent.sh`, so a run that touched one fails before anything is promoted. A destination that holds the
  same bytes already, from an apply that stopped after its promotion, counts as promoted; one with other
  bytes is never overwritten. The ledger, the state and the metadata of one save are written together
  through a journal, which the next command finishes when a save stopped halfway. The audit and every
  pinned run count a change inside a submodule, whatever its `ignore` setting, and refuse files the index
  hides from git status (assume-unchanged, skip-worktree), in every submodule checked out, active or not,
  and a submodule path that holds files but no repository.
- Reconnaissance agent 1d does not probe the host for the sandbox and promotion controls (its items 6 and
  7): `sandbox-run.py probe` answers them, and the agent names the toolchains a local check needs.
- The hunter result has one more field, `sandbox_probe`, and the candidate and `--reverify` verifier
  results two, `checks` (their own source and local checks, recorded in the ledger under their agent ID)
  and `sandbox_probe`. A `confirmed` verdict from either needs a local check of that verifier's own: the
  reading here of upstream's "complete local execution evidence" and "independently run any decisive
  check it can reproduce safely", since every hunter check ran in the same sandbox. That check cites the
  runner's record of it, whose promoted output must match it. A verifier whose probe failed settles
  nothing, also after a switch to source-only, and a source-only run confirms nothing.

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
