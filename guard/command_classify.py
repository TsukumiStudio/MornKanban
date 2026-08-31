"""Fail-closed classifier for Bash commands issued from a secretary pane.

Secretary panes may only: read policy/board files, run `kanban`
add/show/list/init/version/send, run `kanban-secretary.sh`
bootstrap/dispatch/end, and run a small set of read-only inspection commands
(read-only git, cat/ls/grep/find/... with no write side effect). Every other
command - implementation, verification, git mutation, GitHub/GitLab
mutation, headless agent CLIs, package publish/deploy - is denied by
default. This is an allowlist, not a denylist: an unrecognized command is
rejected rather than passed through, which is what keeps shell chaining,
absolute paths, wrapper scripts and `sh -c` from bypassing the allowlist.

python3 stdlib only.
"""
import re
import shlex

# Wrapper binaries that just re-invoke another command. Their own leading
# arguments are stripped and the remainder is classified as a fresh segment.
_ENV_LIKE = {"env", "nohup", "time", "command", "exec"}
_SHELL_C = {"sh", "bash", "zsh", "dash", "ksh"}

# Binaries that must never run from a secretary pane, regardless of args.
_HARD_DENY_BINARIES = {
    "sudo", "su", "doas",
    "claude", "codex",  # headless agent CLIs; also blocks `claude -p` / `codex exec`
    "gh", "glab", "hub",  # GitHub/GitLab CLIs (external publish surface)
    "npm", "pnpm", "yarn", "bun",  # package manager (publish/install/build/test)
    "docker", "podman", "kubectl",
    "curl", "wget",  # arbitrary network egress / can itself write files
    "ssh", "scp", "rsync",
    "make", "cargo", "go", "pytest", "jest", "vitest", "rake", "gradle",
    "xargs", "eval",
    # general-purpose interpreters: arg-independent allow here would let
    # `python3 -c "..."` / `node -e "..."` write files or shell out to
    # git/gh/headless-agent CLIs, defeating every other rule in this file.
    "python3", "python", "python2", "node", "nodejs", "deno",
    "perl", "ruby", "php", "lua", "irb", "pry",
}

_GIT_READONLY_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "remote", "rev-parse",
    "describe", "ls-files", "blame", "cat-file", "shortlog", "reflog",
    "help", "--version", "-v", "--help",
}
# Flags that turn an otherwise-readonly git subcommand into a mutation
# (e.g. `git branch -D foo`, `git remote add x y`).
_GIT_WRITE_FLAGS = {
    "-d", "-D", "-m", "-M", "-c", "-C", "-f", "--force",
    "--delete", "--set-upstream", "--set-upstream-to", "add", "rm",
    "prune", "set-url", "rename",
}

_KANBAN_READONLY_SUBCOMMANDS = {
    "init", "add", "show", "list", "ls", "send", "version", "--version",
    "projects",
}

_KANBAN_SECRETARY_SUBCOMMANDS = {"bootstrap", "dispatch", "end"}

_READONLY_UTILITIES = {
    "cat", "ls", "grep", "egrep", "fgrep", "head", "tail", "wc", "pwd",
    "echo", "date", "true", "printf", "which", "type", "basename",
    "dirname", "realpath", "jq", "tree", "file", "diff",
    "stat", "env",
}
# find(1) doubles as a file-mutation tool via -delete/-exec/-fprintf; only
# allow it when none of those flags are present (checked separately below).
_FIND_WRITE_FLAGS = {"-delete", "-exec", "-execdir", "-fprintf", "-ok", "-okdir"}

_SPLIT_RE = re.compile(r";|&&|\|\||\n|\|")
_SUBSTITUTION_RE = re.compile(r"\$\(|`|<\(|>\(")
# A `>` or `>>` not part of `2>&1`/`&>` and not redirecting to /dev/null is
# treated as a file write, regardless of which binary is on the left.
_REDIRECT_RE = re.compile(r"(?<![0-9&])>>?(?!&)")


def _has_unsafe_redirect(segment):
    for m in _REDIRECT_RE.finditer(segment):
        target = segment[m.end():].strip().split()[:1]
        target = target[0] if target else ""
        if target not in ("/dev/null", ""):
            return True
    return False


def _classify_argv(argv):
    """Return (allowed: bool, reason: str) for one already-tokenized argv."""
    if not argv:
        return True, "empty"

    name = argv[0].rsplit("/", 1)[-1]

    if name in _HARD_DENY_BINARIES:
        return False, "denylisted binary: %s" % name

    if name in _ENV_LIKE:
        rest = list(argv[1:])
        while rest and ("=" in rest[0] and not rest[0].startswith("-")):
            rest.pop(0)
        while rest and rest[0].startswith("-"):
            rest.pop(0)
        if not rest:
            return False, "wrapper with no inner command: %s" % name
        return _classify_argv(rest)

    if name in _SHELL_C:
        if "-c" in argv:
            idx = argv.index("-c")
            if idx + 1 < len(argv):
                return _classify_segment(argv[idx + 1])
        return False, "interactive/unclassifiable shell invocation: %s" % name

    if name == "git":
        if len(argv) < 2:
            return False, "git with no subcommand"
        sub = argv[1]
        if sub not in _GIT_READONLY_SUBCOMMANDS:
            return False, "git mutation subcommand: %s" % sub
        if any(a in _GIT_WRITE_FLAGS for a in argv[2:]):
            return False, "git %s with a write flag" % sub
        return True, "read-only git %s" % sub

    if name in ("kanban", "kanban.sh"):
        if len(argv) < 2:
            return False, "kanban with no subcommand"
        sub = argv[1]
        if sub not in _KANBAN_READONLY_SUBCOMMANDS:
            return False, "kanban mutation subcommand: %s (headless run/monitor/install is not the secretary path)" % sub
        return True, "kanban %s" % sub

    if name == "kanban-secretary.sh":
        sub = argv[1] if len(argv) > 1 else ""
        if sub not in _KANBAN_SECRETARY_SUBCOMMANDS:
            return False, "kanban-secretary.sh with unknown subcommand: %s" % sub
        return True, "kanban-secretary.sh %s" % sub

    if name == "find":
        if any(a in _FIND_WRITE_FLAGS for a in argv[1:]):
            return False, "find with a mutating action flag"
        return True, "read-only find"

    if name in _READONLY_UTILITIES:
        return True, "read-only utility: %s" % name

    return False, "not allowlisted: %s" % name


def _classify_segment(segment):
    segment = segment.strip()
    if not segment:
        return True, "empty"
    if _SUBSTITUTION_RE.search(segment):
        return False, "command/process substitution is not classifiable"
    if _has_unsafe_redirect(segment):
        return False, "output redirection to a file"
    try:
        argv = shlex.split(segment)
    except ValueError as exc:
        return False, "unparsable command: %s" % exc
    return _classify_argv(argv)


def classify(command):
    """Classify a full Bash tool command string.

    Returns (allowed: bool, reason: str). `allowed` is True only when every
    chained segment (split on `;`, `&&`, `||`, `|`, newline) is individually
    allowed; command/process substitution and unparsable input are denied.
    """
    if command is None:
        return False, "no command"
    segments = [s for s in _SPLIT_RE.split(command) if s.strip()]
    if not segments:
        return True, "empty command"
    for segment in segments:
        allowed, reason = _classify_segment(segment)
        if not allowed:
            return False, reason
    return True, "all segments allowed"
