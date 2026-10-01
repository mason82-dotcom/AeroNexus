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
| AeroNexus Farming Guide | `farming-guide/` | geplant | Multispektral-Auswertung (M3M): NDVI und Applikationskarten |
| AeroNexus Photogrammetrie Addon | `photogrammetrie-addon/` | geplant | Orthofotos, 3D-Modelle (OpenDroneMap) |
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
- **control 0001:** Mavic 3M, Matrice 4, RC Pro/RC Plus 2 in der Oberfläche, Konfiguration über Env
- **control 0002:** Karte AMap ersetzt durch Leaflet/OpenStreetMap
- **control 0003:** Livestream-Player im Browser (WebRTC/WHEP, HLS-Fallback)

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
