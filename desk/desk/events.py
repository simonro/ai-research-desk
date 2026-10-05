"""The run's event log: one JSON line per thing that happens, written the moment it happens.

The desk and both engine runners append to the same file, so a watcher (the dashboard's live
mode, a terminal tail, anything) sees the run unfold instead of waiting for the memo. The
runners are separate processes in other virtualenvs, so they pick the path up from the
DESK_EVENTS environment variable and append with their own handle; one line per write keeps
interleaved writes readable.

    log = EventLog.start(run_dir / "events.jsonl", ticker=..., plan=[...])
    log.emit("agent_done", who="Chartzilla", team="tape", text="RSI 36.5", call="bearish")
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ENV_VAR = "DESK_EVENTS"


class EventLog:
    """Append-only JSONL writer. Never raises: a broken log must not kill a run."""

    def __init__(self, path: Path | None, started: float | None = None):
        self.path = Path(path) if path else None
        self.started = started or time.time()
        self.seq = 0
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def start(cls, path: Path, **meta) -> "EventLog":
        path = Path(path)
        path.write_text("", encoding="utf-8")            # a fresh run starts a fresh log
        log = cls(path)
        log.emit("run_started", **meta)
        return log

    @classmethod
    def from_env(cls) -> "EventLog":
        """Used inside the engine runners, which inherit the path from the desk process."""
        return cls(os.environ.get(ENV_VAR) or None)

    @property
    def env(self) -> dict[str, str]:
        return {ENV_VAR: str(self.path)} if self.path else {}

    def emit(self, kind: str, **data) -> None:
        if not self.path:
            return
        self.seq += 1
        row = {"seq": self.seq, "at": round(time.time() - self.started, 2),
               "clock": time.strftime("%H:%M:%S"), "type": kind,
               **{k: v for k, v in data.items() if v is not None}}
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
                fh.flush()
        except OSError:
            pass
