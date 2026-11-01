#!/usr/bin/env python3
"""Screenshot + assert the Commerce colour-by mode and its map layers.

Checks the designation (commercial cells + BID outlines) and culture venue
layers at a zoom where their minzoom allows them to draw, reads visibility off
the style, and reports how many features the renderer actually drew so a
"visible but empty" layer fails the check.

Runs against the served build: scripts/serve.py (default :3118) or EXPLORER_URL.
Zoom override: ZOOM=13.2 SHOT_DIR=/tmp/x
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = os.environ.get("EXPLORER_URL", "http://127.0.0.1:3118/index.html")
OUT = Path(os.environ.get("SHOT_DIR", "/tmp/comm"))
ZOOM = float(os.environ.get("ZOOM", "12"))
CENTER = os.environ.get("CENTER", "-73.97,40.74")


def _chrome():
    for cand in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser",
                 "/usr/bin/chromium", "/snap/bin/chromium"):
        if Path(cand).exists():
            return cand
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lon, lat = [float(v) for v in CENTER.split(",")]
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch(
            executable_path=_chrome(),
            headless=True,
            args=["--enable-unsafe-swiftshader", "--use-gl=angle",
                  "--use-angle=swiftshader", "--no-sandbox"],
        )
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errs.append(f"console:{m.type}: {m.text}")
              if m.type == "error" else None)
        pg.goto(PAGE, wait_until="load")
        pg.wait_for_selector("#map .maplibregl-canvas", timeout=60000)
        pg.wait_for_timeout(6000)

        # Commerce colour-by: should turn the fabric layer on by itself.
        pg.click('button[data-v="comm"]')
        pg.wait_for_timeout(2500)
        pg.evaluate("([lon,lat,z]) => window.__map.jumpTo({center:[lon,lat],zoom:z})",
                    [lon, lat, ZOOM])
        pg.wait_for_timeout(6000)
        print("legend visible:", pg.is_visible("#keysComm"))
        print("showComm checked:", pg.eval_on_selector("#showComm", "e=>e.checked"))
        shot = OUT / "commerce.png"
        pg.screenshot(path=str(shot))
        print("shot:", shot)

        if not pg.eval_on_selector("#showCult", "e=>e.checked"):
            pg.click("#showCult")
            pg.wait_for_timeout(4000)
        vis = pg.evaluate("""() => {
          const m = window.__map;
          const v = id => m.getLayoutProperty(id, 'visibility');
          const n = id => { try { return m.queryRenderedFeatures({layers:[id]}).length; }
                            catch(e) { return 'err'; } };
          return {vis:{comm:v('comm-fill'), bids:v('bids-line'), cult:v('cult')},
                  drawn:{comm:n('comm-fill'), bids:n('bids-line'), cult:n('cult')},
                  zoom:m.getZoom()};
        }""")
        print("layer state:", vis)
        shot = OUT / "commerce_layers.png"
        pg.screenshot(path=str(shot))
        print("shot:", shot)
        print("page errors:", errs[:10])
        b.close()

    ok = (all(v == "visible" for v in vis["vis"].values())
          and all(isinstance(n, int) and n > 0 for n in vis["drawn"].values())
          and not errs)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
