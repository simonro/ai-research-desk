"""Start processes as heads of their own trees, and stop a whole run: the desk and every team.

A desk run is a tree: the desk, each team's runner in its own venv, and the claude or codex
calls under those. Each runner is the head of its own process group (POSIX: a new session), so
a timeout can stop one team without touching the others. That also takes the runners out of the
desk's group, so stopping the desk's group alone would leave them working and spending the plan
(Codex F1). Every runner therefore registers itself in the run folder while it is alive, and
stopping a run stops the desk's tree and every registered runner's tree. Two paths cover it:
the desk stops its runners when it is asked to stop (SIGTERM, `stop_runners_on_term`), and the
dashboard also stops whatever is still registered, which covers a desk that died abruptly.

Windows kills by tree (taskkill /T); elsewhere a group gets SIGTERM, then SIGKILL after a grace
period. Pure standard library: the dashboard and every runner can import it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

WINDOWS = os.name == "nt"
GROUPS_FILE = "process-groups.txt"          # in the run folder: one live runner pid per line
_LIVE: set[subprocess.Popen] = set()
_LOCK = threading.Lock()


def tree_kwargs() -> dict:
    """Popen keyword arguments that make the new process the head of a tree kill_tree can stop."""
    if WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def register(run_dir: Path, proc: subprocess.Popen) -> None:
    """Record a live runner, in this process and in the run folder."""
    with _LOCK:
        _LIVE.add(proc)
        _write(run_dir, _read(run_dir) | {proc.pid})


def unregister(run_dir: Path, proc: subprocess.Popen) -> None:
    """A finished runner leaves the record, so its pid is never signalled after it is reused."""
    with _LOCK:
        _LIVE.discard(proc)
        _write(run_dir, _read(run_dir) - {proc.pid})


def owned(run_dir: Path) -> list[int]:
    """The runner pids a run folder records as still alive (each heads its own group)."""
    with _LOCK:
        return sorted(_read(run_dir))


def stop_runners_on_term() -> None:
    """In the desk: on SIGTERM, stop every live runner's tree, then exit. No-op on Windows, where
    the dashboard's taskkill /T already reaches the runners through the desk's tree."""
    if WINDOWS:
        return

    def handler(signum, frame):
        with _LOCK:
            live = list(_LIVE)
        for p in live:
            kill_tree(p, grace=2.0)
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, handler)


def kill_tree(proc: subprocess.Popen, grace: float = 5.0, groups=()) -> None:
    """Stop proc and everything it started, plus the trees headed by `groups` (pids of runners in
    their own groups). Safe on processes that already exited, and safe to repeat."""
    heads = [proc.pid, *[g for g in groups if g != proc.pid]]
    if WINDOWS:
        for pid in heads:
            if pid == proc.pid and proc.poll() is not None:
                continue
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        for pid in heads:
            _signal_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(_group_alive(pid) for pid in heads):
            proc.poll()                      # reap the leader so it does not count as alive
            time.sleep(0.1)
        for pid in heads:
            _signal_group(pid, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _read(run_dir: Path) -> set[int]:
    path = Path(run_dir) / GROUPS_FILE
    try:
        return {int(x) for x in path.read_text(encoding="utf-8").split() if x.isdigit()}
    except OSError:
        return set()


def _write(run_dir: Path, pids: set[int]) -> None:
    path = Path(run_dir) / GROUPS_FILE
    try:
        path.write_text("".join(f"{p}\n" for p in sorted(pids)), encoding="utf-8")
    except OSError:
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
