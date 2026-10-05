"""Run both research desks for a symbol, in parallel, each in its own virtualenv."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from desk import DeskError
from desk.config import (AIHF_DIR, AIHF_PYTHON, AIHF_TIMEOUT_SECONDS, EDGE_DIR, EDGE_PYTHON,
                         EDGE_TIMEOUT_SECONDS, RUNNERS_DIR, TA_DIR, TA_LOGS, TA_PYTHON,
                         TA_REPORT_FILES, TA_TIMEOUT_SECONDS)
from desk.events import EventLog
from desk.ratings import normalize

_RATING_RE = re.compile(r"\*\*Rating\*\*:\s*\**\s*([A-Za-z]+)")


ENGINES = ("quant", "vets", "edge")            # the names the CLI and the dashboard use
CODE = {"quant": "A", "vets": "B", "edge": "C"}


def run_selected(engines: list[str], ticker: str, as_of: str, mandate: str, run_dir: Path,
                 fresh: bool, say=print, events: EventLog | None = None) -> dict[str, dict]:
    """Run the chosen engines in parallel. Returns {desk code: payload}, in A, B, C order."""
    run_dir.mkdir(parents=True, exist_ok=True)
    events = events or EventLog(None)
    jobs = {"quant": lambda: _tradingagents(ticker, as_of, run_dir, fresh, say, events),
            "vets": lambda: _ai_hedge_fund(ticker, as_of, mandate, run_dir, fresh, say, events),
            "edge": lambda: _edge_desk(ticker, as_of, run_dir, fresh, say, events)}
    chosen = [e for e in ENGINES if e in engines]
    with ThreadPoolExecutor(max_workers=len(chosen) or 1) as pool:
        futures = {e: pool.submit(jobs[e]) for e in chosen}
        return {CODE[e]: f.result() for e, f in futures.items()}


def _edge_desk(ticker: str, as_of: str, run_dir: Path, fresh: bool, say, events) -> dict:
    out = run_dir / "edge-desk.json"
    if out.exists() and not fresh:
        say(f"  Edge Desk: reusing this run's saved report ({out.name})")
        payload = {**_load(out, allow_no_rating=True), "reused": True}
        events.emit("engine_reused", engine="edge", rating=payload["rating"], text="Reusing today's report")
        return payload
    if not EDGE_PYTHON.exists():
        raise DeskError(f"Edge Desk was not found at {EDGE_DIR} (set EDGE_DESK_DIR if it moved)")
    say("  Edge Desk: running (about 10 minutes with its written analysis and research layer)...")
    events.emit("engine_started", engine="edge", text="Edge Desk is building its evidence package")
    payload = _run(EDGE_PYTHON, EDGE_DIR, [RUNNERS_DIR / "edge_runner.py", ticker, as_of, out],
                   out, run_dir / "edge-desk.log", EDGE_TIMEOUT_SECONDS, "Edge Desk", say, events,
                   allow_no_rating=True)
    events.emit("engine_done", engine="edge", rating=payload["rating"],
                text=f"Edge Desk: {payload['rating'] or 'rating withheld'}")
    return payload


def _tradingagents(ticker: str, as_of: str, run_dir: Path, fresh: bool, say, events) -> dict:
    out = run_dir / "tradingagents.json"
    if not fresh:
        reused = None
        if out.exists():
            say(f"  TradingAgents: reusing this run's saved report ({out.name})")
            reused = {**_load(out), "reused": True}
        else:
            cli_reports = TA_LOGS / ticker / as_of / "reports"
            if (cli_reports / "final_trade_decision.md").exists():
                say(f"  TradingAgents: reusing today's CLI run ({cli_reports})")
                out.write_text(json.dumps(load_cli_reports(ticker, as_of, cli_reports), indent=2),
                               encoding="utf-8")
                reused = {**_load(out), "reused": True}
        if reused:
            events.emit("engine_reused", engine="tape", rating=reused["rating"],
                        text="Reusing today's report")
            return reused
    say("  TradingAgents: running (about 8 minutes)...")
    events.emit("engine_started", engine="tape", text="The quant desk is working the tape")
    payload = _run(TA_PYTHON, TA_DIR, [RUNNERS_DIR / "ta_runner.py", ticker, as_of, out],
                   out, run_dir / "tradingagents.log", TA_TIMEOUT_SECONDS, "TradingAgents", say, events)
    events.emit("engine_done", engine="tape", rating=payload["rating"],
                text=f"Portfolio Manager: {payload['rating']}")
    return payload


def _ai_hedge_fund(ticker: str, as_of: str, mandate: str, run_dir: Path, fresh: bool,
                   say, events) -> dict:
    out = run_dir / "ai-hedge-fund.json"
    if out.exists() and not fresh:
        say(f"  ai-hedge-fund: reusing this run's saved report ({out.name})")
        payload = {**_load(out), "reused": True}
        events.emit("engine_reused", engine="value", rating=payload["rating"],
                    text="Reusing today's report")
        return payload
    say(f"  ai-hedge-fund: running fund '{mandate}' (about 1 minute)...")
    events.emit("engine_started", engine="value", text="The veterans are reading the filings")
    payload = _run(AIHF_PYTHON, AIHF_DIR, [RUNNERS_DIR / "aihf_runner.py", ticker, as_of, mandate, out],
                   out, run_dir / "ai-hedge-fund.log", AIHF_TIMEOUT_SECONDS, "ai-hedge-fund", say, events)
    events.emit("engine_done", engine="value", rating=payload["rating"],
                text=f"Research Manager: {payload['rating']}")
    return payload


def load_cli_reports(ticker: str, as_of: str, reports_dir: Path) -> dict:
    reports = {}
    for name in TA_REPORT_FILES:
        path = reports_dir / f"{name}.md"
        reports[name] = path.read_text(encoding="utf-8") if path.exists() else ""
    match = _RATING_RE.search(reports["final_trade_decision"])
    return {"engine": "tradingagents", "ticker": ticker, "date": as_of,
            "rating": match.group(1) if match else None, "reports": reports,
            "usage": None, "reused": True, "source": str(reports_dir)}


def _run(python: Path, cwd: Path, args: list, out: Path, log: Path, timeout: int,
         name: str, say, events: EventLog | None = None, allow_no_rating: bool = False) -> dict:
    started = time.time()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **(events.env if events else {})}
    with open(log, "w", encoding="utf-8") as log_file:
        try:
            proc = subprocess.run([str(python), *map(str, args)], cwd=cwd, env=env,
                                  stdout=log_file, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise DeskError(f"{name} timed out after {timeout // 60} minutes (log: {log})") from exc
    if proc.returncode != 0 or not out.exists():
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        raise DeskError(f"{name} failed (exit {proc.returncode}). Last log lines:\n{tail}")
    say(f"  {name}: done in {time.time() - started:.0f}s")
    return _load(out, allow_no_rating)


def _load(path: Path, allow_no_rating: bool = False) -> dict:
    """Edge Desk may withhold its rating on bad data; that is an answer, not a failure."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rating = normalize(payload.get("rating"))
    if rating is None and not allow_no_rating:
        raise DeskError(f"{payload.get('engine')} produced no usable rating "
                        f"({payload.get('rating')!r}) in {path}")
    payload["rating"] = rating
    return payload
