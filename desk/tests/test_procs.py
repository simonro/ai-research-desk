"""kill_tree stops a run's whole process tree, and the desk's system prompt fits any team count."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from desk.llm import DESK_SYSTEM
from desk.procs import kill_tree, tree_kwargs

# parent -> child -> grandchild, each writing its pid and then sleeping. Harmless and owned by the test.
TREE = r"""
import subprocess, sys, time
from pathlib import Path
out, depth = Path(sys.argv[1]), int(sys.argv[2])
(out / f"pid{depth}").write_text(str(__import__("os").getpid()))
if depth < 2:
    subprocess.Popen([sys.executable, __file__, str(out), str(depth + 1)])
time.sleep(120)
"""


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.split(")")[-1].split()[0] != "Z"          # a zombie has already exited
    except FileNotFoundError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _start_tree(tmp_path: Path) -> tuple[subprocess.Popen, list[int]]:
    script = tmp_path / "tree.py"
    script.write_text(TREE)
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path), "0"], **tree_kwargs())
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (tmp_path / "pid2").exists():
        time.sleep(0.1)
    pids = [int((tmp_path / f"pid{d}").read_text()) for d in range(3)]
    return proc, pids


@pytest.mark.skipif(os.name == "nt", reason="the POSIX process-group path; Windows uses taskkill /T")
def test_kill_tree_stops_child_and_grandchild(tmp_path):
    proc, pids = _start_tree(tmp_path)
    assert all(_alive(p) for p in pids)
    kill_tree(proc, grace=2)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    assert not [p for p in pids if _alive(p)]


@pytest.mark.skipif(os.name == "nt", reason="the POSIX process-group path; Windows uses taskkill /T")
def test_kill_tree_reaches_children_after_the_leader_exited(tmp_path):
    # The desk can exit while a team runner it started is still working: the group still holds it.
    proc, pids = _start_tree(tmp_path)
    os.kill(pids[0], 9)
    proc.wait(timeout=10)
    assert _alive(pids[1]) and _alive(pids[2])
    kill_tree(proc, grace=2)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(_alive(p) for p in pids[1:]):
        time.sleep(0.1)
    assert not [p for p in pids[1:] if _alive(p)]


def test_kill_tree_on_a_finished_process_is_harmless(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "pass"], **tree_kwargs())
    proc.wait(timeout=30)
    kill_tree(proc, grace=0.5)
    assert proc.returncode == 0


def test_system_prompt_fits_two_or_three_teams():
    # It used to name two teams and "the two desk reports", contradicting a three-team run.
    assert "two" not in DESK_SYSTEM.lower().replace("any two", "")
    for team in ("TradingAgents", "ai-hedge-fund", "Edge Desk"):
        assert team in DESK_SYSTEM
    assert "not independent" in DESK_SYSTEM
