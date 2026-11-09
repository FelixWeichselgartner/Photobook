# Commands

## Konfiguration einrichten

Bei einer neuen Einrichtung die Beispielkonfiguration kopieren und die Pfade in
`config.json` anpassen. Eine bereits vorhandene Konfiguration bleibt dabei erhalten:

```powershell
if (-not (Test-Path config.json)) { Copy-Item config.json.example config.json }
```

`config.json.example` ist die versionierte Vorlage. Persoenliche JSON-Konfigurationen
wie `config.json` werden durch `.gitignore` ausgeschlossen.

## Fotobuch 2026

Die App nutzt die Pfade `good_database` und `good_folder` aus der lokalen
`config.json`. Alternativ legt `PHOTOBOOK_DATA_DIR` den Ordner fuer `app.db` und
`good_photobook` fest; die Umgebungsvariable hat Vorrang.
Die unveraendert kopierten aelteren `app copy*.db`-Backups enthalten noch die
frueheren lokalen Pfade der Good-Kopien; diese Pfade beim Wiederherstellen pruefen.

Das Konfigurationsbeispiel enthaelt Wien vor Japan. Wien liest alle JPG-, JPEG- und
PNG-Dateien aus dem angegebenen Wien-Ordner. Fuer Japan
verwenden `01_rotate.py` und `02_pdf.py` nur die in `app.db` als good markierten
Bilder, sortiert nach dem gespeicherten Aufnahmedatum.

```powershell
uv run python 01_rotate.py config.json
# Den erzeugten rotation_plan.txt bei Bedarf bearbeiten.
uv run python 02_pdf.py config.json
```

Ein erneuter Aufruf von `01_rotate.py` ueberschreibt den Rotationsplan. Das PDF
liegt jeweils als `Photobook.pdf` im `output_folder` der Konfiguration.
Fuer ein separates Kapitel kann eine eigene Konfigurationsdatei verwendet werden:

```powershell
uv run python 01_rotate.py "pfad/zur/konfiguration.json"
uv run python 02_pdf.py "pfad/zur/konfiguration.json"
```

uv run python export_gps_img.py . --recursive --width 400 --height 1200 --zoom 5 --line full --center tour

uv run python export_gps_img.py . --recursive --width 400 --height 1200 --zoom 12 --line full --center photo

uv run python export_gps_img.py "path" --recursive --width 400 --height 1200 --zoom 12 --line full --center photo

python export_gps_two_folders.py "path" --track-recursive --photos ./path --photos-recursive --width 400 --height 1200 --zoom 12 --line full --center photo

uv run .\photobook_gps.py .\config_gps.json  
