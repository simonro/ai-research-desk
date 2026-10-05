#!/usr/bin/env sh
# Run the desk from the command line: scripts/run-desk.sh MSFT --own --engines quant,vets,edge
cd "$(dirname "$0")/../desk" && PYTHONUTF8=1 exec .venv/bin/python -m desk "$@"
