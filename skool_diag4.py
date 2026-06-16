#!/usr/bin/env python3
"""
Skool Struktur-Diagnose (Runde 4)
Öffnet eine konkrete Lektion mit Video und protokolliert ALLE Netzwerk-
Anfragen + (bei JSON-Antworten) deren Inhalt, um herauszufinden, wie die
Skool-interne videoId in eine abspielbare Stream-URL übersetzt wird.
Lädt nichts herunter.

    python skool_diag4.py --email du@mail.com --password geheim \
        --community second-brain \
        --module-url "https://www.skool.com/second-brain/classroom/7d4c9c53?md=b75ffade14ee49aa81fb5d85379e0f74"
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

INTERESTING = ["video", "stream", "mux", "m3u8", "manifest", "cloudflare", "playback", "api.skool"]


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--community", required=True)
    p.add_argument("--module-url", required=True)
    args = p.parse_args()

    log_lines = []

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
            url = resp.url
            if not any(k in url.lower() for k in INTERESTING):
                return
            line = f"{resp.status} {resp.request.method} {url}"
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                try:
                    body = await resp.text()
                    if len(body) < 4000:
                        line += f"\n    BODY: {body}"
                    else:
                        line += f"\n    BODY (gekürzt): {body[:4000]}…"
                except Exception:
                    pass
            log_lines.append(line)

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
        await page.wait_for_timeout(5000)

        # Versuche aktiv auf einen Play-Button / das Video zu klicken
        for sel in [
            'button[aria-label*="Play"]', '[class*="play"]', 'video',
            'iframe', '[class*="Player"]', '[class*="player"]',
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click(timeout=3000)
                    print(f"  (geklickt: {sel})")
                    await page.wait_for_timeout(4000)
                    break
            except Exception:
                continue

        await page.wait_for_timeout(4000)

        # Alle iframe-srcs auf der Seite
        iframes = await page.eval_on_selector_all(
            "iframe", "els => els.map(e => e.src)"
        )
        print(f"\n----- iframe-srcs auf der Seite ({len(iframes)}) -----")
        for src in iframes:
            print(src)

        print(f"\n----- Interessante Netzwerk-Antworten ({len(log_lines)}) -----")
        for line in log_lines:
            print(line)
            print()

        (OUT / "diag4_network.txt").write_text(
            "IFRAMES:\n" + "\n".join(iframes) + "\n\nRESPONSES:\n" + "\n\n".join(log_lines),
            encoding="utf-8",
        )

        print("\n### FERTIG ### Bitte den gesamten Text oben an Claude schicken.")
        await page.wait_for_timeout(2000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
