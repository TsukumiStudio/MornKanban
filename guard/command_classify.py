"""Fail-closed classifier for Bash commands issued from a secretary pane.

Secretary panes may only: read policy/board files, run `kanban` operations,
run `kanban-secretary.sh`
bootstrap/dispatch/end, and run a small set of read-only inspection commands
(`kanban inspect` for Git, cat/ls/grep/find/... with no write side effect). Every other
command - implementation, verification, git mutation, GitHub/GitLab
mutation, headless agent CLIs, package publish/deploy - is denied by
default. This is an allowlist, not a denylist: an unrecognized command is
rejected rather than passed through, which is what keeps shell chaining,
absolute paths, wrapper scripts and `sh -c` from bypassing the allowlist.

python3 stdlib only.
"""
import re
import shlex
import shutil
import os

# Wrapper binaries can alter the environment or execution semantics of an
# otherwise allowed command, so the secretary must invoke commands directly.
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

_KANBAN_SUBCOMMANDS = {
    "", "init", "add", "remove", "config", "list", "ls", "show",
    "resume", "operation", "inspect", "projects", "send", "--version", "version",
    "install", "update", "uninstall",
}

_KANBAN_SECRETARY_SUBCOMMANDS = {"bootstrap", "dispatch", "end"}

_READONLY_UTILITIES = {
    "cat", "ls", "grep", "egrep", "fgrep", "head", "tail", "wc", "pwd",
    "echo", "date", "true", "printf", "which", "type", "basename",
    "dirname", "realpath", "jq", "tree", "file", "diff",
    "stat",
}
# find(1) doubles as a file-mutation tool via -delete/-exec/-fprintf; only
# allow it when none of those flags are present (checked separately below).
_FIND_WRITE_FLAGS = {
    "-delete", "-exec", "-execdir", "-fprintf", "-fprint", "-fprint0",
    "-fls", "-ok", "-okdir",
}

_SUBSTITUTION_RE = re.compile(r"\$\(|`|<\(|>\(")
# A `>`/`>>`, with or without an fd prefix, is a file write unless it targets
# /dev/null. fd duplication such as `2>&1` does not match.
_REDIRECT_RE = re.compile(r"(?<![&<>])(?:[0-9]+)?(?:<>|>>?)(?![>&])")

_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_MANAGED_EXECUTABLES = {
    "kanban": os.path.join(_REPO, "kanban.sh"),
    "kanban.sh": os.path.join(_REPO, "kanban.sh"),
    "kanban-secretary.sh": os.path.join(_REPO, "kanban-secretary.sh"),
}


def _is_managed_executable(token, name):
    expected = os.path.realpath(_MANAGED_EXECUTABLES[name])
    if "/" in token:
        actual = os.path.realpath(os.path.abspath(os.path.expanduser(token)))
    else:
        found = shutil.which(token)
        actual = os.path.realpath(found) if found else ""
    return actual == expected


def _split_command(command):
    """Split shell operators only outside quotes; reject background `&`."""
    segments, start, quote, escaped = [], 0, None, False
    i = 0
    while i < len(command):
        ch = command[i]
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "&" and i > 0 and command[i - 1] == ">":
            pass
        elif ch in ";\n|&":
            if ch == "&" and (i == 0 or command[i - 1] != ">") and not (
                i + 1 < len(command) and command[i + 1] == "&"
            ):
                return None
            segments.append(command[start:i])
            if i + 1 < len(command) and command[i + 1] == ch and ch in "|&":
                i += 1
            start = i + 1
        i += 1
    segments.append(command[start:])
    return segments


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
        return False, "command wrapper is not allowed: %s" % name

    if name in _SHELL_C:
        if "-c" in argv:
            idx = argv.index("-c")
            if idx + 1 < len(argv):
                return _classify_segment(argv[idx + 1])
        return False, "interactive/unclassifiable shell invocation: %s" % name

    if name == "git":
        return False, "direct git may run configured helpers; use kanban inspect"

    if name in ("kanban", "kanban.sh"):
        if not _is_managed_executable(argv[0], name):
            return False, "unmanaged kanban executable: %s" % argv[0]
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "run":
            return False, "bare kanban run bypasses the visible dispatcher"
        if sub not in _KANBAN_SUBCOMMANDS:
            return False, "unknown kanban subcommand: %s" % sub
        return True, "kanban %s" % (sub or "help")

    if name == "kanban-secretary.sh":
        if not _is_managed_executable(argv[0], name):
            return False, "unmanaged kanban-secretary executable: %s" % argv[0]
        sub = argv[1] if len(argv) > 1 else ""
        if sub not in _KANBAN_SECRETARY_SUBCOMMANDS:
            return False, "kanban-secretary.sh with unknown subcommand: %s" % sub
        return True, "kanban-secretary.sh %s" % sub

    if name == "find":
        if any(a in _FIND_WRITE_FLAGS for a in argv[1:]):
            return False, "find with a mutating action flag"
        return True, "read-only find"

    if name == "tree" and any(a.startswith("-o") or a.startswith("--output=") for a in argv[1:]):
        return False, "tree with an output-file option"

    if name == "diff" and any(a == "--output" or a.startswith("--output=") for a in argv[1:]):
        return False, "diff with an output-file option"

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
    split = _split_command(command)
    if split is None:
        return False, "background shell execution is not allowed"
    segments = [s for s in split if s.strip()]
    if not segments:
        return True, "empty command"
    for segment in segments:
        allowed, reason = _classify_segment(segment)
        if not allowed:
            return False, reason
    return True, "all segments allowed"
