"""Start a process as the head of its own tree, and stop the whole tree.

A desk run is a tree: the desk, each team's runner in its own venv, and the claude or codex
calls under those. Killing only the top process leaves the rest working and spending the plan.
Windows kills by tree (taskkill /T). Elsewhere the run starts as the leader of a new session,
so its process group holds every descendant that did not leave it on purpose; the group gets
SIGTERM, then SIGKILL after a grace period.

Pure standard library: the dashboard and every runner can import it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

WINDOWS = os.name == "nt"


def tree_kwargs() -> dict:
    """Popen keyword arguments that make the new process the head of a tree kill_tree can stop."""
    if WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """Stop proc and everything it started. Safe on a process that already exited."""
    if WINDOWS:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        # The group outlives its leader: children can still be running after the desk exited.
        _signal_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and _group_alive(proc.pid):
            proc.poll()                      # reap the leader so it does not count as alive
            time.sleep(0.1)
        _signal_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
