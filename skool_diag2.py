#!/usr/bin/env python3
"""
Skool Struktur-Diagnose (Runde 2)
Zeigt die genaue JSON-Struktur von Classroom (course/allCourses) und
Feed (postTrees) – mit gekürzten Texten, damit es übersichtlich bleibt.
Lädt nichts herunter.

    python skool_diag2.py --email du@mail.com --password geheim --community second-brain
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

try:
    import playwright  # noqa
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True)

from playwright.async_api import async_playwright

BASE = "https://www.skool.com"
OUT = Path("skool_diag_output")
OUT.mkdir(exist_ok=True)


def skeleton(obj, depth=0, max_depth=7):
    """Return a structure-only copy: strings truncated, lists capped at 3."""
    if depth > max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: skeleton(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [skeleton(x, depth + 1, max_depth) for x in obj[:3]]
        if len(obj) > 3:
            out.append(f"…(+{len(obj) - 3} weitere, insgesamt {len(obj)})")
        return out
    if isinstance(obj, str):
        return obj if len(obj) <= 100 else obj[:100] + f"…(+{len(obj) - 100})"
    return obj


async def get_next_data(page):
    raw = await page.evaluate(
        "() => { const e = document.getElementById('__NEXT_DATA__');"
        " return e ? e.textContent : null; }"
    )
    return json.loads(raw) if raw else None


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--community", required=True)
    args = p.parse_args()
    slug = args.community

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        # --- Login ---
        await page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        for sel in ['input[type="email"]', 'input[name="email"]']:
            try:
                await page.fill(sel, args.email, timeout=3000); break
            except Exception:
                pass
        for sel in ['input[type="password"]', 'input[name="password"]']:
            try:
                await page.fill(sel, args.password, timeout=3000); break
            except Exception:
                pass
        for sel in ['button[type="submit"]', 'button:has-text("Log in")']:
            try:
                await page.click(sel, timeout=3000); break
            except Exception:
                pass
        await page.wait_for_timeout(6000)
        print(f"Login OK: {'/login' not in page.url}")

        # --- Classroom ---
        print("\n### CLASSROOM-STRUKTUR ###")
        await page.goto(f"{BASE}/{slug}/classroom", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        data = await get_next_data(page)
        pp = (data or {}).get("props", {}).get("pageProps", {})

        for key in ("course", "allCourses", "selectedModule"):
            val = pp.get(key)
            skel = skeleton(val)
            text = json.dumps(skel, ensure_ascii=False, indent=2)
            (OUT / f"struct_{key}.json").write_text(text, encoding="utf-8")
            print(f"\n----- pageProps['{key}'] -----")
            print(text[:6000])
            if len(text) > 6000:
                print(f"…(gekürzt, vollständig in skool_diag_output/struct_{key}.json)")

        # --- Feed (ein Beispiel-Post) ---
        print("\n\n### FEED-STRUKTUR (ein Beispiel-Post) ###")
        await page.goto(f"{BASE}/{slug}", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        fdata = await get_next_data(page)
        fpp = (fdata or {}).get("props", {}).get("pageProps", {})
        trees = fpp.get("postTrees") or []
        print(f"Anzahl Posts in postTrees: {len(trees)}")
        if trees:
            skel = skeleton(trees[0])
            text = json.dumps(skel, ensure_ascii=False, indent=2)
            (OUT / "struct_post.json").write_text(text, encoding="utf-8")
            print("\n----- postTrees[0] -----")
            print(text[:6000])
            if len(text) > 6000:
                print("…(gekürzt, vollständig in skool_diag_output/struct_post.json)")

        print("\n### FERTIG ### Bitte den gesamten Text oben an Claude schicken.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
