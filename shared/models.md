# Model and effort per stage

Read this when a resolved model or effort is not `default` (`loop.md` → Arguments). `default` passes
nothing, so the selected agent uses its own configuration. Each role (`test`, `rev`, `sec`, `fix`) has its
own model and effort. An explicit argument overrides project defaults and `models=auto`.

## Applying a setting

- For a headless run, set `WF_AGENT_MODEL` and `WF_AGENT_EFFORT` for `scripts/run-agent.sh`. Never put
  credentials or private input in environment variables or command-line arguments.
- Use a native subagent only when it supports the requested settings. Otherwise use the **selected**
  agent's CLI. If neither can apply them, stop and report which setting was unavailable.
- Record the actual agent, model, effort or variant, and whether the run was a subagent or headless.
  `default` means no setting was passed. Do not claim a setting was applied if it was not.
- Gemini CLI has no portable effort flag. OpenCode maps `effort` to the provider-specific `--variant`
  flag; a rejected variant is a failed stage. A failed model or effort choice requires a new human
  selection, never a silent fallback.

## auto

Choose from the task's `Size`, `Sensitivity`, and current finding severity. Prefer the selected CLI's
configured default model unless a supported model is explicitly named in project settings. Do not make
network calls merely to enumerate models. Choose an effort only when the selected CLI supports it:

| Stage | Normal S | M or L, or open Major | High sensitivity or Blocker |
|---|---|---|---|
| Plan review | medium | high | xhigh |
| Implementation or wave review | medium | high | xhigh |
| Security review | high | high | xhigh |
| Fix | medium | medium | high |

For Gemini, keep effort `default`. For OpenCode, apply an effort only when that model's variant name is
known; otherwise keep `default`. A user-supplied agent, model, effort, or variant always wins over auto.
