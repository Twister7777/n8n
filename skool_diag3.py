#!/usr/bin/env python3
"""
Skool Struktur-Diagnose (Runde 3)
Öffnet einen Kurs im Classroom und protokolliert:
  - die Modul-/Lektionsstruktur (pageProps['course'])
  - alle Netzwerk-Anfragen, die beim Laden einer Lektion mit Video passieren
Lädt nichts herunter.

    python skool_diag3.py --email du@mail.com --password geheim --community second-brain
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


def skeleton(obj, depth=0, max_depth=8):
    if depth > max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: skeleton(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [skeleton(x, depth + 1, max_depth) for x in obj[:5]]
        if len(obj) > 5:
            out.append(f"…(+{len(obj) - 5} weitere, insgesamt {len(obj)})")
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
    p.add_argument("--course-id", default=None, help="Optional: bestimmte Kurs-ID testen")
    args = p.parse_args()
    slug = args.community

    network_log = []

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

        def log_request(req):
            url = req.url
            if any(k in url.lower() for k in [
                "video", "mux", "vimeo", "wistia", "stream", "m3u8", "mp4", ".ts",
                "cloudfront", "cdn", "playback",
            ]):
                network_log.append(f"{req.method} {url}")

        page.on("request", log_request)

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

        # --- Classroom-Übersicht, um eine Kurs-ID zu bekommen ---
        await page.goto(f"{BASE}/{slug}/classroom", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        data = await get_next_data(page)
        pp = (data or {}).get("props", {}).get("pageProps", {})
        courses = pp.get("allCourses") or []

        course_id = args.course_id or (courses[0]["id"] if courses else None)
        if not course_id:
            print("Kein Kurs gefunden, Abbruch.")
            await browser.close()
            return

        course_title = next((c["metadata"]["title"] for c in courses if c["id"] == course_id), course_id)
        print(f"\nÖffne Kurs: {course_title} ({course_id})")

        # --- Kurs öffnen ---
        course_url = f"{BASE}/{slug}/classroom/{course_id}"
        await page.goto(course_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        cdata = await get_next_data(page)
        cpp = (cdata or {}).get("props", {}).get("pageProps", {})
        course_obj = cpp.get("course")

        text = json.dumps(skeleton(course_obj), ensure_ascii=False, indent=2)
        (OUT / "struct_course_detail.json").write_text(text, encoding="utf-8")
        print("\n----- pageProps['course'] (Struktur) -----")
        print(text[:8000])
        if len(text) > 8000:
            print("…(gekürzt, vollständig in skool_diag_output/struct_course_detail.json)")

        # --- Versuchen, ein Modul mit Video zu öffnen (per Klick), um Video-Requests zu sehen ---
        print("\n----- Klicke auf erstes Modul/Lektion, um Video-Requests zu sehen -----")
        try:
            first_item = await page.query_selector('[class*="ModuleItem"], [class*="lesson"], [class*="Module"] a, aside a')
            if first_item:
                await first_item.click(timeout=5000)
                await page.wait_for_timeout(5000)
            else:
                print("(kein klickbares Element gefunden, übersprungen)")
        except Exception as e:
            print(f"(Klick fehlgeschlagen: {e})")

        print(f"\nAktuelle URL: {page.url}")

        # __NEXT_DATA__ nach dem Klick erneut auslesen (falls Modul-Inhalt da reinkommt)
        cdata2 = await get_next_data(page)
        cpp2 = (cdata2 or {}).get("props", {}).get("pageProps", {})
        sel_mod = cpp2.get("selectedModule")
        if sel_mod:
            text2 = json.dumps(skeleton(sel_mod), ensure_ascii=False, indent=2)
            (OUT / "struct_selected_module.json").write_text(text2, encoding="utf-8")
            print("\n----- pageProps['selectedModule'] nach Klick -----")
            print(text2[:6000])

        print(f"\n----- Video-relevante Netzwerk-Anfragen ({len(network_log)}) -----")
        for line in network_log[:60]:
            print(line)
        (OUT / "network_log.txt").write_text("\n".join(network_log), encoding="utf-8")

        print("\n### FERTIG ### Bitte den gesamten Text oben an Claude schicken.")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
