#!/bin/sh
# run-agent.sh — run one wf stage in a fresh, headless agent session.
#
# Usage: run-agent.sh [--expect-clean] <claude|codex|gemini|opencode> <ro|rw> <prompt-file> <out-file>
#        run-agent.sh --wait <out-file> [seconds]
#
#   ro   read-only: the agent may read files and inspect git; it must not modify anything.
#        The working tree and the private records in development/ (except runs/) are always
#        checked afterwards.
#   rw   the agent may edit files and run commands in the repository.
#   In every mode, the personal role settings must not change during the run: development/wf-settings.md
#   and agentic-dev-workflow/settings.md in $XDG_CONFIG_HOME (absolute), else in ~/.config.
#   --expect-clean  with rw: fail with exit 3 if the tree or the records changed — for reviews that must
#        run the project's checks but must not touch the code.
#
# The agent's final message is written to <out-file>; its progress and errors go to <out-file>.log;
# the exit code goes to <out-file>.exit when the run ends.
#   --wait  block until the run writing <out-file> has finished (at most <seconds>, default 540), then
#        exit with its exit code, repeating the runner's reason for a failure; exit 124 if it is still
#        running. For hosts whose shell tool cannot wait 30 minutes: start the run in the background,
#        then call --wait until it returns.
#
# Environment:
#   WF_AGENT_TIMEOUT           seconds before the run is killed (default 1800)
#   WF_AGENT_MODEL             model passed to the agent CLI (default: the CLI's own default)
#   WF_AGENT_EFFORT            effort level, lowercase (claude: --effort; codex: model_reasoning_effort;
#                              gemini has none and refuses it). Default: the CLI's own setting
#   WF_CLAUDE_PERMISSION_MODE  claude permission mode for rw (default acceptEdits)
#   WF_GEMINI_SANDBOX=1        run gemini with --sandbox; required for headless rw yolo mode
#   WF_AGENT_NETWORK=0|1       codex rw: network inside the sandbox. Default: the `Agent network: on|off`
#                              line of the wf block in AGENTS.md, else off
#
# Codex rw runs also get the real path of development/ as a writable directory when it lies outside the
# repository (a task worktree links it to the main worktree). Codex keeps .git read-only, so a delegated
# Codex run cannot commit; the orchestrator commits its changes.
#   WF_AGENT_DRY_RUN=1         print the agent command line and exit 0
#   WF_AGENT_EXPECT_HEAD       a full commit id: start only when HEAD is that commit and the working tree
#                              has no change, untracked files and submodules included, else exit 3 (wf sec
#                              audit pins its agents to the audited commit)
#
# Exit codes: 0 ok, 2 usage, 3 tree, records or local settings changed, 4 required executable not found,
#             5 agent failed, timed out (124) or produced no output,
#             6 the agent reached its usage limit (its message, repeated on stderr, says when it resets),
#             7 the same, but the run had already changed the tree, HEAD or the records
set -eu

usage() {
    sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

# A message for the caller. Once the output paths are checked, it is also appended to the log, so that
# --wait repeats it for a background run.
say() {
    echo "run-agent: $*" >&2
    if [ -n "${say_log:-}" ]; then printf 'run-agent: %s\n' "$*" >>"$say_log" 2>/dev/null || true; fi
}

die() {
    code=$1
    shift
    say "$*"
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

# The real path of development/ when it lies outside the repository root; empty otherwise.
records_outside_root() {
    [ -e "$root/development" ] || return 0
    real=$(cd -P "$root/development" && pwd) || return 1
    case $real in
        "$root"|"$root"/*) ;;
        *) printf '%s\n' "$real" ;;
    esac
}

# on or off: WF_AGENT_NETWORK, else the wf block's `- Agent network:` line, else off.
agent_network() {
    case ${WF_AGENT_NETWORK:-} in
        1) echo on; return ;;
        0) echo off; return ;;
        '') ;;
        *) die 2 "WF_AGENT_NETWORK must be 0 or 1: $WF_AGENT_NETWORK" ;;
    esac
    value=
    if [ -f "$root/AGENTS.md" ]; then
        value=$(sed -n '/<!-- wf:begin/,/<!-- wf:end -->/s/^- Agent network:[[:space:]]*\([a-z]*\).*/\1/p' \
            "$root/AGENTS.md" | head -n 1)
    fi
    case $value in
        on) echo on ;;
        off|'') echo off ;;
        *) die 2 "AGENTS.md: Agent network must be on or off: $value" ;;
    esac
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
    if [ "$code" != 0 ]; then
        grep '^run-agent: ' "$out.log" 2>/dev/null | tail -n 3 >&2 || true
    fi
    echo "run-agent: finished with exit $code — $out" >&2
    exit "$code"
fi

expect_clean=0
if [ "${1:-}" = "--expect-clean" ]; then
    expect_clean=1
    shift
fi
[ $# -eq 4 ] || usage
# This script's own directory, before the cd below: resolve-roles.py lies next to it.
here=$(cd "$(dirname "$0")" && pwd) || die 2 "cannot find the directory of $0"
agent=$1
mode=$2
prompt=$(abspath "$3")
out=$(abspath "$4")
log="$out.log"
[ "$prompt" != "$out" ] && [ "$prompt" != "$log" ] || die 2 "prompt and output paths must differ"
[ ! -L "$out" ] && [ ! -L "$log" ] && [ ! -L "$out.tmp" ] && [ ! -L "$out.exit" ] ||
    die 2 "output paths must not be symlinks"
root=$(git rev-parse --show-toplevel 2>/dev/null) || die 2 "not inside a git repository"
cd "$root"
# Reject output paths before anything is written to them.
for output_path in "$out" "$log" "$out.tmp" "$out.exit"; do
    git ls-files --error-unmatch -- "$output_path" >/dev/null 2>&1 &&
        die 2 "output path is tracked: $output_path"
    # check-ignore: 0 ignored, 1 inside the repository and not ignored, 128 outside the repository
    ignore_rc=0
    git check-ignore -q -- "$output_path" 2>/dev/null || ignore_rc=$?
    [ "$ignore_rc" -ne 1 ] || die 2 "output paths inside the repository must be gitignored: $output_path"
done
rm -f "$out.exit"
trap 'echo $? >"$out.exit"' EXIT
: >"$log"
say_log=$log

case $mode in
    ro) expect_clean=1 ;;
    rw) ;;
    *) usage ;;
esac
[ -r "$prompt" ] || die 2 "prompt file not readable: $prompt"

model=${WF_AGENT_MODEL:-}
effort=${WF_AGENT_EFFORT:-}
case $model in
    -*|*[[:space:]]*) die 2 "WF_AGENT_MODEL must be a name without spaces, not starting with -: $model" ;;
esac
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
        if [ "$mode" = rw ]; then
            records_dir=$(records_outside_root) || die 2 "cannot resolve development/: $root/development"
            [ -n "$records_dir" ] && set -- "$@" --add-dir "$records_dir"
            network=$(agent_network) || exit 2
            [ "$network" = on ] && set -- "$@" -c 'sandbox_workspace_write.network_access=true'
        fi
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
        # --file takes several values and would swallow a message after it: the message goes first.
        set -- "$@" "Follow the attached workflow prompt." --file "$prompt"
        ;;
    *) usage ;;
esac

if [ "${WF_AGENT_DRY_RUN:-0}" = 1 ]; then
    printf '%s\n' "$*"
    exit 0
fi

command -v "$agent" >/dev/null 2>&1 || die 4 "$agent CLI not found on PATH"

# The private records in development/ are gitignored, so git status does not see them. Hash them too,
# except runs/, which holds the prompts and outputs, and this run's own output files.
records() {
    [ -d development ] || return 0
    found=$(find -L development -path '*/runs' -prune -o -type f -print) || return 1
    paths=$(printf '%s\n' "$found" | LC_ALL=C sort |
        grep -vxF -e '' -e "${out#"$root"/}" -e "${log#"$root"/}" -e "${out#"$root"/}.tmp" \
            -e "${out#"$root"/}.exit" || true)
    [ -n "$paths" ] || return 0
    printf '%s\n' "$paths"
    printf '%s\n' "$paths" | git hash-object --no-filters --stdin-paths
}

# The working tree as a git tree, built in a temporary index with every file that is not ignored. Unlike
# git status and git diff HEAD, it sees an edit to a file that was untracked already, file modes and
# links, whatever the file names. Its new objects go to a temporary object directory that reads the
# repository's objects as an alternate: nothing in .git is written, which Codex keeps read-only.
worktree_tree() {
    objects=$(cd "$(git rev-parse --git-path objects)" && pwd) || return 1
    scratch=$(mktemp -d "${TMPDIR:-/tmp}/run-agent.XXXXXX") || return 1
    mkdir -p "$scratch/objects/info" && printf '%s\n' "$objects" >"$scratch/objects/info/alternates" ||
        { rm -rf "$scratch"; return 1; }
    cp "$(git rev-parse --git-path index)" "$scratch/index" 2>/dev/null || :
    snapshot_rc=0
    tree=$(export GIT_INDEX_FILE="$scratch/index" GIT_OBJECT_DIRECTORY="$scratch/objects"
        git add -A >/dev/null 2>&1 && git write-tree) || snapshot_rc=1
    rm -rf "$scratch"
    [ "$snapshot_rc" = 0 ] && printf '%s\n' "$tree"
}

# Each part is taken on its own, so that a part that fails fails the snapshot. The tree holds what is on
# disk; the real index entries add what is staged, which can differ.
fingerprint() {
    tree=$(worktree_tree) || return 1
    head=$(git rev-parse HEAD 2>/dev/null) || head=none
    status=$(GIT_OPTIONAL_LOCKS=0 git status --porcelain=v1 --untracked-files=all --ignore-submodules=none) ||
        return 1
    staged=$(git ls-files --stage) || return 1
    record_hashes=$(records) || return 1
    printf '%s\n' "$head" "$status" "$tree" "$staged" "$record_hashes" | git hash-object --stdin
}

# A snapshot that cannot be taken after the run counts as a change.
changed() {
    after=$(fingerprint) || return 0
    [ "$after" != "$before" ]
}

file_hash() {
    if [ -f "$1" ]; then
        git hash-object --stdin <"$1"
    else
        echo none
    fi
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

# The personal role settings decide which agents later stages run; no delegated run may change them.
# resolve-roles.py, next to this script, knows where they are.
settings_paths=$(python3 "$here/resolve-roles.py" --paths) ||
    die 4 "cannot locate the role settings: python3 and resolve-roles.py next to run-agent.sh are required"
user_settings=$(printf '%s\n' "$settings_paths" | sed -n 's/^user: //p')
project_settings=$(printf '%s\n' "$settings_paths" | sed -n 's/^project: //p')

before=$(fingerprint) || die 2 "cannot take a snapshot of the working tree"
# Checked after the snapshot, so that the run is compared with a tree that was the pinned commit, unchanged.
if [ -n "${WF_AGENT_EXPECT_HEAD:-}" ]; then
    actual=$(git rev-parse HEAD 2>/dev/null) || actual=none
    [ "$actual" = "$WF_AGENT_EXPECT_HEAD" ] ||
        die 3 "HEAD is $actual, but this run is pinned to $WF_AGENT_EXPECT_HEAD; nothing ran"
    pinned_status=$(GIT_OPTIONAL_LOCKS=0 git status --porcelain=v1 --untracked-files=all --ignore-submodules=none) ||
        die 3 "git status failed, so the tree of this pinned run is unknown; nothing ran"
    [ -z "$pinned_status" ] ||
        die 3 "the working tree has changes, but this run is pinned to an unchanged $WF_AGENT_EXPECT_HEAD; nothing ran"
fi
project_settings_before=$(file_hash "$project_settings")
user_settings_before=$(file_hash "$user_settings")
: >"$out"
rc=0
if [ "$stdout_is_output" = 1 ]; then
    "$@" <"$prompt" >"$out" 2>"$log" || rc=$?
else
    "$@" <"$prompt" >"$log" 2>&1 || rc=$?
fi

for settings_file in "$project_settings" "$user_settings"; do
    settings_before=$user_settings_before
    [ "$settings_file" = "$project_settings" ] && settings_before=$project_settings_before
    if [ "$(file_hash "$settings_file")" != "$settings_before" ]; then
        say "the role settings changed during the run: $settings_file;" \
            "the human must check them before any further run"
        exit 3
    fi
done
if [ "$expect_clean" = 1 ] && changed; then
    say "the working tree changed during a run that must not modify it"
    git status --short >&2
    exit 3
fi
if [ "$rc" -ne 0 ]; then
    # A usage limit is not transient. Codex prints "ERROR: You’ve hit your usage limit … try again at <time>"
    # to the log, Claude "You've hit your 5-hour limit · resets <time>" as its output. Only a line that starts
    # that way counts, and not one that is a line of the prompt, which Codex echoes into the log.
    if [ "$rc" -ne 124 ]; then
        limit_re='^[[:space:]]*((ERROR|Error):[[:space:]]*)?You[^[:space:]]* (hit|reached) your [[:alnum:] -]*limit'
        limit_line=$({ tail -n 50 "$log"; head -n 5 "$out"; } 2>/dev/null | grep -E "$limit_re" |
            grep -vxF -f "$prompt" | sed -n 1p)
        if [ -n "$limit_line" ]; then
            say "$agent reached its usage limit: $limit_line"
            if changed; then
                say "the run had already changed the tree, HEAD or the records;" \
                    "no other agent may continue it"
                exit 7
            fi
            exit 6
        fi
    fi
    tail -n 20 "$log" >&2 || true
    say "$agent exited with $rc (124 = timeout); the last lines of $log are above"
    exit 5
fi
[ -s "$out" ] || die 5 "$agent produced no output; see $log"

# Strip a stray code fence wrapped around the whole message: an opening line of exactly three backticks and
# an optional language, a closing line of exactly three backticks, nothing else.
if [ "$(awk 'END { print NR }' "$out")" -ge 2 ] && head -n 1 "$out" | grep -q '^```[A-Za-z0-9_+.-]*[[:space:]]*$' &&
    tail -n 1 "$out" | grep -q '^```[[:space:]]*$'; then
    tmp="$out.tmp"
    sed -e '1d' -e '$d' "$out" >"$tmp" && mv "$tmp" "$out"
fi

echo "run-agent: ok — $out" >&2
