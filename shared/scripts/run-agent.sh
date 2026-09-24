#!/bin/sh
# run-agent.sh — run one wf stage in a fresh, headless agent session.
#
# Usage: run-agent.sh [--expect-clean] <claude|codex|gemini|opencode> <ro|rw> <prompt-file> <out-file>
#        run-agent.sh --wait <out-file> [seconds]
#
#   ro   read-only: the agent may read files and inspect git; it must not modify anything.
#        The working tree is always checked afterwards.
#   rw   the agent may edit files and run commands in the repository.
#   --expect-clean  with rw: fail with exit 3 if the working tree changed — for reviews that must
#        run the project's checks but must not touch the code.
#
# The agent's final message is written to <out-file>; its progress and errors go to <out-file>.log;
# the exit code goes to <out-file>.exit when the run ends.
#   --wait  block until the run writing <out-file> has finished (at most <seconds>, default 540), then
#        exit with its exit code; exit 124 if it is still running. For hosts whose shell tool cannot
#        wait 30 minutes: start the run in the background, then call --wait until it returns.
#
# Environment:
#   WF_AGENT_TIMEOUT           seconds before the run is killed (default 1800)
#   WF_AGENT_MODEL             model passed to the agent CLI (default: the CLI's own default)
#   WF_AGENT_EFFORT            effort level, lowercase (claude: --effort; codex: model_reasoning_effort;
#                              gemini has none and refuses it). Default: the CLI's own setting
#   WF_CLAUDE_PERMISSION_MODE  claude permission mode for rw (default acceptEdits)
#   WF_GEMINI_SANDBOX=1        run gemini with --sandbox; required for headless rw yolo mode
#   WF_AGENT_DRY_RUN=1         print the agent command line and exit 0
#
# Exit codes: 0 ok, 2 usage, 3 working tree changed, 4 required executable not found,
#             5 agent failed, timed out (124) or produced no output
set -eu

usage() {
    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

die() {
    code=$1
    shift
    echo "run-agent: $*" >&2
    exit "$code"
}

abspath() {
    case $1 in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$(pwd)" "$1" ;;
    esac
}

is_uint() {
    case ${1:-} in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac
}

if [ "${1:-}" = "--wait" ]; then
    [ $# -ge 2 ] && [ $# -le 3 ] || usage
    out=$(abspath "$2")
    limit=${3:-540}
    is_uint "$limit" || die 2 "wait seconds must be a non-negative integer: $limit"
    waited=0
    while [ ! -f "$out.exit" ]; do
        if [ "$waited" -ge "$limit" ]; then
            echo "run-agent: still running after ${limit}s; call --wait again" >&2
            exit 124
        fi
        sleep 5
        waited=$((waited + 5))
    done
    code=$(cat "$out.exit")
    echo "run-agent: finished with exit $code — $out" >&2
    exit "$code"
fi

expect_clean=0
if [ "${1:-}" = "--expect-clean" ]; then
    expect_clean=1
    shift
fi
[ $# -eq 4 ] || usage
agent=$1
mode=$2
prompt=$(abspath "$3")
out=$(abspath "$4")
log="$out.log"
[ "$prompt" != "$out" ] && [ "$prompt" != "$log" ] || die 2 "prompt and output paths must differ"
[ ! -L "$out" ] && [ ! -L "$log" ] && [ ! -L "$out.tmp" ] && [ ! -L "$out.exit" ] ||
    die 2 "output paths must not be symlinks"
rm -f "$out.exit"
trap 'echo $? >"$out.exit"' EXIT

case $mode in
    ro) expect_clean=1 ;;
    rw) ;;
    *) usage ;;
esac
[ -r "$prompt" ] || die 2 "prompt file not readable: $prompt"

root=$(git rev-parse --show-toplevel 2>/dev/null) || die 2 "not inside a git repository"
cd "$root"
for output_path in "$out" "$log" "$out.tmp" "$out.exit"; do
    git ls-files --error-unmatch -- "$output_path" >/dev/null 2>&1 &&
        die 2 "output path is tracked: $output_path"
    # check-ignore: 0 ignored, 1 inside the repository and not ignored, 128 outside the repository
    ignore_rc=0
    git check-ignore -q -- "$output_path" 2>/dev/null || ignore_rc=$?
    [ "$ignore_rc" -ne 1 ] || die 2 "output paths inside the repository must be gitignored: $output_path"
done

model=${WF_AGENT_MODEL:-}
effort=${WF_AGENT_EFFORT:-}
case $effort in
    *[!a-z]*) die 2 "WF_AGENT_EFFORT must be a lowercase level such as high: $effort" ;;
esac
stdout_is_output=1
case $agent in
    claude)
        set -- claude -p --no-session-persistence --output-format text
        [ -n "$model" ] && set -- "$@" --model "$model"
        [ -n "$effort" ] && set -- "$@" --effort "$effort"
        if [ "$mode" = ro ]; then
            set -- "$@" --permission-mode dontAsk \
                --allowedTools "Read,Grep,Glob,Bash(git diff *),Bash(git log *),Bash(git show *),Bash(git status *)" \
                --disallowedTools "Edit,Write,NotebookEdit"
        else
            set -- "$@" --permission-mode "${WF_CLAUDE_PERMISSION_MODE:-acceptEdits}" \
                --allowedTools "Bash,Read,Edit,Write,Grep,Glob"
        fi
        ;;
    codex)
        sandbox=read-only
        [ "$mode" = rw ] && sandbox=workspace-write
        set -- codex exec -s "$sandbox" --ephemeral -C "$root" -o "$out"
        [ -n "$model" ] && set -- "$@" -m "$model"
        [ -n "$effort" ] && set -- "$@" -c "model_reasoning_effort=\"$effort\""
        set -- "$@" -
        stdout_is_output=0
        ;;
    gemini)
        [ -z "$effort" ] || die 2 "gemini CLI has no effort setting; unset WF_AGENT_EFFORT"
        approval=plan
        if [ "$mode" = rw ]; then
            [ "${WF_GEMINI_SANDBOX:-0}" = 1 ] ||
                die 2 "Gemini headless rw needs WF_GEMINI_SANDBOX=1; otherwise use an interactive session"
            approval=yolo
        fi
        set -- gemini --skip-trust --approval-mode "$approval" --output-format text
        [ "${WF_GEMINI_SANDBOX:-0}" = 1 ] && set -- "$@" --sandbox
        [ -n "$model" ] && set -- "$@" -m "$model"
        set -- "$@" -p "Follow the instructions above exactly."
        ;;
    opencode)
        set -- opencode run
        [ "$mode" = ro ] && set -- "$@" --agent plan
        [ -n "$model" ] && set -- "$@" --model "$model"
        [ -n "$effort" ] && set -- "$@" --variant "$effort"
        set -- "$@" --file "$prompt" "Follow the attached workflow prompt."
        ;;
    *) usage ;;
esac

if [ "${WF_AGENT_DRY_RUN:-0}" = 1 ]; then
    printf '%s\n' "$*"
    exit 0
fi

command -v "$agent" >/dev/null 2>&1 || die 4 "$agent CLI not found on PATH"

fingerprint() {
    {
        git rev-parse HEAD 2>/dev/null || true
        git status --porcelain=v1 --untracked-files=all
        git diff HEAD --binary 2>/dev/null || true
    } | git hash-object --stdin
}

timeout_s=${WF_AGENT_TIMEOUT:-1800}
is_uint "$timeout_s" && [ "$timeout_s" -gt 0 ] || die 2 "WF_AGENT_TIMEOUT must be a positive integer"
if command -v timeout >/dev/null 2>&1; then
    timeout_cmd=timeout
elif command -v gtimeout >/dev/null 2>&1; then
    timeout_cmd=gtimeout
else
    die 4 "timeout or gtimeout is required (on macOS: brew install coreutils)"
fi
set -- "$timeout_cmd" -k 30 "$timeout_s" "$@"

before=$(fingerprint)
: >"$out"
rc=0
if [ "$stdout_is_output" = 1 ]; then
    "$@" <"$prompt" >"$out" 2>"$log" || rc=$?
else
    "$@" <"$prompt" >"$log" 2>&1 || rc=$?
fi

if [ "$expect_clean" = 1 ] && [ "$(fingerprint)" != "$before" ]; then
    echo "run-agent: the working tree changed during a run that must not modify it:" >&2
    git status --short >&2
    exit 3
fi
if [ "$rc" -ne 0 ]; then
    echo "run-agent: $agent exited with $rc (124 = timeout); last lines of $log:" >&2
    tail -n 20 "$log" >&2 || true
    exit 5
fi
[ -s "$out" ] || die 5 "$agent produced no output; see $log"

# Strip a stray code fence wrapped around the whole message.
if head -n 1 "$out" | grep -q '^```'; then
    tmp="$out.tmp"
    sed -e '1d' -e '${/^```[[:space:]]*$/d;}' "$out" >"$tmp" && mv "$tmp" "$out"
fi

echo "run-agent: ok — $out" >&2
