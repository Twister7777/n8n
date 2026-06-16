#!/usr/bin/env python3
"""
Extrahiert Video- und Datei-Links aus den bereits gespeicherten
_raw_*.json Dumps und schreibt pro Lektion/Post eine `links.txt`.

Praktisch als Fallback-Referenz: falls ein Video (noch) nicht
heruntergeladen werden konnte, steht hier trotzdem der Original-Link
(z.B. zu YouTube), falls man es sich später dort ansehen möchte.

Liest NICHTS aus dem Netz und loggt sich nirgendwo ein – arbeitet nur
mit den bereits vorhandenen Dateien im Download-Ordner.

    python skool_extract_links.py --output skool_downloads
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

VIDEO_DOMAINS = [
    "stream.video.skool.com",
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


def is_video_url(url) -> bool:
    if not isinstance(url, str) or "edgemv" in url:
        return False
    for d in VIDEO_DOMAINS:
        if d in url:
            return True
    return bool(re.search(r"\.(m3u8|mp4|mov|webm|mkv)(\?|$)", url, re.I))


def is_attachment_url(url) -> bool:
    if not isinstance(url, str) or not url.startswith("http"):
        return False
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in ATTACHMENT_EXTS)


def walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)
    else:
        yield obj


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="skool_downloads", help="Download-Ordner")
    args = p.parse_args()
    root = Path(args.output)

    json_files = sorted(root.rglob("_raw_*.json"))
    print(f"{len(json_files)} gespeicherte Dump-Dateien gefunden\n")

    written = 0
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ✗ {jf}: {e}")
            continue

        videos: list[str] = []
        files: list[str] = []
        for v in walk(data):
            if not isinstance(v, str):
                continue
            if is_video_url(v) and v not in videos:
                videos.append(v)
            elif is_attachment_url(v) and v not in files:
                files.append(v)

        if not videos and not files:
            continue

        lines: list[str] = []
        if videos:
            lines.append("## Video-Links")
            lines.extend(videos)
        if files:
            lines.append("")
            lines.append("## Datei-Links")
            lines.extend(files)

        out_path = jf.parent / "links.txt"
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
        print(f"  ✓ {jf.parent.name}/links.txt  ({len(videos)} Video(s), {len(files)} Datei(en))")

    print(f"\nFertig: {written} links.txt Dateien geschrieben.")


if __name__ == "__main__":
    main()
