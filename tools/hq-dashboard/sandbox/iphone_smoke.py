# Opens the sandbox in WebKit (Safari's engine) as an iPhone 15 and saves a screenshot per tab.
# Needs: pip install playwright, python -m playwright install webkit,
# and the sandbox served on 127.0.0.1:8799 (see build_sandbox.py). Run: python -I tools/hq-dashboard/sandbox/iphone_smoke.py OUTDIR
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8799/index.html"
out = Path(sys.argv[1] if len(sys.argv) > 1 else "work/sandbox/shots")
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.webkit.launch()
    ctx = b.new_context(**p.devices["iPhone 15"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_timeout(1500)
    for tab in ("overview", "board", "list", "contacts", "search", "review"):
        page.tap(f'.seg [data-v="{tab}"]')
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"{tab}.png"), full_page=False)
    print("viewport", page.viewport_size, "scrollWidth", page.evaluate("document.documentElement.scrollWidth"), "errors", errors)
    b.close()
