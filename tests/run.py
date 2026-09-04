#!/usr/bin/env python3
"""Bounded test tiers; timeouts terminate the whole subprocess group."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]

WIRED_SH_TESTS = {
    "tests/test_herdr_agent_worker.sh",
    "tests/test_submodule_preservation.sh",
    "tests/test_submodule_publish_card.sh",
    "tests/test_review_prompt_injection.sh",
}


def find_unwired_sh_tests():
    found = {str(p.relative_to(REPO)) for p in (REPO / "tests").glob("test_*.sh")}
    return sorted(found - WIRED_SH_TESTS)


FAST_TESTS = [
    "tests.test_activity_log",
    "tests.test_dispatcher_tui",
    "tests.test_dependencies",
    "tests.test_registry",
    "tests.test_secretary_guard",
    "tests.test_setup_dashboard",
    "tests.test_kanban_secretary.CardEffortTests",
    "tests.test_kanban_secretary.PromptProjectionTests",
    "tests.test_kanban_secretary.WorkerParallelismTests",
    "tests.test_kanban_secretary.SecretaryNameResolutionTests",
    "tests.test_kanban_secretary.SecretaryDoesNotHoldCardsBackContractTests",
    "tests.test_kanban_secretary.DiagnosisCardContractTests",
    "tests.test_kanban_secretary.SecretaryForbidsInProcessDelegationContractTests",
]


def run_step(label, command, timeout, env=None):
    started = time.monotonic()
    print("==> %s (limit %ss)" % (label, timeout), flush=True)
    proc = subprocess.Popen(command, cwd=REPO, env=env, start_new_session=True)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        elapsed = time.monotonic() - started
        print("TIMEOUT: %s after %.1fs (entire process group stopped)" % (label, elapsed), file=sys.stderr)
        return 124
    elapsed = time.monotonic() - started
    print("<== %s: %s in %.1fs" % (label, "PASS" if rc == 0 else "FAIL", elapsed), flush=True)
    return rc


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    tier = argv.pop(0) if argv else "fast"
    env = os.environ.copy()
    env.setdefault("KANBAN_DISPATCH_POLL_INTERVAL", "0.05")

    unwired = find_unwired_sh_tests()
    if unwired:
        print("UNWIRED bash tests (add to tests/run.py tiers): %s" % ", ".join(unwired), file=sys.stderr)
        return 1

    if tier == "targeted":
        if not argv:
            print("usage: python3 tests/run.py targeted <unittest-name> [...]", file=sys.stderr)
            return 2
        return run_step("targeted python", [sys.executable, "-m", "unittest", "-v"] + argv, 60, env)

    if tier == "fast":
        steps = [
            ("submodule preservation", ["bash", "tests/test_submodule_preservation.sh"], 20),
            ("submodule publish card", ["bash", "tests/test_submodule_publish_card.sh"], 20),
            ("review prompt injection", ["bash", "tests/test_review_prompt_injection.sh"], 10),
            ("fast python", [sys.executable, "-m", "unittest", "-q"] + FAST_TESTS, 45),
        ]
    elif tier == "full":
        steps = [
            ("full python", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], 300),
            ("visible worker lifecycle", ["bash", "tests/test_herdr_agent_worker.sh"], 45),
            ("submodule preservation", ["bash", "tests/test_submodule_preservation.sh"], 20),
            ("submodule publish card", ["bash", "tests/test_submodule_publish_card.sh"], 20),
            ("review prompt injection", ["bash", "tests/test_review_prompt_injection.sh"], 10),
            ("skill validation", [
                sys.executable,
                os.path.expanduser("~/.codex/skills/.system/skill-creator/scripts/quick_validate.py"),
                "skills/kanban-dispatch",
            ], 20),
            ("report skill validation", [
                sys.executable,
                os.path.expanduser("~/.codex/skills/.system/skill-creator/scripts/quick_validate.py"),
                "skills/kanban-report",
            ], 20),
        ]
    else:
        print("unknown tier %r (targeted|fast|full)" % tier, file=sys.stderr)
        return 2

    results = []
    for label, command, timeout in steps:
        rc = run_step(label, command, timeout, env)
        results.append((label, rc))

    failed = [label for label, rc in results if rc]
    if failed:
        print("FAILED steps: %s" % ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
