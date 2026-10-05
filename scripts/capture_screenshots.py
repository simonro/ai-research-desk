"""Retake the README screenshots from a dashboard loaded with the SYNTHETIC demo run.

    python scripts/demo_seed.py --memos <throwaway folder>
    DESK_MEMOS_DIR=<that folder> python dashboard/server.py --port 8793 --no-browser
    python scripts/capture_screenshots.py

Never point it at real runs. Writes docs/screenshot-<name>.png; open every image afterwards.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs"
BASE = "http://localhost:8793"
W = 1560
PAGES = [
    ("call", "/?t=DEMO&d=2026-10-02&h=long_term", ".fs-card", 1500),
    ("weekly", "/?view=week", ".fs-wk", 700),
    ("brief", "/?t=DEMO&d=2026-10-02&print=brief", ".brief2", 1370),
]


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": 1000}, device_scale_factor=2)
        for name, path, ready, height in PAGES:
            page.set_viewport_size({"width": W if name != "brief" else 900, "height": height})
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_selector(ready, timeout=20000)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / f"screenshot-{name}.png"))
            print("saved", name)
        browser.close()


if __name__ == "__main__":
    main()
