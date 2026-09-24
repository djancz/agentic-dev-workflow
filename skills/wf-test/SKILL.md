---
name: wf-test
description: >-
  Author contract-based feature or bug regression tests in a fresh context before implementation, using only the approved plan, spec, and public interface. Triggers: "wf test", "write the task tests".
---

# wf-test

Read `references/project.md`, `references/engineering.md`, and the approved task plan. Accept
`agent=self|claude|codex|gemini|opencode`, `model=`, and `effort=`; resolve them from project defaults
when omitted. Start a fresh native subagent or headless CLI run. Record the actual selection. If a
requested setting cannot be applied, stop and report it.
Read `references/models.md` for non-default model or effort choices.
For a headless run, write a short prompt under the task's `runs/` directory and call
`scripts/run-agent.sh <agent> rw <prompt> <out>`. Wait for its exit status and inspect the committed
tests and report before accepting the stage. A failed run is not completed test authoring.

The test author reads applicable PRD/spec sections when present, plus task acceptance criteria and the
public interface. It must not
read the implementation diff or the implementation author's conversation. Create tests for observable
behavior, including a relevant failure case. Existing code may be inspected only to discover test
conventions and public APIs. Keep the tests focused and deterministic.

Run the tests and record the expected red result for unimplemented behavior. A passing test is valid
only if it verifies a behavior already present; say so. Commit tests on the task branch as a checkpoint,
using explicit paths. Write `TASK-<N>-test-report.md` beside the task plan: agent/model/effort, contract
references, tests, command, result, and gaps. Next: `wf impl`. Do not implement the behavior or weaken
an acceptance criterion to make the test pass.
