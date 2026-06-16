#!/usr/bin/env python3
"""
Skool Community Content Downloader
Lädt alle Videos, Beschreibungstexte und Anhänge aus Skool-Communities herunter.

Verwendung:
    python skool_downloader.py --email deine@email.com --password deinpasswort
    python skool_downloader.py --email deine@email.com --password deinpasswort --community meine-community
    python skool_downloader.py --email deine@email.com --password deinpasswort --no-headless

Der eigentliche Trick: Skool basiert auf Next.js und legt alle Inhalte einer
Seite als JSON im Element `__NEXT_DATA__` ab (Texte, Video-Links, Datei-Links,
Kursstruktur). Dieses JSON auszulesen ist deutlich zuverlässiger als sichtbare
HTML-Elemente zu durchsuchen.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional
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

from playwright.async_api import Page, async_playwright
import yt_dlp
import requests as http_requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKIP_SLUGS = {
    "feed", "login", "signup", "discover", "settings",
    "notifications", "profile", "checkout", "invite", "about",
    "members", "leaderboards", "calendar", "map", "search",
}

VIDEO_DOMAINS = [
    "vimeo.com", "player.vimeo.com",
    "wistia.com", "wistia.net", "fast.wistia",
    "loom.com",
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "mux.com", "stream.mux.com",
    "vidyard.com",
]

ATTACHMENT_EXTS = {
    ".pdf", ".zip", ".docx", ".xlsx", ".pptx",
    ".doc", ".xls", ".ppt", ".csv", ".txt", ".rtf",
    ".mp3", ".wav", ".m4a",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".key", ".pages", ".numbers", ".epub", ".mobi",
}


def sanitize(name: str) -> str:
    """Strip characters that are invalid in file/dir names."""
    name = html.unescape(name or "")
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip("._ ")[:150] or "unnamed"


def is_video_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    for domain in VIDEO_DOMAINS:
        if domain in url:
            return True
    if re.search(r"\.(m3u8|mp4|mov|webm|mkv)(\?|$)", url, re.I):
        return True
    return False


def is_attachment_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in ATTACHMENT_EXTS)


def walk(obj) -> Iterable:
    """Yield every leaf value inside nested dicts/lists."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)
    else:
        yield obj


def walk_dicts(obj) -> Iterable[dict]:
    """Yield every dict inside a nested structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_dicts(v)


def unique(seq: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(x for x in seq if x))


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
        debug: bool = True,
    ):
        self.email = email
        self.password = password
        self.output_dir = Path(output_dir)
        self.community_slug = community_slug
        self.headless = headless
        self.debug = debug
        self._cookies: list = []
        self._done: set[str] = set()

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
    # __NEXT_DATA__ extraction
    # ------------------------------------------------------------------

    async def next_data(self, page: Page) -> Optional[dict]:
        """Read the embedded Next.js JSON payload from the current page."""
        try:
            raw = await page.evaluate(
                "() => { const e = document.getElementById('__NEXT_DATA__');"
                " return e ? e.textContent : null; }"
            )
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def login(self, page: Page) -> bool:
        self.log("→ Login…")
        await page.goto(f"{self.BASE}/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

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

        for sel in [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Log in")', 'button:has-text("Sign in")',
            'button:has-text("Continue")',
        ]:
            try:
                await page.click(sel, timeout=3000)
                break
            except Exception:
                continue

        try:
            await page.wait_for_url(
                lambda url: "/login" not in url and "/signup" not in url,
                timeout=25_000,
            )
            self.log(f"✓ Eingeloggt als {self.email}")
            return True
        except Exception:
            self.log("✗ Login fehlgeschlagen. Tipp: mit --no-headless starten und im")
            self.log("  Browser-Fenster ggf. CAPTCHA / Bestätigung manuell durchführen.")
            # Even on timeout the login may have succeeded behind a CAPTCHA the user
            # solved manually – give it one more chance.
            await page.wait_for_timeout(8000)
            if "/login" not in page.url:
                self.log("✓ Login erkannt (verzögert).")
                return True
            return False

    # ------------------------------------------------------------------
    # Community discovery
    # ------------------------------------------------------------------

    async def get_community_slugs(self, page: Page) -> list[str]:
        if self.community_slug:
            return [self.community_slug]

        self.log("→ Communities werden gesucht…")
        await page.goto(f"{self.BASE}/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        slugs: set[str] = set()
        for link in await page.query_selector_all("a[href]"):
            href = (await link.get_attribute("href")) or ""
            m = re.match(r"^/([a-z0-9][a-z0-9-]{1,60}[a-z0-9])(?:/|$|\?)", href)
            if m and m.group(1) not in SKIP_SLUGS:
                slugs.add(m.group(1))

        self.log(f"✓ Communities gefunden: {sorted(slugs)}")
        return sorted(slugs)

    # ------------------------------------------------------------------
    # Classroom enumeration (courses → modules → lessons)
    # ------------------------------------------------------------------

    async def get_classroom_lessons(self, page: Page, slug: str) -> list[dict]:
        """Return [{url, title}] for every lesson in the community classroom."""
        url = f"{self.BASE}/{slug}/classroom"
        self.log(f"  → Classroom öffnen: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        lessons: dict[str, str] = {}   # url -> title

        # --- 1) Aus dem __NEXT_DATA__-JSON die Kursstruktur lesen -------------
        data = await self.next_data(page)
        if data:
            self._dump_debug(data, f"{slug}_classroom")
            for course_id, lesson_id, title in self._lessons_from_json(data):
                u = f"{self.BASE}/{slug}/classroom/{course_id}?md={lesson_id}"
                lessons.setdefault(u, title)

        # --- 2) Zusätzlich sichtbare Classroom-Links aus dem DOM -------------
        for course_link in await page.query_selector_all(f'a[href*="/{slug}/classroom/"]'):
            href = (await course_link.get_attribute("href")) or ""
            full = urljoin(self.BASE, href)
            if full not in lessons:
                txt = (await course_link.inner_text()).strip()
                lessons[full] = txt or href.rsplit("/", 1)[-1]

        # Visit each course landing page to expand the lessons it links to.
        for course_url in list(lessons.keys()):
            if re.search(r"/classroom/[^/?]+$", course_url):  # looks like a course root
                try:
                    await page.goto(course_url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    cdata = await self.next_data(page)
                    if cdata:
                        for course_id, lesson_id, title in self._lessons_from_json(cdata):
                            u = f"{self.BASE}/{slug}/classroom/{course_id}?md={lesson_id}"
                            lessons.setdefault(u, title)
                except Exception:
                    pass

        result = [{"url": u, "title": t} for u, t in lessons.items()]
        self.log(f"  ✓ {len(result)} Classroom-Einträge gefunden")
        return result

    def _lessons_from_json(self, data: dict):
        """Yield (course_id, lesson_id, title) tuples from a Skool JSON payload."""
        # Find candidate course containers (objects holding a 'children' list).
        for node in walk_dicts(data):
            children = node.get("children")
            root_id = node.get("id")
            if not (isinstance(children, list) and root_id):
                continue
            for child in walk_dicts({"_": children}):
                cid = child.get("id")
                if not cid or cid == root_id:
                    continue
                # A leaf (no further children) is treated as a lesson.
                if not isinstance(child.get("children"), list) or not child.get("children"):
                    title = (
                        child.get("name")
                        or (child.get("metadata") or {}).get("title")
                        or child.get("title")
                        or f"lesson_{cid[:8]}"
                    )
                    yield (root_id, cid, str(title))

    # ------------------------------------------------------------------
    # Feed enumeration
    # ------------------------------------------------------------------

    async def get_feed_posts(self, page: Page, slug: str) -> list[dict]:
        self.log(f"  → Feed öffnen: {self.BASE}/{slug}")
        await page.goto(f"{self.BASE}/{slug}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        for _ in range(10):
            await page.keyboard.press("End")
            await page.wait_for_timeout(1200)

        posts: dict[str, str] = {}
        for link in await page.query_selector_all(f'a[href*="/{slug}/p/"]'):
            href = (await link.get_attribute("href")) or ""
            full = urljoin(self.BASE, href.split("?")[0])
            posts.setdefault(full, href.split("/p/")[-1].split("/")[0])

        self.log(f"  ✓ {len(posts)} Feed-Posts gefunden")
        return [{"url": u, "title": t} for u, t in posts.items()]

    # ------------------------------------------------------------------
    # Process a single lesson / post
    # ------------------------------------------------------------------

    async def process_url(self, page: Page, item: dict, base_dir: Path, index: int):
        title = item["title"]
        safe = f"{index:03d}_{sanitize(title)}"
        dest = base_dir / safe
        dest.mkdir(parents=True, exist_ok=True)
        self.log(f"    · {title}")

        intercepted: list[str] = []

        def _on_request(req):
            if is_video_url(req.url):
                intercepted.append(req.url)

        page.on("request", _on_request)
        try:
            await page.goto(item["url"], wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            data = await self.next_data(page)

            # --- Beschreibungstext + Medien aus dem JSON --------------------
            description_parts: list[str] = []
            if data:
                if self.debug:
                    self._dump_debug(data, safe, sub=dest)
                for v in walk(data):
                    if is_video_url(v):
                        intercepted.append(v)
                # Text-Felder einsammeln (Beschreibung, Inhalt, Body …)
                for node in walk_dicts(data):
                    for key in ("description", "content", "body", "text", "post"):
                        val = node.get(key)
                        if isinstance(val, str) and len(val.strip()) > 20:
                            description_parts.append(val.strip())

            # --- Lesbaren Beschreibungstext zusätzlich aus dem DOM ----------
            dom_text = await self._main_text(page)
            if dom_text:
                description_parts.append(dom_text)

            # --- iframes (Vimeo, Wistia, YouTube, Loom) ---------------------
            for iframe in await page.query_selector_all("iframe[src]"):
                src = (await iframe.get_attribute("src")) or ""
                if is_video_url(src):
                    intercepted.append(src)

            # --- <video> tags ----------------------------------------------
            for sel in ["video[src]", "video > source[src]"]:
                for el in await page.query_selector_all(sel):
                    src = (await el.get_attribute("src")) or ""
                    if src:
                        intercepted.append(src)

            # --- Beschreibungstext speichern -------------------------------
            self._save_description(dest, title, description_parts)

            # --- Dateianhänge (DOM-Links) ----------------------------------
            for link in await page.query_selector_all("a[href]"):
                href = (await link.get_attribute("href")) or ""
                full = urljoin(self.BASE, href)
                if is_attachment_url(full):
                    text = (await link.inner_text()).strip()
                    await self._download_file(full, dest, text, page)

            # --- Dateianhänge (aus JSON) -----------------------------------
            if data:
                for node in walk_dicts(data):
                    for key in ("url", "link", "href", "downloadUrl", "fileUrl"):
                        val = node.get(key)
                        if is_attachment_url(val):
                            name = node.get("name") or node.get("filename") or ""
                            await self._download_file(val, dest, name, page)

        finally:
            page.remove_listener("request", _on_request)

        # --- Videos herunterladen ------------------------------------------
        for i, vurl in enumerate(unique(intercepted)):
            if vurl in self._done:
                continue
            self._done.add(vurl)
            self.log(f"      ▶ Video {i + 1}: {vurl[:90]}")
            await asyncio.to_thread(self._ytdlp_download, vurl, str(dest))

    async def _main_text(self, page: Page) -> str:
        """Grab the most text-rich content container as readable description."""
        try:
            return await page.evaluate(
                """() => {
                    const sels = [
                        '[class*="LessonContent"]','[class*="ChildrenContent"]',
                        '[class*="PostContent"]','[class*="PostBody"]',
                        '[class*="styled__Content"]','article','main'
                    ];
                    let best = '';
                    for (const s of sels) {
                        for (const el of document.querySelectorAll(s)) {
                            const t = (el.innerText || '').trim();
                            if (t.length > best.length) best = t;
                        }
                    }
                    return best;
                }"""
            ) or ""
        except Exception:
            return ""

    def _save_description(self, dest: Path, title: str, parts: list[str]):
        seen: set[str] = set()
        cleaned: list[str] = []
        for p in parts:
            p = html.unescape(re.sub(r"<[^>]+>", " ", p))  # strip HTML tags
            p = re.sub(r"\s+\n", "\n", p).strip()
            key = p[:120]
            if p and key not in seen:
                seen.add(key)
                cleaned.append(p)
        if not cleaned:
            return
        body = f"# {title}\n\n" + "\n\n---\n\n".join(cleaned) + "\n"
        (dest / "beschreibung.md").write_text(body, encoding="utf-8")
        self.log("      ✓ Beschreibung gespeichert")

    # ------------------------------------------------------------------
    # yt-dlp download
    # ------------------------------------------------------------------

    def _ytdlp_download(self, url: str, output_dir: str):
        cookie_file = self._write_cookies()
        ydl_opts: dict = {
            "outtmpl": str(Path(output_dir) / "%(title)s.%(ext)s"),
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "quiet": False,
            "ignoreerrors": True,
            "retries": 3,
            "fragment_retries": 5,
            "http_headers": {"Referer": self.BASE + "/"},
        }
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
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
                secure = "TRUE" if c.get("secure", False) else "FALSE"
                expires = int(c.get("expires") or 0)
                f.write(
                    f"{domain}\t{flag}\t{c.get('path', '/')}\t{secure}\t{expires}"
                    f"\t{c['name']}\t{c['value']}\n"
                )
        return path

    # ------------------------------------------------------------------
    # Direct file download
    # ------------------------------------------------------------------

    async def _download_file(self, url: str, dest_dir: Path, name: str, page: Page):
        if url in self._done:
            return
        self._done.add(url)

        safe = sanitize(name or url.split("/")[-1].split("?")[0])
        ext = Path(urlparse(url).path).suffix
        if ext and not safe.lower().endswith(ext.lower()):
            safe += ext
        dest = dest_dir / safe
        if dest.exists():
            return

        self.log(f"      ⬇ Datei: {safe}")
        sess = http_requests.Session()
        for c in await page.context.cookies():
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        try:
            r = sess.get(url, stream=True, timeout=90, headers={"Referer": self.BASE + "/"})
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
            self.log(f"      ✓ {dest.name}")
        except Exception as e:
            self.log(f"      ✗ {url[:80]}: {e}")

    # ------------------------------------------------------------------
    # Debug dump
    # ------------------------------------------------------------------

    def _dump_debug(self, data: dict, name: str, sub: Optional[Path] = None):
        if not self.debug:
            return
        try:
            target_dir = sub if sub else (self.output_dir / "_debug")
            target_dir.mkdir(parents=True, exist_ok=True)
            payload = data.get("props", {}).get("pageProps", data)
            (target_dir / f"_raw_{sanitize(name)}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2)[:5_000_000],
                encoding="utf-8",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
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
                self.log(f"\n{'=' * 60}\nCommunity: {slug}\n{'=' * 60}")
                comm_dir = self.output_dir / sanitize(slug)

                # --- Classroom ---
                classroom_dir = comm_dir / "classroom"
                lessons = await self.get_classroom_lessons(page, slug)
                for idx, lesson in enumerate(lessons, 1):
                    await self.process_url(page, lesson, classroom_dir, idx)
                    await asyncio.sleep(1)

                # --- Feed ---
                feed_dir = comm_dir / "feed"
                posts = await self.get_feed_posts(page, slug)
                for idx, post in enumerate(posts, 1):
                    await self.process_url(page, post, feed_dir, idx)
                    await asyncio.sleep(1)

                self.log(f"✓ Community {slug} abgeschlossen")

            await browser.close()
            self.log(f"\n{'=' * 60}")
            self.log(f"Fertig → {self.output_dir.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skool Videos, Beschreibungstexte & Dateien herunterladen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python skool_downloader.py --email du@mail.com --password geheim
  python skool_downloader.py --email du@mail.com --password geheim --community meine-gruppe
  python skool_downloader.py --email du@mail.com --password geheim --no-headless
        """,
    )
    parser.add_argument("--email", required=True, help="Skool E-Mail-Adresse")
    parser.add_argument("--password", required=True, help="Skool Passwort")
    parser.add_argument("--output", default="skool_downloads", help="Ausgabeordner")
    parser.add_argument("--community", default=None, help="Nur diese Community (Slug aus URL)")
    parser.add_argument("--no-headless", action="store_true", help="Browser-Fenster anzeigen")
    parser.add_argument("--no-debug", action="store_true", help="Keine Roh-JSON-Dateien speichern")
    args = parser.parse_args()

    downloader = SkoolDownloader(
        email=args.email,
        password=args.password,
        output_dir=args.output,
        community_slug=args.community,
        headless=not args.no_headless,
        debug=not args.no_debug,
    )
    asyncio.run(downloader.run())


if __name__ == "__main__":
    main()
