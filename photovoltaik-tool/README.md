# AeroNexus Photovoltaik Tool

Thermografische Inspektion von PV-Anlagen (Dach und Freifläche) mit Mavic 3T und Matrice 4T.
Dienst `photovoltaik-tool/service` (Compose-Dienst `pv`, Port 6792), Oberfläche: Menüpunkt **Photovoltaik**.

## Ablauf

1. Anlage mit der Wärmebildkamera befliegen, Fotoformat **R-JPEG** (radiometrisch, Standard bei M3T/M4T).
   Senkrecht (Gimbal -90°) für Freiflächen, schräg für Dächer. Auto-Upload in Pilot 2 an.
2. Web: Photovoltaik → **Neue Inspektion** → Wärmebilder (`*_T.JPG`) auswählen → Starten.
3. Der Dienst wertet jedes Bild aus:
   - Temperaturen in °C mit dem DJI Thermal SDK (Emissionsgrad, reflektierte Temperatur, Luftfeuchte, Entfernung)
   - Hotspots: Bereiche mindestens `min. ΔT` wärmer als die Umgebung (Referenz: Modultemperatur rundum);
     warme Dachflächen, Reihenzwischenräume und Kanten werden ignoriert
   - Klassen nach gängiger Praxis zu IEC TS 62446-3: 1 beobachten (ab 3 K), 2 Wartung planen (ab 10 K),
     3 dringend (ab 20 K), einstellbar
   - Position aus GPS, Gimbalwinkeln und Höhe; bei Dächern die Höhe des Laser-Entfernungsmessers
   - derselbe Fehler in überlappenden Bildern wird zusammengefasst (Umkreis einstellbar)
4. Ergebnis: Marker auf der Karte (Farbe = Klasse), Liste, Detail mit Bildausschnitt, Status
   (offen / bestätigt / verworfen) und Notiz; Export als Bericht (HTML, im Browser als PDF drucken), CSV, GeoJSON.

Aussagekräftig nur bei Einstrahlung über 600 W/m², klarem Himmel und Anlage unter Last.
Fehlerart (Zelle, Substring, Modul, String) und Fläche sind Schätzungen aus der Bildgeometrie.

## DJI Thermal SDK

Die Umrechnung der Rohwerte in Temperaturen macht DJIs Thermal SDK (proprietär, DJI-Lizenz). Es ist deshalb
nicht im Repository und muss einmal selbst geladen werden (getestet: v1.8 vom 29.08.2025, unterstützt M3T/M4T):

```bash
# Download: https://www.dji.com/downloads/softwares/dji-thermal-sdk (Lizenz akzeptieren)
photovoltaik-tool/tools/install-tsdk.sh ~/Downloads/dji_thermal_sdk_v1.8_*.zip
cd edge && docker compose restart tsdk pv
```

DJI liefert das SDK für Linux nur als x64. Es läuft im Hilfscontainer `tsdk` (`photovoltaik-tool/tsdk`):
auf einem x64-Server direkt, auf dem Raspberry Pi 5 mit dem x64-Emulator **Box64** (ca. 0,9 s pro Bild).
qemu scheidet auf dem Pi 5 aus: dessen Kernel nutzt 16-KB-Speicherseiten, die SDK-Bibliotheken lassen sich
damit unter qemu nicht laden. Der Container ist nur intern erreichbar; `pv` schickt ihm das R-JPEG und erhält
die Temperaturen (640 × 512, °C).

Ohne SDK zeigt die Seite einen Hinweis; Inspektionen schlagen mit „DJI Thermal SDK fehlt“ fehl.

## Technik

- Python-Standardbibliothek + GDAL/numpy aus dem gepinnten GDAL-Image (wie Farming Guide), kein pip.
- Ablage im MinIO-Bucket `pv-inspections` (eigener Benutzer `pvtool`, liest die Pilot-2-Uploads).
- Module: `rjpeg.py` (R-JPEG lesen), `thermal.py` (SDK), `detect.py` (Hotspots), `geo.py` (Pixel → Boden),
  `analysis.py` (Bild → Anomalien, Zusammenfassen), `inspections.py` (API, Hintergrund-Auswertung, Exporte).
- Tests: `edge/run-tests.sh pv` (mit echten Bildern: `AERONEXUS_REAL_RJPEG_DIR=...`).
