#!/usr/bin/env python3
"""Diagnose a blank map: report console errors, style/source state, canvas size.

Usage: EXPLORER_URL=... /usr/bin/python3 scripts/check_blank.py
"""
import json
import os

from playwright.sync_api import sync_playwright

URL = os.environ.get("EXPLORER_URL", "http://127.0.0.1:3021/index.html")
SHOT = os.environ.get("SHOT", "/tmp/blank/diag.png")
BLOCK = os.environ.get("BLOCK_HOST", "")  # e.g. tiles.openfreemap.org


def main():
    os.makedirs(os.path.dirname(SHOT), exist_ok=True)
    logs = []
    with sync_playwright() as p:
        b = p.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            args=[
                "--enable-unsafe-swiftshader",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--no-sandbox",
            ],
        )
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        if BLOCK:
            pg.route(f"**/*{BLOCK}*/**", lambda r: r.abort())
            pg.route(f"**{BLOCK}**", lambda r: r.abort())
        pg.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:300]}"))
        pg.on("pageerror", lambda e: logs.append(f"[pageerror] {str(e)[:400]}"))
        pg.on(
            "requestfailed",
            lambda r: logs.append(f"[reqfail] {r.url[:120]} :: {r.failure}"),
        )
        try:
            pg.goto(URL, wait_until="load", timeout=90000)
        except Exception as e:  # noqa: BLE001
            logs.append(f"[goto] {e}")
        pg.wait_for_timeout(12000)

        state = pg.evaluate(
            """() => {
              const m = window.__map;
              const out = {map: m ? 'window.__map' : 'missing',
                           boot: !!document.getElementById('bootmsg'),
                           bootmsg: (document.getElementById('bootmsg')||{}).textContent || null,
                           bootErrors: window.__bootErrors || []};
              if (!m) return out;
              try {
                const st = m.getStyle();
                out.style_loaded = !!st;
                out.layers = (st.layers || []).length;
                out.sources = Object.keys(st.sources || {});
                out.canvas = m.getCanvas().width + 'x' + m.getCanvas().height;
                out.zoom = m.getZoom();
                out.loaded = m.loaded();
                out.tracts = st.sources.tracts ? (m.querySourceFeatures('tracts').length) : null;
                const r = m.getCanvas().getBoundingClientRect();
                out.canvas_css = Math.round(r.width) + 'x' + Math.round(r.height);
              } catch (e) { out.err = String(e); }
              return out;
            }"""
        )
        print("state:", json.dumps(state, default=str)[:1200])
        print("webgl:", pg.evaluate("() => { const c=document.createElement('canvas'); return !!(c.getContext('webgl2')||c.getContext('webgl')); }"))
        pg.screenshot(path=SHOT)
        print("shot:", SHOT)
        b.close()

    print("--- console ---")
    for line in logs[-40:]:
        print(line)
    print("errors:", sum(1 for line in logs if "pageerror" in line or "reqfail" in line))


if __name__ == "__main__":
    main()
