# AeroNexus

Selbst gehostete Drohnen-Plattform für DJI-Enterprise-Geräte, aufgebaut auf der DJI Cloud API.
DJI Pilot 2 auf der Fernsteuerung verbindet sich direkt mit dem eigenen Server, ohne DJI-Cloud.

**Hardware:** DJI RC Pro Enterprise mit Mavic 3E / 3T / 3M, DJI RC Plus 2 Enterprise mit Matrice 4T (4E).

## Module

| Modul | Ordner | Status | Inhalt |
|---|---|---|---|
| AeroNexus Control | `control/` | verfügbar | Web-Oberfläche: Lagebild, Geräte, Livestream-Player, Medien, Wegpunkte, Karte |
| AeroNexus API | `api/` | verfügbar | Backend (Spring Boot): Cloud-API-Protokoll, REST, WebSocket |
| AeroNexus Pilot | `pilot/` | verfügbar | Anbindung DJI Pilot 2 (Login-Seite, JSBridge). MSDK-App geplant |
| AeroNexus Fleet | `fleet/` | verfügbar | Gerätemodelle, Topologie, Gerätewörterbuch |
| AeroNexus Telemetry | `telemetry/` | verfügbar | MQTT-Broker (EMQX), Livestream-Server (MediaMTX) |
| AeroNexus Evidence | `evidence/` | Basis | Medienablage (S3/MinIO). Beweissicherung geplant |
| AeroNexus Mission | `mission/` | Basis | Wegpunkt-Bibliothek. Eigene Missionsplanung geplant |
| AeroNexus Mapping Tool | `mapping-tool/` | Basis | Leaflet/OSM-Karte, Annotationen, Flugzonen. Offline-Karten geplant |
| AeroNexus RTK | `rtk/` | geplant | Eigene RTK-Basis / NTRIP-Caster |
| AeroNexus Photovoltaik Tool | `photovoltaik-tool/` | geplant | Thermografie-Auswertung von PV-Anlagen (M3T / M4T) |
| AeroNexus Farming Guide | `farming-guide/` | verfügbar | Multispektral-Auswertung (M3M): NDVI und Applikationskarten |
| AeroNexus Photogrammetrie Addon | `photogrammetrie-addon/` | geplant | Orthofotos, 3D-Modelle (OpenDroneMap) |
| AeroNexus Compute-Agent | `compute-agent/` | verfügbar | Rechenknoten (x64, Windows/WSL2 oder Linux): NodeODM, COG, Kacheln für das Mapping Tool |
| AeroNexus Edge | `edge/` | verfügbar | Installation auf Raspberry Pi 5 / lokalem Server (Docker Compose) |

## Herkunft des Codes

`api` und `control` sind keine Kopien, sondern **Patches** auf die archivierten DJI-Referenzprojekte
(DJI-Cloud-API-Demo und Cloud-API-Demo-Web, MIT, eingefroren am 10.04.2025).
`edge/setup.sh` klont diese auf gepinnten Commits und wendet die Patches an. So bleibt nachvollziehbar,
was von DJI stammt und was AeroNexus ergänzt.

Bisherige Patches:
- **api 0001:** Matrice 4E/4T, M4-Kameras, RC Plus 2 als Gateway, ausführbares Jar
- **api 0002:** WHIP-URL mit `{stream}`-Platzhalter für MediaMTX
- **api 0003:** Mavic 3M Multispektral-Videotyp und `dongle_infos` vom RC (sonst verworfene Statusmeldungen)
- **api 0004:** unbekannte RC-Status-Keys werden ignoriert statt Fehler, M3M `rgb`, doppelte Videoeinträge entfernt
- **api 0005:** Livestream-Status vom RC wird angenommen (Sample warf bei jeder Meldung eine Exception)
- **api 0006:** Geometrie-Routen aus Pilot 2 (Typ 4 `mappingPrism`, 5 `mappingCylinder`) landen in der Wegpunkt-Bibliothek
- **api 0007:** Medien-Upload der M3M-Multispektral-TIFs (Pilot schickt dort kein Aufnahmedatum)
- **api 0008:** Kamera-Firmware vom RC im Format `67-0-0` (M3T) wird angenommen
- **api 0009:** Antworten des RC Plus 2 mit strukturierten Daten (z. B. Livestream-Start der Matrice 4T)
- **api 0010:** KI-Alarm-Meldungen von Pilot 2 (RC Plus 2) werden quittiert statt mit 404 abgelehnt
- **api 0011:** RTMP-Adresse mit Sende-Zugangsdaten für MediaMTX; die Zugangsdaten gehen nie an den Browser
- **control 0001:** Mavic 3M, Matrice 4, RC Pro/RC Plus 2 in der Oberfläche, Konfiguration über Env
- **control 0002:** Karte AMap ersetzt durch Leaflet/OpenStreetMap
- **control 0003:** Livestream-Player im Browser (WebRTC/WHEP, HLS-Fallback)
- **control 0004:** Leaflet-Karte bleibt unter schwebenden Fenstern (Livestream-Fenster war verdeckt)
- **control 0005:** Zeichenwerkzeug legt Annotationen nur noch einmal an (vorher bei jedem Werkzeugwechsel mehrfach)
- **control 0006:** Annotationen landen auch beim Zeichnen ausserhalb der Annotations-Seite in der richtigen Ebene
- **control 0007:** Seite Mapping: Auftraege, Bildauswahl aus den Medien, Ergebnis-Layer in der Karte
- **control 0008:** Livestream-Hinweis: beim RC zeigt der Stream das in Pilot 2 gewaehlte Objektiv
- **control 0009:** Flugrouten auf der Karte (Flaeche + berechnete Bahn), Flaechenrouten als Kopie bearbeiten
- **control 0010:** Mapping-Auftraege und Ergebnis-Layer in der Oberflaeche loeschen
- **control 0011:** Mapping-Auftraege abbrechen (auch waehrend der Rechenknoten rechnet)
- **control 0012:** Vorschaubilder in der Medienliste (Foto, Waermebild, Multispektral)
- **control 0013:** Fotoparameter in der Medienliste (Aufnahmezeit, Belichtung, GNSS/Hoehe, Gimbal, LRF)
- **control 0014:** Kamera-/Objektivspalte (Wide, IR, MS-Band ...), Medienliste verschlankt
- **control 0015:** Farming Guide auf der Mapping-Seite: Profil Multispektral, Index-Legende und Statistik
- **control 0016:** Farming Guide: Zonenkarten, Ausbringmengen, Export (Shapefile, ISO-XML, KML, GeoJSON)
- **control 0017:** Oberfläche auf Deutsch (zentrale Übersetzung `src/locales/de.ts`)
- **control 0018:** Livestream-Player schicken den Web-Login an MediaMTX (Abspielen nur angemeldet)

Prüfen, ob die Patches vollständig sind und sauber auf die gepinnten Commits passen: `edge/verify-patches.sh`

## Tests

`edge/run-tests.sh` führt die Tests der eigenen Dienste aus (Routenbearbeitung, Zonenkarten und Exporte,
Index-Berechnung, Anmeldung), jeweils im Docker-Image des Dienstes. GitHub Actions führt bei jedem Push
zusätzlich die Patch-Prüfung und den kompletten Image-Build aus.

## Schnellstart

```bash
cd edge
cp .env.example .env && nano .env   # SERVER_HOST, DJI_APP_*, alle change-me
./setup.sh
docker compose up -d --build
```

Details zu Ports, Pilot-2-Login, Livestream und Fehlersuche: [`edge/README.md`](edge/README.md).

## Lizenz

DJI-Upstream-Code: MIT. AeroNexus-Patches und eigene Dateien: MIT.
