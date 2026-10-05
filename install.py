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
# The TradingAgents commit this release is tested on (also pinned as the git submodule).
TRADINGAGENTS_URL = "https://github.com/TauricResearch/TradingAgents.git"
TRADINGAGENTS_COMMIT = "dffff22951ce3b57ef6ebc127558f6014167b5a2"
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


def ensure_tradingagents() -> None:
    ta = ROOT / "engines" / "TradingAgents"
    if not (ta / "pyproject.toml").exists():
        # A git clone has it as a submodule; a ZIP download has an empty folder, so it is cloned
        # from the original project and checked out at the exact commit this release was tested on.
        if (ROOT / ".git").exists():
            print("Fetching TradingAgents (git submodule)...")
            subprocess.run(["git", "submodule", "update", "--init"], cwd=ROOT, check=True)
        else:
            print(f"Fetching TradingAgents at {TRADINGAGENTS_COMMIT[:7]}...")
            if ta.exists() and not any(ta.iterdir()):
                ta.rmdir()
            subprocess.run(["git", "clone", "--quiet", TRADINGAGENTS_URL, str(ta)], check=True)
            subprocess.run(["git", "-C", str(ta), "checkout", "--quiet", TRADINGAGENTS_COMMIT], check=True)


def main() -> None:
    ensure_tradingagents()
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
