#!/usr/bin/env python3
"""
Skool Community Content Downloader
Lädt alle Videos und Anhänge aus Skool-Communities herunter.

Verwendung:
    python skool_downloader.py --email deine@email.com --password deinpasswort
    python skool_downloader.py --email deine@email.com --password deinpasswort --community meine-community
    python skool_downloader.py --email deine@email.com --password deinpasswort --no-headless
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Dependency bootstrap
# ---------------------------------------------------------------------------

def _ensure(package: str, import_name: Optional[str] = None):
    mod = import_name or package.split("[")[0]
    try:
        __import__(mod)
    except ImportError:
        print(f"Installing {package}…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", package], check=True)


_ensure("playwright")
_ensure("yt-dlp", "yt_dlp")
_ensure("requests")

from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
)
import yt_dlp
import requests as http_requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKIP_SLUGS = {
    "feed", "login", "signup", "discover", "settings",
    "notifications", "profile", "checkout", "invite",
}

VIDEO_DOMAINS = [
    "vimeo.com", "player.vimeo.com",
    "wistia.com", "wistia.net", "fast.wistia",
    "loom.com",
    "youtube.com", "youtu.be",
    "mux.com", "stream.mux.com",
]

ATTACHMENT_EXTS = {
    ".pdf", ".zip", ".docx", ".xlsx", ".pptx",
    ".doc", ".xls", ".ppt", ".csv", ".mp3", ".wav",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
}


def sanitize(name: str) -> str:
    """Strip characters that are invalid in file/dir names."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip("._")[:180] or "unnamed"


def is_video_url(url: str) -> bool:
    for domain in VIDEO_DOMAINS:
        if domain in url:
            return True
    if re.search(r'\.(m3u8|mp4|mov|webm|mkv)(\?|$)', url, re.I):
        return True
    return False


def is_attachment_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in ATTACHMENT_EXTS)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class SkoolDownloader:
    BASE = "https://www.skool.com"

    def __init__(
        self,
        email: str,
        password: str,
        output_dir: str = "skool_downloads",
        community_slug: Optional[str] = None,
        headless: bool = True,
    ):
        self.email = email
        self.password = password
        self.output_dir = Path(output_dir)
        self.community_slug = community_slug
        self.headless = headless
        self._cookies: list = []
        self._done: set[str] = set()   # already-downloaded URLs

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.output_dir / "download.log"

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, msg: str):
        print(msg)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, page: Page) -> bool:
        self.log("→ Login…")
        await page.goto(f"{self.BASE}/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        # Fill email/password (Skool uses various selectors)
        for sel in ['input[type="email"]', 'input[name="email"]', "#email"]:
            try:
                await page.fill(sel, self.email, timeout=3000)
                break
            except Exception:
                continue

        for sel in ['input[type="password"]', 'input[name="password"]', "#password"]:
            try:
                await page.fill(sel, self.password, timeout=3000)
                break
            except Exception:
                continue

        for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Log in")', 'button:has-text("Sign in")', 'button:has-text("Continue")']:
            try:
                await page.click(sel, timeout=3000)
                break
            except Exception:
                continue

        try:
            await page.wait_for_url(
                lambda url: "/login" not in url and "/signup" not in url,
                timeout=20_000,
            )
            self.log(f"✓ Eingeloggt als {self.email}")
            return True
        except Exception:
            self.log("✗ Login fehlgeschlagen – bitte Credentials prüfen oder --no-headless nutzen")
            return False

    # ------------------------------------------------------------------
    # Community discovery
    # ------------------------------------------------------------------

    async def get_community_slugs(self, page: Page) -> list[str]:
        if self.community_slug:
            return [self.community_slug]

        self.log("→ Communities werden gesucht…")
        await page.goto(f"{self.BASE}/feed", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        slugs: set[str] = set()
        links = await page.query_selector_all("a[href]")
        for link in links:
            href = (await link.get_attribute("href")) or ""
            m = re.match(r"^/([a-z0-9][a-z0-9-]{1,60}[a-z0-9])(?:/|$)", href)
            if m and m.group(1) not in SKIP_SLUGS:
                slugs.add(m.group(1))

        self.log(f"✓ Communities gefunden: {list(slugs)}")
        return list(slugs)

    # ------------------------------------------------------------------
    # Classroom (Kurse)
    # ------------------------------------------------------------------

    async def get_classroom_posts(self, page: Page, slug: str) -> list[dict]:
        """Alle Lektionen/Module aus dem Classroom holen."""
        classroom_url = f"{self.BASE}/{slug}/classroom"
        self.log(f"  → Classroom: {classroom_url}")
        await page.goto(classroom_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        posts: list[dict] = []
        visited: set[str] = set()

        # Skool classroom sidebar links
        for sel in [
            f'a[href*="/{slug}/classroom"]',
            'a[href*="/classroom/"]',
            '[class*="lesson"] a',
            '[class*="module"] a',
        ]:
            links = await page.query_selector_all(sel)
            for link in links:
                href = (await link.get_attribute("href")) or ""
                full = urljoin(self.BASE, href)
                if full not in visited and "/classroom" in href:
                    visited.add(full)
                    text_el = await link.query_selector("span, p, div, h3")
                    title = (await text_el.inner_text()).strip() if text_el else href.split("/")[-1]
                    posts.append({"url": full, "title": title or f"lesson_{len(posts)+1}"})

        self.log(f"  ✓ {len(posts)} Lektionen gefunden")
        return posts

    # ------------------------------------------------------------------
    # Feed posts
    # ------------------------------------------------------------------

    async def get_feed_posts(self, page: Page, slug: str) -> list[dict]:
        """Posts aus dem Community-Feed holen."""
        self.log(f"  → Feed: {self.BASE}/{slug}")
        await page.goto(f"{self.BASE}/{slug}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Scroll to load more
        for _ in range(8):
            await page.keyboard.press("End")
            await page.wait_for_timeout(1200)

        posts: list[dict] = []
        visited: set[str] = set()
        links = await page.query_selector_all(f'a[href*="/{slug}/p/"]')
        for link in links:
            href = (await link.get_attribute("href")) or ""
            full = urljoin(self.BASE, href)
            if full not in visited:
                visited.add(full)
                posts.append({"url": full, "title": href.split("/p/")[-1].split("/")[0]})

        self.log(f"  ✓ {len(posts)} Feed-Posts gefunden")
        return posts

    # ------------------------------------------------------------------
    # Content extraction per post/lesson
    # ------------------------------------------------------------------

    async def process_post(self, page: Page, post: dict, output_dir: Path):
        title = post["title"]
        safe = sanitize(title)
        post_dir = output_dir / safe
        post_dir.mkdir(parents=True, exist_ok=True)

        self.log(f"    · {title}")

        intercepted_videos: list[str] = []

        def _on_request(req):
            if is_video_url(req.url):
                intercepted_videos.append(req.url)

        page.on("request", _on_request)

        try:
            await page.goto(post["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # --- iframes (Vimeo, Wistia, YouTube, Loom) ---
            iframes = await page.query_selector_all("iframe[src]")
            for iframe in iframes:
                src = (await iframe.get_attribute("src")) or ""
                if src and is_video_url(src):
                    intercepted_videos.append(src)

            # --- data-video-id (Wistia) ---
            wistia_els = await page.query_selector_all("[data-video-id], [class*='wistia']")
            for el in wistia_els:
                vid = (await el.get_attribute("data-video-id")) or ""
                if vid:
                    intercepted_videos.append(f"https://fast.wistia.com/embed/medias/{vid}")

            # --- <video> tags ---
            for sel in ["video[src]", "video > source[src]"]:
                for el in await page.query_selector_all(sel):
                    src = (await el.get_attribute("src")) or ""
                    if src:
                        intercepted_videos.append(src)

            # --- Dateianhänge ---
            all_links = await page.query_selector_all("a[href]")
            for link in all_links:
                href = (await link.get_attribute("href")) or ""
                if not href:
                    continue
                full = urljoin(self.BASE, href)
                if is_attachment_url(full):
                    text = (await link.inner_text()).strip() or href.split("/")[-1]
                    await self._download_file(full, post_dir, text, page)

        finally:
            page.remove_listener("request", _on_request)

        # Download unique videos
        unique_videos = list(dict.fromkeys(intercepted_videos))
        for i, vurl in enumerate(unique_videos):
            if vurl in self._done:
                continue
            self._done.add(vurl)
            self.log(f"      ▶ Video {i+1}: {vurl[:90]}")
            await asyncio.to_thread(
                self._ytdlp_download,
                vurl,
                str(post_dir),
                safe,
            )

    # ------------------------------------------------------------------
    # yt-dlp download
    # ------------------------------------------------------------------

    def _ytdlp_download(self, url: str, output_dir: str, name_hint: str):
        cookie_file = self._write_cookies()

        ydl_opts: dict = {
            "outtmpl": str(Path(output_dir) / f"%(autonumber)s_%(title)s.%(ext)s"),
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "quiet": False,
            "ignoreerrors": True,
            "no_warnings": False,
            "retries": 3,
            "fragment_retries": 5,
        }

        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file

        # Vimeo: pass referrer so private embeds work
        if "vimeo" in url:
            ydl_opts["http_headers"] = {"Referer": self.BASE}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                ydl.download([url])
            except Exception as e:
                self.log(f"        ✗ yt-dlp: {e}")

    def _write_cookies(self) -> Optional[str]:
        if not self._cookies:
            return None
        path = str(self.output_dir / "cookies.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in self._cookies:
                domain = c.get("domain", "")
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                path_ = c.get("path", "/")
                secure = "TRUE" if c.get("secure", False) else "FALSE"
                expires = int(c.get("expires") or 0)
                f.write(f"{domain}\t{flag}\t{path_}\t{secure}\t{expires}\t{c['name']}\t{c['value']}\n")
        return path

    # ------------------------------------------------------------------
    # Direct file download
    # ------------------------------------------------------------------

    async def _download_file(self, url: str, output_dir: Path, name: str, page: Page):
        safe = sanitize(name or url.split("/")[-1].split("?")[0])
        ext = Path(urlparse(url).path).suffix
        if ext and not safe.endswith(ext):
            safe += ext
        dest = output_dir / safe

        if dest.exists():
            return

        self.log(f"      ⬇ Datei: {safe}")
        cookies = await page.context.cookies()
        sess = http_requests.Session()
        for c in cookies:
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

        try:
            r = sess.get(url, stream=True, timeout=60)
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
            self.log(f"      ✓ {dest}")
        except Exception as e:
            self.log(f"      ✗ {url}: {e}")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            if not await self.login(page):
                await browser.close()
                return

            self._cookies = await context.cookies()

            slugs = await self.get_community_slugs(page)
            if not slugs:
                self.log("Keine Communities gefunden.")
                await browser.close()
                return

            for slug in slugs:
                self.log(f"\n{'='*60}")
                self.log(f"Community: {slug}")
                self.log("=" * 60)
                comm_dir = self.output_dir / sanitize(slug)
                comm_dir.mkdir(parents=True, exist_ok=True)

                # --- Classroom / Kurse ---
                classroom_dir = comm_dir / "classroom"
                classroom_dir.mkdir(exist_ok=True)
                classroom_posts = await self.get_classroom_posts(page, slug)
                for post in classroom_posts:
                    await self.process_post(page, post, classroom_dir)
                    await asyncio.sleep(1)

                # --- Feed-Posts ---
                feed_dir = comm_dir / "feed"
                feed_dir.mkdir(exist_ok=True)
                feed_posts = await self.get_feed_posts(page, slug)
                for post in feed_posts:
                    await self.process_post(page, post, feed_dir)
                    await asyncio.sleep(1)

                self.log(f"✓ Community {slug} abgeschlossen")

            await browser.close()
            self.log(f"\n{'='*60}")
            self.log(f"Download abgeschlossen → {self.output_dir.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skool Videos & Dateien herunterladen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  # Alle Communities herunterladen
  python skool_downloader.py --email du@mail.com --password geheim

  # Nur eine bestimmte Community
  python skool_downloader.py --email du@mail.com --password geheim --community meine-gruppe

  # Browser sichtbar machen (z.B. bei 2FA oder CAPTCHA)
  python skool_downloader.py --email du@mail.com --password geheim --no-headless
        """,
    )
    parser.add_argument("--email",      required=True,  help="Skool E-Mail-Adresse")
    parser.add_argument("--password",   required=True,  help="Skool Passwort")
    parser.add_argument("--output",     default="skool_downloads", help="Ausgabeordner (Standard: skool_downloads)")
    parser.add_argument("--community",  default=None,   help="Nur diese Community (Slug aus der URL)")
    parser.add_argument("--no-headless", action="store_true", help="Browser-Fenster anzeigen")
    args = parser.parse_args()

    downloader = SkoolDownloader(
        email=args.email,
        password=args.password,
        output_dir=args.output,
        community_slug=args.community,
        headless=not args.no_headless,
    )

    asyncio.run(downloader.run())


if __name__ == "__main__":
    main()
