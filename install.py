"""Set up ai-research-desk: one virtual environment per part, settings, and the default fund.

    python install.py            (Python 3.12 is used for the environments; see the README)
    python install.py --dev      also installs pytest in every environment, to run the tests

Each part has its own environment because their libraries conflict (ai-hedge-fund pins
numpy < 2, TradingAgents pulls LangChain). The desk drives them as separate processes.
Safe to run again: existing environments are updated, existing settings are left alone.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME_DESK = Path.home() / ".hedge-desk"
PARTS = [
    ("desk", ROOT / "desk", ["-r", "requirements.txt"]),
    ("Edge Desk", ROOT / "edgedesk", ["-e", "."]),
    ("ai-hedge-fund", ROOT / "engines" / "ai-hedge-fund", ["-e", "."]),
    ("TradingAgents", ROOT / "engines" / "TradingAgents", ["-e", "."]),
]


def python312() -> list[str]:
    """A Python 3.12 to build the environments with. On Windows prefer the signed python.org
    build through the `py` launcher: Smart App Control blocks unsigned interpreters."""
    candidates = [["py", "-3.12"]] if sys.platform == "win32" else []
    candidates += [["python3.12"], [sys.executable]]
    for cmd in candidates:
        if not shutil.which(cmd[0]) and cmd[0] != sys.executable:
            continue
        try:
            out = subprocess.run([*cmd, "-c", "import sys; print(sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0 and "(3, 12)" in out.stdout:
            return cmd
    sys.exit("Python 3.12 is required (ai-hedge-fund pins numpy < 2). Install it from python.org.")


def venv_python(folder: Path) -> Path:
    return folder / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main() -> None:
    if not (ROOT / "engines" / "TradingAgents" / "pyproject.toml").exists():
        print("Fetching TradingAgents (git submodule)...")
        subprocess.run(["git", "submodule", "update", "--init"], cwd=ROOT, check=True)
    py = python312()
    for name, folder, spec in PARTS:
        print(f"\n== {name}")
        if not venv_python(folder).exists():
            subprocess.run([*py, "-m", "venv", str(folder / ".venv")], check=True)
        pip = [str(venv_python(folder)), "-m", "pip", "install", "--disable-pip-version-check", "-q"]
        subprocess.run([*pip, "--upgrade", "pip"], cwd=folder, check=True)
        subprocess.run([*pip, *spec], cwd=folder, check=True)
        if "--dev" in sys.argv:
            subprocess.run([*pip, "pytest"], cwd=folder, check=True)

    HOME_DESK.mkdir(parents=True, exist_ok=True)
    settings = HOME_DESK / ".env"
    if not settings.exists():
        shutil.copy(ROOT / ".env.example", settings)
        print(f"\nSettings written to {settings}: open it and fill in SEC_USER_AGENT (and Alpaca keys if you have them).")
    mandates = Path.home() / ".hedge-fund" / "mandates"
    mandates.mkdir(parents=True, exist_ok=True)
    for src in (ROOT / "config" / "mandates").glob("*.yaml"):
        if not (mandates / src.name).exists():
            shutil.copy(src, mandates / src.name)

    print("\nDone. Next:")
    print("  1. Log in to your plan once: `claude` (Claude plan), or see the README for the ChatGPT plan.")
    print("  2. Start the dashboard: scripts/run-dashboard (.bat on Windows, .sh elsewhere).")


if __name__ == "__main__":
    main()
