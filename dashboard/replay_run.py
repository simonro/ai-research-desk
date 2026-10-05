"""Play a recorded run's event log back into a fresh file, fast, to test the live view for free.

    python replay_run.py SRC_EVENTS DEST_EVENTS [--speed 12] [--fail-at N]

The server uses this instead of the desk when DESK_REPLAY is set (see server.py). Timing
keeps the original gaps divided by --speed. --fail-at N stops after N events with exit code 1,
which is how the failure banner is tested. Nothing here calls a model or the broker.
"""

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dest")
    ap.add_argument("--speed", type=float, default=12)
    ap.add_argument("--fail-at", type=int, default=0)
    args = ap.parse_args()
    rows = [json.loads(line) for line in Path(args.src).read_text(encoding="utf-8").splitlines() if line.strip()]
    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("", encoding="utf-8")
    start, last = time.time(), 0.0
    for i, row in enumerate(rows, 1):
        if args.fail_at and i > args.fail_at:
            print("replay: simulated failure (DeskError: TradingAgents failed, exit 1)", flush=True)
            return 1
        at = float(row.get("at") or 0)
        time.sleep(max(0.0, (at - last) / args.speed))
        last = at
        row["at"] = round(time.time() - start, 2) * args.speed      # keep the recorded clock scale
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
