# TASK-<N> — Plan: <title>

- Status: Draft
- Revision: 1
- Size: <S | M | L>
- Sensitivity: <normal | high>
- Approved: —
- Wave: <WAVE-N>
- Tests: <required | N/A with reason>
- Updated: <YYYY-MM-DD HH:MM UTC>

## Goal

<One sentence: what will be true after this change that is not true now.>

## Context

<2–5 facts from the code that shape the approach, each with `path:line`. Existing code to reuse.>

## Reproduction and cause

<Only for Type: fix. The command or steps, observed vs expected result, and the cause at `path:line`.
Delete this section for other types.>

## Approach

<The design in a few sentences. Where there was a real choice, name the rejected alternative in one line
and say why. A reviewer must be able to disagree with a specific sentence.>

## Steps

<Ordered. Each step names files and leaves the repository working.>

1. `path/to/file` — <what changes>

## Test strategy

<This section is the contract the implementation is judged against.>

For a feature, `wf test` authors these tests in a fresh context before `wf impl`. The test author sees
the approved spec and public contract, not implementation code or author chat.

| Behaviour | Level | Test | Acceptance |
|---|---|---|---|
| <behaviour> | unit / integration / e2e | `tests/…::test_name` | <checkable criterion> |

Not covered automatically: <what, and how it will be checked instead — or "nothing">

## Security

Sensitivity: <normal | high> — <why, in one line>

<Answer only the questions from the security baseline that apply: entry points and trust boundaries,
validation, authorisation, secrets, new dependencies, abuse bounds. "No new entry points or dependencies"
is a valid answer for a normal change.>

## Docs to update

<README sections, docstrings, CHANGELOG — or "none">

## Commits

<Grouped by concern (conventions → Git): one commit per coherent change, in dependency order, each with
the steps it contains. A small, single-concern task has one.>

1. `<type>(<scope>): <subject>` — steps <n, …>

## Out of scope

<Binding for the whole task. Adjacent things that will not be touched.>

## Risks

| Risk | If it happens |
|---|---|
| | |

## Open decisions

<Each with a recommendation, phrased so the human can answer in one word. "None" is valid.>

1. **<decision>** — Options: <A> / <B>. Recommendation: <A>, because <reason>.

## Follow-ups

<Worth doing later, not in this task.>

## Revision history

<!-- wf plan fix appends one entry per revision: "Changes in revision <n>" bullets and a resolution table. -->
