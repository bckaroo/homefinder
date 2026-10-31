#!/usr/bin/env python3
"""Render check for the built explorer using SwiftShader (software WebGL).

The harness browser has no GL, and MapLibre needs a real GL context before it
will fire `load` — so verification runs here with --enable-unsafe-swiftshader.
Screens each travel mode and reports the qualifying tract count per mode.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "index.html"


def _chrome():
    """Prefer a system Chrome/Chromium: the bundled Playwright build and the
    installed package drift apart on this box, and a system binary sidesteps it."""
    for cand in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser",
                 "/usr/bin/chromium", "/snap/bin/chromium"):
        if Path(cand).exists():
            return cand
    return None


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/modes")
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=_chrome(),
            args=["--enable-unsafe-swiftshader", "--use-gl=angle",
                  "--use-angle=swiftshader", "--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(PAGE.as_uri())
        page.wait_for_function(
            "() => { try { return document.querySelectorAll('#tb tr').length > 0 } "
            "catch(e) { return false } }", timeout=60_000)
        for mode in ("transit", "cycle", "walk"):
            page.select_option("#mode", mode)
            page.wait_for_timeout(1200)
            rows = page.eval_on_selector_all("#tb tr", "els => els.length")
            shot = out / f"{mode}.png"
            page.screenshot(path=str(shot))
            print(f"{mode:8} rows={rows:<4} {shot}")
        print("page errors:", errors)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
