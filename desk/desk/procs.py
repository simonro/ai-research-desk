"""Start processes as heads of their own trees, and stop a whole run: the desk and every team.

A desk run is a tree: the desk, each team's runner in its own venv, and the claude or codex
calls under those. Each runner is the head of its own process group (POSIX: a new session), so
a timeout can stop one team without touching the others. That also takes the runners out of the
desk's group, so stopping the desk's group alone would leave them working and spending the plan
(Codex F1). Every runner therefore registers itself in the run folder while it is alive, and
stopping a run stops the desk's tree and every registered runner's tree. Two paths cover it:
the desk stops its runners when it is asked to stop (SIGTERM, `stop_runners_on_term`), and the
dashboard also stops whatever is still registered, which covers a desk that died abruptly.

Windows: every launched process is put in its own Job Object, which every process it starts
joins too, so the whole tree is stopped with one call even after the head exited and left
orphans (taskkill /T only walks from a live process, and a venv launcher's real interpreter
dies with it). A runner's job also closes, killing its tree, if the desk holding it dies.
Elsewhere a group gets SIGTERM, then SIGKILL after a grace period. Pure standard library (ctypes
on Windows): the dashboard and every runner can import it.
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


def launch(args: list, kill_on_close: bool = False, **kw) -> subprocess.Popen:
    """Popen as the head of a tree kill_tree can stop. On Windows the tree is a Job Object;
    `kill_on_close` also stops it when the launching process dies (used for team runners, not
    for the desk itself, which should outlive a closed dashboard)."""
    proc = subprocess.Popen(args, **tree_kwargs(), **kw)
    if WINDOWS:
        proc.job = _job_for(proc, kill_on_close)
    return proc


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
        job = getattr(proc, "job", None)
        if job:
            _k32.TerminateJobObject(job, 1)          # the head and every process it ever started
        elif proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        for pid in heads[1:]:                        # recorded runners: already in the desk's job
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


if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Extended(ctypes.Structure):
        _fields_ = [("Basic", _Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job_for(proc: subprocess.Popen, kill_on_close: bool):
    """A Job Object holding proc (and everything it starts from now on), or None if Windows refused."""
    handle = getattr(proc, "_handle", None)        # absent on a stand-in process (tests)
    if handle is None:
        return None
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return None
    if kill_on_close:
        info = _Extended()
        info.Basic.LimitFlags = 0x2000               # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    if not _k32.AssignProcessToJobObject(job, int(handle)):
        _k32.CloseHandle(job)
        return None
    return job


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
