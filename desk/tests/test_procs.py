"""Stopping a run stops the desk and every team runner, in the launch layout production uses:
the desk heads its own group, and each team runner heads another (Codex F1). Harmless sleeper
processes only, owned by the test. The system prompt check sits here too."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from desk.llm import DESK_SYSTEM
from desk.procs import GROUPS_FILE, kill_tree, owned, tree_kwargs

DESK_DIR = Path(__file__).resolve().parents[1]

# A stand-in desk: it starts two "team runners" exactly as engines._run does (tree_kwargs, then
# register in the run folder), each of which starts a "model call" grandchild, then waits. With
# --handler it installs the desk's own SIGTERM cleanup, as run_one does.
DESK = r"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from desk.procs import register, stop_runners_on_term, tree_kwargs
run_dir = Path(sys.argv[1])
if "--handler" in sys.argv:
    stop_runners_on_term()
(run_dir / "pid-desk").write_text(str(__import__("os").getpid()))
for team in ("quant", "vets"):
    p = subprocess.Popen([sys.executable, str(run_dir / "team.py"), str(run_dir), team], **tree_kwargs())
    register(run_dir, p)
time.sleep(120)
"""
TEAM = r"""
import os, subprocess, sys, time
from pathlib import Path
run_dir, team = Path(sys.argv[1]), sys.argv[2]
(run_dir / f"pid-{team}").write_text(str(os.getpid()))
if not team.endswith("-call"):
    subprocess.Popen([sys.executable, __file__, str(run_dir), team + "-call"])
time.sleep(120)
"""
NAMES = ("desk", "quant", "vets", "quant-call", "vets-call")


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


def _start(tmp_path: Path, handler: bool) -> tuple[subprocess.Popen, dict]:
    (tmp_path / "desk.py").write_text(DESK)
    (tmp_path / "team.py").write_text(TEAM)
    proc = subprocess.Popen([sys.executable, str(tmp_path / "desk.py"), str(tmp_path), str(DESK_DIR),
                             *(["--handler"] if handler else [])], **tree_kwargs())   # as the dashboard does
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not all((tmp_path / f"pid-{n}").exists() for n in NAMES):
        time.sleep(0.1)
    pids = {n: int((tmp_path / f"pid-{n}").read_text()) for n in NAMES}
    assert all(_alive(p) for p in pids.values())
    assert os.getpgid(pids["quant"]) != os.getpgid(pids["desk"])          # the production layout
    return proc, pids


def _all_gone(pids, timeout=10) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(p) for p in pids.values()):
        time.sleep(0.1)
    return [n for n, p in pids.items() if _alive(p)]


def _cleanup(pids):
    for p in pids.values():
        try:
            os.killpg(p, 9)
        except (ProcessLookupError, PermissionError):
            pass


posix = pytest.mark.skipif(os.name == "nt", reason="the POSIX process-group path; Windows uses taskkill /T")


@posix
def test_cancel_from_the_dashboard_stops_every_team_and_its_calls(tmp_path):
    proc, pids = _start(tmp_path, handler=False)
    try:
        assert len(owned(tmp_path)) == 2
        kill_tree(proc, grace=2, groups=owned(tmp_path))                  # what cancel_run does
        assert _all_gone(pids) == []
    finally:
        _cleanup(pids)


@posix
def test_the_desk_stops_its_own_teams_when_it_is_terminated(tmp_path):
    # Without the run folder record: only the desk's group is signalled, and its handler does the rest.
    proc, pids = _start(tmp_path, handler=True)
    try:
        kill_tree(proc, grace=5)
        assert _all_gone(pids) == []
    finally:
        _cleanup(pids)


@posix
def test_teams_are_stopped_after_the_desk_died_abruptly(tmp_path):
    proc, pids = _start(tmp_path, handler=True)
    try:
        os.kill(pids["desk"], 9)                       # no handler runs on SIGKILL
        proc.wait(timeout=10)
        assert _alive(pids["quant"]) and _alive(pids["vets-call"])
        kill_tree(proc, grace=2, groups=owned(tmp_path))
        assert _all_gone(pids) == []
        kill_tree(proc, grace=0.5, groups=owned(tmp_path))                # a second cancel is harmless
    finally:
        _cleanup(pids)


def test_a_finished_runner_leaves_the_record(tmp_path):
    from desk.procs import register, unregister
    p = subprocess.Popen([sys.executable, "-c", "pass"], **tree_kwargs())
    register(tmp_path, p)
    assert owned(tmp_path) == [p.pid]
    p.wait(timeout=30)
    unregister(tmp_path, p)
    assert owned(tmp_path) == [] and (tmp_path / GROUPS_FILE).exists()


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
