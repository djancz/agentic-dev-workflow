# TASK-<N> — Plan: Routine maintenance <YYYY-MM-DD>

- Status: Draft
- Revision: 1
- Kind: maintenance
- Size: <S | M>
- Sensitivity: high
- Approved: —
- Wave: <WAVE-N>
- Tests: N/A — routine dependency update adds no feature behavior
- Updated: <YYYY-MM-DD HH:MM UTC>

## Goal

Update the dependencies below to their latest patch or minor versions and close the listed advisories,
without changing behaviour.

## Context

Report: `development/maintenance/<YYYY-MM-DD>.md`. <Anything that shapes the update: pins kept on purpose,
a lockfile format, a check that already fails on the base.>

## Updates

| Ecosystem | Package | From | To | Kind | Advisory closed |
|---|---|---|---|---|---|
| | | | | patch / minor | <id or —> |

## Not updated

| Package | Latest | Why not | Follow-up |
|---|---|---|---|
| | | major / needs code changes / pinned on purpose | proposed task <n> / none |

## Steps

1. `<manifest and lockfile>` — `<update command from the ecosystem reference>`
2. Run every row of the `wf` block.

## Test strategy

The change adds no feature behavior. The existing suite is the contract; independent test authoring is N/A.

| Behaviour | Level | Test | Acceptance |
|---|---|---|---|
| existing behaviour unchanged | all | every row of the `wf` block | same results as on the base |
| advisories closed | — | the `deps-audit` row | the advisories above are no longer reported |

Not covered automatically: <what the suite does not exercise that the updated packages touch, or "nothing">

## Security

Sensitivity: high — dependency changes.

<New or changed install scripts, packages that changed maintainers, changelogs that mention security
fixes.>

## Commits

<By concern, in English and Conventional Commits form, e.g. `chore(deps): update npm dependencies` and
`ci: update workflow actions`. One ecosystem is usually one commit.>

## Out of scope

Major upgrades and changes to the project's own code: they are the proposed tasks in the report.

## Revision history

<!-- A revision after notes at the maintenance gate appends "Changes in revision <n>" bullets. -->
