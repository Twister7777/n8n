#!/usr/bin/env python3
"""
Sortiert bereits heruntergeladene Classroom-Lektionen nachträglich in eine
verschachtelte Modul/Abschnitt/Lektion-Ordnerstruktur ein, statt alles flach
in einem Ordner zu haben. Erkennt außerdem doppelt heruntergeladene Lektionen
(gleicher Modul-/Abschnitts-/Lektionsname, nur mit unterschiedlicher
Nummerierung) und führt sie zu einer einzigen Lektion zusammen.

Arbeitet rein lokal auf dem Dateisystem – lädt nichts erneut herunter und
loggt sich nirgendwo ein.

    python skool_reorganize.py --output skool_downloads --community second-brain
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
from pathlib import Path

FOLDER_RE = re.compile(r"^(\d{3})_(.+)$")


def sanitize(name: str) -> str:
    name = html.unescape(name or "")
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip("._ ")[:150] or "unnamed"


def total_size(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())


def _lesson_title(lesson_dir: Path) -> str:
    """Best readable title: first '# Heading' of beschreibung.md, else folder."""
    desc = lesson_dir / "beschreibung.md"
    if desc.exists():
        for line in desc.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    name = lesson_dir.name
    m = FOLDER_RE.match(name)
    return m.group(2) if m else name


def _lesson_summary(lesson_dir: Path, limit: int = 220) -> str:
    """Short 'what's it about' excerpt from beschreibung.md."""
    desc = lesson_dir / "beschreibung.md"
    if not desc.exists():
        return ""
    text_lines = []
    for line in desc.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s == "---":
            continue
        text_lines.append(s)
    text = " ".join(text_lines)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text


def _parse_links(lesson_dir: Path) -> tuple[list[str], list[str]]:
    """Read a lesson's links.txt → (video_links, file_links)."""
    lf = lesson_dir / "links.txt"
    videos: list[str] = []
    files: list[str] = []
    if not lf.exists():
        return videos, files
    bucket = None
    for line in lf.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("## Video"):
            bucket = videos
        elif s.startswith("## Datei"):
            bucket = files
        elif s.startswith("http") and bucket is not None:
            bucket.append(s)
    return videos, files


def build_overview(classroom_dir: Path) -> int:
    """Write _Video-Uebersicht.txt: per Modul/Abschnitt the lesson title,
    a short 'what's it about' line and the video/file links. Returns the
    number of lessons listed."""
    # A lesson folder is any dir that holds a links.txt or beschreibung.md.
    lesson_dirs = set()
    for marker in ("links.txt", "beschreibung.md"):
        for f in classroom_dir.rglob(marker):
            lesson_dirs.add(f.parent)

    sections: dict[str, list[str]] = {}
    order: list[str] = []
    for lesson_dir in sorted(lesson_dirs, key=lambda d: str(d).lower()):
        rel = lesson_dir.relative_to(classroom_dir).parts
        crumb_parts = [
            (FOLDER_RE.match(p).group(2) if FOLDER_RE.match(p) else p) for p in rel[:-1]
        ]
        section = " > ".join(crumb_parts) if crumb_parts else "Classroom"
        title = _lesson_title(lesson_dir)
        summary = _lesson_summary(lesson_dir)
        videos, files = _parse_links(lesson_dir)

        block = [f"### {title}"]
        if summary:
            block.append(f"   Worum geht's: {summary}")
        for v in videos:
            block.append(f"   🎬 Video: {v}")
        for fl in files:
            block.append(f"   📎 Datei: {fl}")
        if not videos and not files:
            block.append("   (kein Video-/Datei-Link gefunden)")

        if section not in sections:
            sections[section] = []
            order.append(section)
        sections[section].append("\n".join(block))

    if not order:
        return 0

    out: list[str] = [
        "VIDEO- & DOWNLOAD-ÜBERSICHT",
        "Pro Modul/Abschnitt: jede Lektion, worum es geht und der passende Link.",
        "",
    ]
    for section in order:
        out.append("=" * 70)
        out.append(f"MODUL/ABSCHNITT:  {section}")
        out.append("=" * 70)
        out.append("")
        out.append("\n\n".join(sections[section]))
        out.append("")

    (classroom_dir / "_Video-Uebersicht.txt").write_text(
        "\n".join(out) + "\n", encoding="utf-8"
    )
    return sum(len(v) for v in sections.values())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="skool_downloads", help="Download-Ordner")
    p.add_argument("--community", required=True, help="Community-Slug, z.B. second-brain")
    args = p.parse_args()

    classroom_dir = Path(args.output) / sanitize(args.community) / "classroom"
    if not classroom_dir.exists():
        print(f"Ordner nicht gefunden: {classroom_dir}")
        return

    # --- Schritt 1: alle flachen Lektionsordner einlesen und nach ihrem
    # vollen Modul/Abschnitt/Lektion-Pfad gruppieren (Nummer ignorieren) ---
    groups: dict[tuple, list[tuple[str, Path]]] = {}
    for old in sorted(classroom_dir.iterdir()):
        if not old.is_dir():
            continue
        m = FOLDER_RE.match(old.name)
        if not m:
            continue  # schon umsortiert oder kein bekanntes Format
        idx, rest = m.group(1), m.group(2)
        parts = tuple(sanitize(part) for part in rest.split(" - "))
        groups.setdefault(parts, []).append((idx, old))

    if not groups:
        print("Keine flachen Lektionsordner zum Umsortieren gefunden "
              "(eventuell schon erledigt).")
        return

    moved = 0
    merged = 0
    for parts, entries in groups.items():
        *module_parts, leaf_title = parts
        target_dir = classroom_dir
        for mp in module_parts:
            target_dir = target_dir / mp
        target_dir.mkdir(parents=True, exist_ok=True)

        if len(entries) > 1:
            # Mehrere Ordner mit identischem Modul/Abschnitt/Lektion-Pfad
            # -> dieselbe Lektion wurde mehrfach heruntergeladen.
            entries_sorted = sorted(entries, key=lambda e: total_size(e[1]), reverse=True)
            primary_idx, primary_old = entries_sorted[0]
            print(f"  ⚠ Duplikat erkannt: {' / '.join(parts)} ({len(entries)}×) "
                  f"– behalte {primary_old.name} als Basis")
            target = target_dir / f"{primary_idx}_{leaf_title}"
            shutil.move(str(primary_old), str(target))
            for dup_idx, dup_old in entries_sorted[1:]:
                for f in dup_old.iterdir():
                    dest_f = target / f.name
                    if not dest_f.exists():
                        shutil.move(str(f), str(dest_f))
                        print(f"      (zusätzliche Datei übernommen: {f.name})")
                shutil.rmtree(dup_old, ignore_errors=True)
                print(f"      (Duplikat-Ordner {dup_old.name} entfernt)")
            merged += len(entries) - 1
        else:
            idx, old = entries[0]
            target = target_dir / f"{idx}_{leaf_title}"
            shutil.move(str(old), str(target))

        moved += 1
        print(f"  ✓ {' / '.join(parts)}")

    print(f"\nFertig: {moved} Lektionen einsortiert, {merged} Duplikat(e) zusammengeführt.")

    n = build_overview(classroom_dir)
    if n:
        print(f"✓ _Video-Uebersicht.txt geschrieben ({n} Lektionen)")
    print(f"Neue Struktur unter: {classroom_dir}")


if __name__ == "__main__":
    main()
