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
    print(f"Neue Struktur unter: {classroom_dir}")


if __name__ == "__main__":
    main()
