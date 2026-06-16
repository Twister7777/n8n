#!/usr/bin/env python3
"""
Skool Diagnose-Tool
Loggt sich ein, öffnet die Classroom- und Feed-Seite und schreibt auf,
in welchem Format Skool die Inhalte ausliefert. Damit kann der Downloader
passgenau angepasst werden. Es wird NICHTS heruntergeladen.

Verwendung:
    python skool_diag.py --email du@mail.com --password geheim --community second-brain
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

for pkg, mod in [("playwright", "playwright")]:
    try:
        __import__(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], check=True)

from playwright.async_api import async_playwright

BASE = "https://www.skool.com"
OUT = Path("skool_diag_output")
OUT.mkdir(exist_ok=True)


def section(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def inspect(page, label: str, url: str, slug: str):
    section(f"{label}: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)

    # Datei-Namen ohne Sonderzeichen
    fname = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")

    # --- HTML komplett speichern ---
    html = await page.content()
    (OUT / f"{fname}.html").write_text(html, encoding="utf-8")
    print(f"• HTML gespeichert ({len(html):,} Zeichen) → skool_diag_output/{fname}.html")

    # --- Screenshot ---
    try:
        await page.screenshot(path=str(OUT / f"{fname}.png"), full_page=True)
        print(f"• Screenshot → skool_diag_output/{fname}.png")
    except Exception:
        pass

    # --- __NEXT_DATA__ (Next.js Pages Router) ---
    next_data = await page.evaluate(
        "() => { const e = document.getElementById('__NEXT_DATA__');"
        " return e ? e.textContent : null; }"
    )
    if next_data:
        try:
            data = json.loads(next_data)
            (OUT / f"{fname}_nextdata.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            pp = data.get("props", {}).get("pageProps", {})
            print(f"• __NEXT_DATA__ gefunden. pageProps-Schlüssel: {list(pp.keys())}")
        except Exception as e:
            print(f"• __NEXT_DATA__ vorhanden, aber nicht lesbar: {e}")
    else:
        print("• __NEXT_DATA__: NICHT vorhanden")

    # --- __next_f Streaming-Chunks (Next.js App Router) ---
    next_f_count = await page.evaluate(
        "() => (window.__next_f ? window.__next_f.length : 0)"
    )
    print(f"• __next_f Streaming-Chunks: {next_f_count}")
    if next_f_count:
        chunks = await page.evaluate(
            "() => (window.__next_f || []).map(c => JSON.stringify(c))"
        )
        (OUT / f"{fname}_nextf.txt").write_text("\n".join(chunks), encoding="utf-8")
        print(f"  → gespeichert in skool_diag_output/{fname}_nextf.txt")

    # --- Alle Links zu dieser Community ---
    hrefs = await page.evaluate(
        "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))"
    )
    relevant = sorted({h for h in hrefs if h and f"/{slug}" in h})
    print(f"• Links mit /{slug} ({len(relevant)} Stück):")
    for h in relevant[:40]:
        print(f"    {h}")
    if len(relevant) > 40:
        print(f"    … und {len(relevant) - 40} weitere")


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

        section("Login")
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
        print(f"• Aktuelle URL nach Login: {page.url}")
        print(f"• Eingeloggt: {'/login' not in page.url}")

        await inspect(page, "Classroom", f"{BASE}/{slug}/classroom", slug)
        await inspect(page, "Feed", f"{BASE}/{slug}", slug)

        section("FERTIG")
        print("Alle Diagnose-Dateien liegen im Ordner: skool_diag_output/")
        print("Bitte den gesamten Terminal-Text oben kopieren und an Claude schicken.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
