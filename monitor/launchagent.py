"""macOS user LaunchAgent lifecycle for `kanban monitor` (PC-resident mode).

Manages exactly one plist, identified by LABEL, under
`~/Library/LaunchAgents`. Never touches any other file or LaunchAgent.
Idempotent: install/uninstall/start/stop can be called repeatedly.

All launchctl invocations go through an injectable `runner` (defaults to
`subprocess.run`) so tests can verify behavior without registering a real
daemon.
"""
import os
import plistlib
import subprocess
import sys

LABEL = "dev.mornkanban.monitor"


def plist_path():
    return os.path.join(os.path.expanduser("~/Library/LaunchAgents"), LABEL + ".plist")


def log_dir():
    return os.path.join(os.path.expanduser("~/Library/Logs"), "MornKanban")


def _service_target():
    return "gui/%d/%s" % (os.getuid(), LABEL)


def _domain_target():
    return "gui/%d" % os.getuid()


def build_plist_bytes(python_bin, cli_path, host, port, roots):
    args = [python_bin, cli_path, "run", "--host", host, "--port", str(port)]
    for r in roots:
        args += ["--root", r]
    logs = log_dir()
    plist = {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": os.path.join(logs, "monitor.out.log"),
        "StandardErrorPath": os.path.join(logs, "monitor.err.log"),
    }
    return plistlib.dumps(plist)


def install(host="127.0.0.1", port=8787, roots=None, python_bin=None, cli_path=None):
    python_bin = python_bin or sys.executable
    cli_path = cli_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cli.py")
    roots = list(roots or [])

    os.makedirs(os.path.dirname(plist_path()), exist_ok=True)
    os.makedirs(log_dir(), exist_ok=True)
    data = build_plist_bytes(python_bin, cli_path, host, port, roots)
    tmp = plist_path() + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, plist_path())
    return True, "installed: %s (host=%s port=%s)" % (plist_path(), host, port)


def is_installed():
    return os.path.isfile(plist_path())


def start(runner=subprocess.run):
    if not is_installed():
        return False, "not installed; run 'kanban monitor daemon install' first"
    r = runner(["launchctl", "bootstrap", _domain_target(), plist_path()], capture_output=True, text=True)
    if r.returncode == 0:
        return True, "started"
    stderr = (r.stderr or "").strip()
    if "already bootstrapped" in stderr.lower() or "service already loaded" in stderr.lower():
        r2 = runner(["launchctl", "kickstart", "-k", _service_target()], capture_output=True, text=True)
        if r2.returncode == 0:
            return True, "restarted"
        return False, "kickstart failed: %s" % (r2.stderr or r2.stdout).strip()
    return False, "bootstrap failed: %s" % (stderr or r.stdout.strip())


def stop(runner=subprocess.run):
    if not is_installed():
        return True, "not installed"
    r = runner(["launchctl", "bootout", _service_target()], capture_output=True, text=True)
    stderr = (r.stderr or "").strip()
    if r.returncode == 0 or "no such process" in stderr.lower() or "could not find" in stderr.lower():
        return True, "stopped"
    return False, "bootout failed: %s" % (stderr or r.stdout.strip())


def status(runner=subprocess.run):
    installed = is_installed()
    running = False
    detail = ""
    if installed:
        r = runner(["launchctl", "print", _service_target()], capture_output=True, text=True)
        if r.returncode == 0:
            detail = r.stdout
            running = "state = running" in r.stdout
    return {"installed": installed, "running": running, "detail": detail}


def uninstall(runner=subprocess.run):
    messages = []
    ok, msg = stop(runner=runner)
    messages.append(msg)
    if is_installed():
        os.remove(plist_path())
        messages.append("removed: %s" % plist_path())
    else:
        messages.append("plist not present")
    return True, messages
