# AeroNexus Edge

Lokale Installation des AeroNexus-Stacks (Raspberry Pi 5 oder x86-Server).

Selbst gehosteter Cloud-API-Server für **DJI Pilot 2**, als Ersatz für FlightHub 2 im eigenen LAN.
Basis sind die archivierten DJI-Referenzprojekte (Stand 10.04.2025), erweitert um Matrice 4 und RC Plus 2.

| Fernsteuerung | Fluggeräte | Status |
|---|---|---|
| DJI RC Pro Enterprise | Mavic 3E, Mavic 3T, Mavic 3M | Upstream schon unterstützt, Web-Anzeige für 3M ergänzt |
| DJI RC Plus 2 Enterprise | Matrice 4T (und 4E) | Per Patch nachgerüstet |

## Funktionsumfang (Pilot-to-Cloud)

Geräte-Topologie und Telemetrie (OSD/State), Lagebild mehrerer Geräte (TSA), Livestream über RTMP,
Karten-Annotationen (Sync mit Pilot 2), automatischer Medien-Upload nach S3/MinIO,
Wegpunkt-Bibliothek (KMZ/WPML hochladen, Pilot synchronisiert sie), HMS-Meldungen.

Nicht möglich mit RC: Missionen aus der Cloud starten. Das geht nur mit Dock.

## Architektur

```
DJI Pilot 2 (RC)
  |-- HTTPS/HTTP :6789  --> api (Spring Boot, gepatchtes DJI-Demo)
  |-- MQTT :1884        --> EMQX  <--> api
  |-- S3 :9000          --> MinIO (STS-Uploads direkt vom Controller)
  |-- RTMP :1935        --> MediaMTX --> WebRTC :8889 / HLS :8888 / RTSP :8554
  `-- WebView :8085     --> control (Vue, Login-Seite /pilot-login)
api --> MySQL, Redis
```

## Voraussetzungen

- Raspberry Pi 5 (8 GB empfohlen) oder x86-Host, 64-bit OS, Docker + Compose v2, `git`, `perl`.
  Der ODROID-HC4 (4 GB) ist für den kompletten Stack zu knapp.
- DJI-Developer-Account mit einer App vom Typ **Cloud API**. Daraus App ID, App Key, License.
- Der Controller muss den Server erreichen: WLAN im selben Netz oder 4G-Dongle plus VPN/Reverse-Proxy.
- Firmware mindestens auf dem Stand der Cloud-API-Releasenotes (M4T: Pilot 2 / RC Plus 2 aktuell halten).

## Schnellstart

```bash
git clone https://github.com/mason82-dotcom/AeroNexus.git
cd AeroNexus/edge
cp .env.example .env
nano .env                 # SERVER_HOST, DJI_APP_*, alle change-me ersetzen
./setup.sh                # klont Upstream (gepinnt), wendet Patches an, rendert Secrets
docker compose up -d --build
docker compose logs -f api
```

Der erste Build dauert auf dem Pi 10 bis 20 Minuten (Maven + Node).

Wichtig: Passwörter in `.env` ohne `'` und `$`, weil sie in SQL und CSV gerendert werden.
Die SQL-Dateien laufen nur beim allerersten Start von MySQL (leeres `data/mysql`).

## Ports

| Port | Dienst | Wer greift zu |
|---|---|---|
| 8085 | Web-UI + Pilot-Login | Browser, Pilot 2 |
| 6789 | Backend API + WebSocket | Browser, Pilot 2 |
| 1884 / 8084 | MQTT / MQTT über WS | Pilot 2, Backend |
| 9000 | MinIO S3 | Pilot 2 (Uploads), Browser, Compute-Agent |
| 1935 | RTMP-Ingest | Pilot 2 |
| 8889 / 8888 / 8554 | WebRTC (WHEP/WHIP) / HLS / RTSP | Browser (mit Web-Login), VLC (mit RTSP-Login) |
| 6790 / 6791 | Mapping / Farming | Browser, Compute-Agent |
| 8189/udp | WebRTC-Medien | Browser |
| 9001 | MinIO-Konsole | nur auf dem Server (127.0.0.1) |
| 9997 | MediaMTX API | nur auf dem Server (127.0.0.1) |
| 18083 | EMQX Dashboard (User `admin`) | nur auf dem Server (127.0.0.1) |

Admin-Oberflächen vom PC aus per SSH-Tunnel: `ssh -L 18083:localhost:18083 -L 9001:localhost:9001 <user>@<SERVER_HOST>`,
dann `http://localhost:18083` bzw. `http://localhost:9001` im Browser.

## Controller verbinden

1. Controller ins Netz bringen, in dem `SERVER_HOST` erreichbar ist.
2. Pilot 2 öffnen, auf der Startseite **Cloud Services** wählen und dort die Drittanbieter-Plattform
   (**Open Platform**) auswählen.
3. URL eingeben: `http://<SERVER_HOST>:8085/pilot-login`
4. Mit `pilot` und `PILOT_LOGIN_PASSWORD` anmelden (zweite Fernsteuerung, z. B. RC Plus 2: `pilot2` und
   `PILOT2_LOGIN_PASSWORD`, eigener MQTT-Benutzer `MQTT_PILOT2_USER`). Die Seite prüft die DJI-License per JSBridge
   und verbindet danach automatisch MQTT, API, Karte, Medien, Wegpunkte und Livestream.
5. Im Browser `http://<SERVER_HOST>:8085` mit `adminPC` öffnen. Der Controller samt Fluggerät
   sollte unter Geräte bzw. im Lagebild erscheinen.

## Karte

Das Web-UI nutzt Leaflet mit OpenStreetMap, umschaltbar auf Satellitenbild (Esri World Imagery).
Startpunkt und Zoom über `MAP_CENTER` / `MAP_ZOOM`, Kachelserver über `MAP_TILE_URL` (Build-Zeit,
nach Änderung `docker compose up -d --build control`).

Zeichnen von Annotationen und Flugzonen:
- **Pin:** Klick setzt den Pin.
- **Linie / Polygon:** Klicks setzen Punkte, Doppelklick beendet. `Esc` bricht ab.
- **Kreis (Flugzone):** Maustaste am Mittelpunkt drücken, auf den Radius ziehen, loslassen.

**Offline-Karte:** Standard-Ebene ist eine Vektorkarte von Baden-Württemberg (+10 km Rand), die komplett
vom Server kommt und ohne Internet funktioniert (Straßen, Wege, Gebäude, Gewässer, Ortsnamen, Zoom bis 20).
Die Ebenen „Karte (online)“ und „Satellit (online)“ bleiben im Ebenen-Schalter oben rechts.
- Datei: `edge/data/basemaps/baden-wuerttemberg.pmtiles` (ca. 800 MB, OpenStreetMap über Protomaps)
- Erstellen/aktualisieren (z. B. monatlich): `mapping-tool/basemaps/fetch-basemap.sh`
- Andere Region: GeoJSON-Polygon nach `mapping-tool/basemaps/regions/<name>.geojson`,
  `fetch-basemap.sh <name>` und `MAP_PMTILES_URL=/basemaps/<name>.pmtiles` in `edge/.env`
- Außerhalb der Region bleibt die Offline-Karte leer: dort auf „Karte (online)“ umschalten.
- Die Kartendatei ist nicht in der Datensicherung (jederzeit neu ladbar).

Der öffentliche OSM-Kachelserver (Ebene „Karte (online)“) ist für gelegentliche private Nutzung gedacht.

## Livestream

Im Web-UI unter **Livestream**: Fluggerät, Kamera und Qualität wählen, Typ **RTMP**, Start.
Das Video erscheint direkt auf der Seite, darunter steht der Player-Status.

Ablauf: Pilot 2 schickt per RTMP an MediaMTX (`rtmp://<SERVER_HOST>:1935/live/<SN>-<Payload>?user=…&pass=…`,
die Sende-Zugangsdaten `LIVE_PUBLISH_*` hängt das Backend an),
der Browser holt den Stream per WebRTC (WHEP, ca. 0,3 s Verzögerung). Kann der Browser H.264 nicht
über WebRTC dekodieren, wechselt der Player automatisch auf HLS (2 bis 4 s Verzögerung).
Die ersten Sekunden nach dem Start meldet der Player "waiting for stream", bis die Drohne sendet.

Typ **WEBRTC** (WHIP direkt vom Controller zu MediaMTX) ist vorbereitet, aber experimentell: ob Pilot 2
das für Mavic 3E/3T/3M und Matrice 4T anbietet, hängt von Firmware und Pilot-Version ab. RTMP ist der Standard.

Weitere Wege zum selben Stream:
- Liste aktiver Streams (auf dem Server): `curl http://127.0.0.1:9997/v3/paths/list`
- VLC: `rtsp://<RTSP_USER>:<RTSP_PASSWORD>@<SERVER_HOST>:8554/live/<SN>-<Payload>`

Zugriffsschutz: MediaMTX fragt bei jedem Senden und Abspielen den Mapping-Dienst
(`mapping-tool/service/app/mediaauth.py`). Senden nur mit `LIVE_PUBLISH_*`, Abspielen nur mit gültigem
Web-Login oder RTSP-Zugangsdaten. Abgelehnte Zugriffe stehen im Log: `docker compose logs mapping | grep denied`.
Meldet der Player "not allowed to watch", ist der Web-Login abgelaufen (24 h): neu anmelden.
Läuft der Mapping-Dienst nicht, startet kein Livestream.

## Fehlersuche

- **Pilot verbindet MQTT nicht:** In den EMQX-Dashboard-Clients nachsehen. Die Bootstrap-Authentifizierung
  (Benutzer aus `runtime/emqx/auth-bootstrap.csv`) ist getestet: Backend und Pilot kommen rein, anonyme Clients
  und falsche Passwörter werden abgewiesen. Die Benutzer liegen danach in `data/emqx`. Passwortänderungen in `.env`
  deshalb auch im Dashboard nachziehen (oder `data/emqx` löschen und neu starten).
- **RC Plus 2 erscheint nicht, im Log `CloudSDKException ... DeviceEnum`:** Patch nicht angewendet.
  `runtime/upstream/` löschen und `./setup.sh` neu ausführen.
- **Fotos werden nicht hochgeladen, obwohl Auto-Upload an ist:** Nach einem Drohnenwechsel am selben RC kann die
  Upload-Warteschlange von Pilot 2 haengen (im Zugriffsprotokoll `edge/logs/access_log.*` nur `POST .../sts`,
  kein `fast-upload`). Pilot 2 komplett beenden und neu starten, danach laedt er die Fotos hoch
  (beobachtet mit Mavic 3E am RC Pro Enterprise, 02.10.2026).
- **Medien-Upload schlägt fehl:** `minio-init` muss mit Exit 0 enden (`docker compose logs minio-init`);
  `SERVER_HOST:9000` muss vom Controller aus erreichbar sein.
- **Neuer Login in bestehender Installation:** Die Bootstrap-CSV von EMQX und die SQL-Dateien greifen nur beim
  allerersten Start. Neue Benutzer (z. B. `pilot2`) zusaetzlich per EMQX-Dashboard/API und SQL anlegen.
- **SQL-Änderungen greifen nicht:** MySQL war schon initialisiert. Dann `runtime/initdb/02_*.sql` und `03_*.sql`
  per `docker compose exec -T mysql mysql -uroot -p... < datei.sql` einspielen.

## Datensicherung

`edge/backup.sh` sichert täglich um 03:15 (Cron, eingerichtet mit `./backup.sh --install-cron`) nach
`BACKUP_DIR` (Standard `/mnt/hc4backup/aeronexus`, eigene NVMe, nicht die SD-Karte):

| Was | Wohin | Aufbewahrung |
|---|---|---|
| MySQL `cloud_sample` + `mapping` (konsistenter Dump im laufenden Betrieb) | `mysql/aeronexus-<Zeit>.sql.gz` | `BACKUP_KEEP_DAYS` (14) |
| alle MinIO-Buckets (Fotos, Ergebnisse, Farming) | `minio/<bucket>/` | Spiegel, inkrementell; in MinIO gelöschte Dateien bleiben erhalten |
| `edge/.env` (alle Passwörter) | `config/env-<Zeit>` (nur für den Besitzer lesbar) | wie MySQL |

Kontrolle: `BACKUP_DIR/last-success` (Zeitpunkt des letzten erfolgreichen Laufs), Protokoll in `BACKUP_DIR/backup.log`.

**Wiederherstellung prüfen** (fasst den laufenden Stack nicht an, etwa monatlich):
`./restore.sh --test` spielt die neueste Sicherung in Wegwerf-Container ein und vergleicht Zeilen pro Tabelle
sowie Objekte und Bytes pro Bucket mit dem Live-Stand.

**Wiederherstellen** in den laufenden Stack: `./restore.sh --yes [--dump DATEI]`. Stoppt api/mapping/farming,
ersetzt die Datenbanken, schreibt die Objekte zurück und startet alles wieder.

**Neuer Rechner / SD-Karte defekt:** Repo klonen, `BACKUP_DIR/config/env-<Zeit>` nach `edge/.env` kopieren
(ggf. `SERVER_HOST` anpassen), `./setup.sh`, `docker compose up -d --build`, dann `./restore.sh --yes`.

**MySQL-Version wechseln** (ein Downgrade ist bei MySQL nicht möglich): vorher `./backup.sh`, dann
`MYSQL_IMAGE=mysql:<neu> ./restore.sh --test`, MySQL mit `SET GLOBAL innodb_fast_shutdown = 0` stoppen und
`edge/data/mysql` kalt kopieren. So wurde am 02.10.2026 von 8.0.39 auf 8.4.11 umgestellt; die Kopie des
8.0-Datenverzeichnisses liegt unter `BACKUP_DIR/mysql-datadir-8.0.39`.

Die Sicherung liegt im selben Gerät wie der Server. Gegen Brand/Diebstahl hilft nur eine zusätzliche Kopie
ausser Haus (z. B. `BACKUP_DIR` per Borg/rsync auf ein externes Ziel).

## Sicherheit

Nur für LAN/VPN gedacht. Alles läuft unverschlüsselt (HTTP, MQTT ohne TLS).

Absicherung im LAN:
- MQTT: Anmeldung Pflicht; Pilot-Konten dürfen nur die Geräteseite der DJI-Topics nutzen
  (`telemetry/emqx/acl.conf`): keine Befehle im Namen des Servers, keine fremde Telemetrie, keine Wildcards.
- Livestream: Senden und Abspielen nur mit Zugangsdaten (siehe Livestream).
- Admin-Oberflächen (EMQX, MinIO-Konsole, MediaMTX-API) nur auf dem Server selbst.
- Mapping/Farming nehmen Browser-Anfragen (CORS) nur von der Web-Oberfläche an.
- Login-Tokens werden nicht mehr in Zugriffslogs geschrieben.

Für Zugriff über 4G: Reverse-Proxy mit TLS (z. B. Caddy) und WireGuard, MQTT auf 8883 mit TLS.
MinIO CE bekommt keine Updates mehr und wird nicht mehr als Image verteilt; AeroNexus baut es aus dem
gepinnten Quellcode (`evidence/docker/minio`). Nicht ins Internet exponieren.

## Phase 2

- TLS über Caddy (Vorlage liegt in `caddy/`, noch nicht eingebunden, siehe unten)
- Ersatz für MinIO prüfen (muss STS AssumeRole können)
- Thing-Model-Felder der Matrice 4T (z. B. zusätzliche Kameramodi) gegen die aktuelle Cloud-API-Doku abgleichen

## TLS (in Arbeit)

`caddy/` enthält eine geprüfte Caddyfile-Vorlage (ein Hostname für Web, API, MQTT über WSS und Livestream,
ein zweiter für S3) und ein Dockerfile mit DNS-01-Modul (Cloudflare oder DuckDNS), das gegen Caddy 2.10.2 baut.
Noch offen: Einbindung in Compose und setup.sh.

Wichtig vorab: DJI nennt für die Cloud API nur Zertifikate von GoDaddy und Cloudflare als unterstützt.
Entwickler berichten, dass MQTT über `ssl://` aus Pilot 2 nicht funktioniert, über `wss://` aber schon.
Deshalb ist MQTT über WSS (Port 443, Pfad `/mqtt`) vorgesehen.

## Lizenz

Upstream-Code von DJI unter MIT. Patches und Deployment-Dateien in diesem Repo ebenfalls MIT.
