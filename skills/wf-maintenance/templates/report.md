# Maintenance report — <YYYY-MM-DD>

- Base: <branch> at <sha7>
- Task: <TASK-N for the routine updates, or "none">
- Updated: <YYYY-MM-DD HH:MM UTC>

## Summary

<Two to four lines: what is out of date, what is exploitable, and what this run does about it.>

## Dependencies

| Ecosystem | Package | Current | Latest in range | Latest | Kind | Advisory | Decision |
|---|---|---|---|---|---|---|---|
| | | | | | patch / minor / major | <id or —> | update now / proposed task <n> / no action: <reason> |

## Security scanners

| Check | Result | Evidence |
|---|---|---|
| secrets | PASS / FAIL / NOT RUN (<reason>) | <summary line; secret values redacted> |
| deps-audit | | |
| sast | | |

<Each High or Critical result: true positive (→ proposed task) or false positive with the reason.>

## Runtimes, images and CI

| Item | In use | Status | Evidence |
|---|---|---|---|
| | <version and where it is set> | supported until <date> / EOL <date> / newer major <v> | <command or source> |

## Proposed tasks

<One per concern. "None" is valid.>

| # | Task | Why | Created |
|---|---|---|---|
| 1 | <title for wf task> | <advisory, EOL date, breaking change> | <TASK-N or "no"> |

## Not checked

<Every scan that did not run, and why. "Nothing" only if true.>
