#!/usr/bin/env sh
# The Desk dashboard on http://localhost:8790. Ctrl+C to stop it.
cd "$(dirname "$0")/.." && PYTHONUTF8=1 exec desk/.venv/bin/python dashboard/server.py "$@"
