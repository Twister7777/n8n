#!/usr/bin/env python3
"""
Skool Struktur-Diagnose (Runde 5)
Schneidet ALLE XHR/Fetch-Anfragen auf der Lektionsseite mit und durchsucht
die Antworten nach der Mux-Playback-ID bzw. nach Tokens, um die Stelle zu
finden, an der die echte Video-Stream-URL erzeugt wird. Lädt nichts herunter.

    python skool_diag5.py --email du@mail.com --password geheim \
        --community second-brain \
        --module-url "https://www.skool.com/second-brain/classroom/7d4c9c53?md=b75ffade14ee49aa81fb5d85379e0f74" \
        --playback-id t1HyG00hEJ6BNhaZ02sljoF355y023ZQa8O00PD64gcOlX4
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


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--community", required=True)
    p.add_argument("--module-url", required=True)
    p.add_argument("--playback-id", default=None)
    args = p.parse_args()

    all_xhr = []        # every xhr/fetch URL seen
    hits = []            # responses whose body mentions the playback id / a token

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

        async def on_response(resp):
            try:
                req = resp.request
                if req.resource_type not in ("xhr", "fetch"):
                    return
                all_xhr.append(f"{resp.status} {req.method} {resp.url}")
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype and "text" not in ctype:
                    return
                body = await resp.text()
                needle_hit = False
                if args.playback_id and args.playback_id in body:
                    needle_hit = True
                if "video.skool.com" in body or '"token"' in body or "eyJ" in body:
                    needle_hit = True
                if needle_hit:
                    snippet = body if len(body) < 6000 else body[:6000] + "…(gekürzt)"
                    hits.append(f"{resp.url}\n{snippet}")
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

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

        print(f"\nÖffne Lektion: {args.module_url}")
        await page.goto(args.module_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)

        # Versuche aktiv das Video abzuspielen
        for sel in ['video', 'button[aria-label*="Play"]', '[class*="play"]']:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click(timeout=3000)
                    print(f"  (geklickt: {sel})")
                    await page.wait_for_timeout(5000)
            except Exception:
                continue

        await page.wait_for_timeout(3000)

        print(f"\n----- Alle XHR/Fetch-URLs ({len(all_xhr)}) -----")
        for line in all_xhr:
            print(line)

        print(f"\n----- Treffer mit Token/Playback-ID ({len(hits)}) -----")
        for h in hits:
            print(h)
            print("---")

        (OUT / "diag5_all_xhr.txt").write_text("\n".join(all_xhr), encoding="utf-8")
        (OUT / "diag5_hits.txt").write_text("\n\n===\n\n".join(hits), encoding="utf-8")

        print("\n### FERTIG ### Bitte den gesamten Text oben an Claude schicken.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
