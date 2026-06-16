# Skool Video & Content Downloader

Lädt automatisch **alle Videos, Kursvideos und Dateianhänge** aus deinen Skool-Communities herunter – bevor die Community geschlossen wird.

## Was wird heruntergeladen?

- Videos aus dem **Classroom** (alle Module & Lektionen)
- **Beschreibungstexte** jeder Lektion (als `beschreibung.md`)
- Videos aus **Feed-Posts** (Vimeo, Wistia, YouTube, Loom, direkte MP4-Links)
- **Dateianhänge** (PDFs, ZIPs, Word-Dokumente, Excel-Tabellen, etc.)
- Zur Sicherheit: die kompletten Rohdaten jeder Seite als `_raw_*.json`

> Das Tool liest die Inhalte aus dem in Skool eingebetteten `__NEXT_DATA__`-JSON
> (Next.js). Das ist deutlich zuverlässiger als sichtbare HTML-Elemente zu suchen.

## Voraussetzungen

- **Python 3.9+** – [python.org/downloads](https://www.python.org/downloads/)
- Ein aktiver Skool-Account mit Zugang zur Community

## Installation

```bash
# 1. Repository klonen oder Dateien herunterladen
git clone https://github.com/twister7777/n8n.git
cd n8n

# 2. Abhängigkeiten installieren
pip install -r requirements.txt

# 3. Playwright-Browser installieren (einmalig)
playwright install chromium
```

## Verwendung

### Alle Communities auf einmal herunterladen
```bash
python skool_downloader.py --email deine@email.com --password deinpasswort
```

### Nur eine bestimmte Community
Den Community-Slug aus der URL nehmen, z.B. bei `https://www.skool.com/meine-gruppe` ist der Slug `meine-gruppe`:
```bash
python skool_downloader.py --email deine@email.com --password deinpasswort --community meine-gruppe
```

### Mit sichtbarem Browser (bei CAPTCHA oder 2FA)
```bash
python skool_downloader.py --email deine@email.com --password deinpasswort --no-headless
```

### Eigenen Ausgabeordner festlegen
```bash
python skool_downloader.py --email deine@email.com --password deinpasswort --output /Users/martin/skool-backup
```

## Ordnerstruktur der Downloads

```
skool_downloads/
├── meine-community/
│   ├── classroom/
│   │   ├── Modul 1 - Einführung/
│   │   │   ├── 001_Willkommen.mp4
│   │   │   └── arbeitsblatt.pdf
│   │   └── Modul 2 - Fortgeschritten/
│   │       └── 001_Lektion.mp4
│   └── feed/
│       ├── post_abc123/
│       │   └── 001_Video.mp4
│       └── post_def456/
│           └── checkliste.pdf
└── download.log
```

## Tipps & Hinweise

### Problem: Login schlägt fehl
- Nutze `--no-headless` um den Browser zu sehen und ggf. ein CAPTCHA manuell zu lösen
- Bei 2FA (Zwei-Faktor-Authentifizierung): mit `--no-headless` starten und den Code im Browser eingeben

### Problem: Bestimmte Videos werden nicht heruntergeladen
Skool nutzt verschiedene Video-Plattformen. Das Skript unterstützt:
- **Vimeo** (häufigste bei Skool)
- **Wistia**
- **YouTube**
- **Loom**
- **Direkte MP4-Links** (Skool CDN, AWS S3, etc.)

### Passwort nicht in der Kommandozeile speichern
Setze stattdessen Umgebungsvariablen:
```bash
export SKOOL_EMAIL="deine@email.com"
export SKOOL_PASSWORD="deinpasswort"
python skool_downloader.py --email "$SKOOL_EMAIL" --password "$SKOOL_PASSWORD"
```

## Alternativer Ansatz: yt-dlp direkt

Falls du die Video-URL manuell aus dem Browser-Netzwerk-Tab herausgefunden hast:
```bash
yt-dlp --cookies-from-browser chrome "https://player.vimeo.com/video/XXXXXX"
```

## Alle Optionen

```
--email       Skool E-Mail (Pflicht)
--password    Skool Passwort (Pflicht)
--output      Ausgabeordner (Standard: skool_downloads)
--community   Nur diese Community herunterladen (Slug aus URL)
--no-headless Browser-Fenster anzeigen (für CAPTCHA/2FA)
```

---

> **Hinweis:** Lade nur Inhalte herunter, zu denen du Zugang hast. Dieses Tool dient der persönlichen Sicherung von Inhalten, auf die du legitim Zugriff hast.
