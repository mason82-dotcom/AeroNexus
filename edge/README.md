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
| 9000 / 9001 | MinIO S3 / Konsole | Pilot 2 (Uploads) / Admin |
| 1935 | RTMP-Ingest | Pilot 2 |
| 8889 / 8888 / 8554 | WebRTC (WHEP/WHIP) / HLS / RTSP | Browser, VLC |
| 8189/udp | WebRTC-Medien | Browser |
| 9997 | MediaMTX API | Admin (nur vom Server selbst, MediaMTX-Default) |
| 18083 | EMQX Dashboard | Admin (User `admin`) |

## Controller verbinden

1. Controller ins Netz bringen, in dem `SERVER_HOST` erreichbar ist.
2. Pilot 2 öffnen, auf der Startseite **Cloud Services** wählen und dort die Drittanbieter-Plattform
   (**Open Platform**) auswählen.
3. URL eingeben: `http://<SERVER_HOST>:8085/pilot-login`
4. Mit `pilot` und `PILOT_LOGIN_PASSWORD` anmelden. Die Seite prüft die DJI-License per JSBridge
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

Der öffentliche OSM-Kachelserver ist für gelegentliche private Nutzung gedacht. Für Dauerbetrieb oder
Einsätze ohne Internet einen eigenen Kachelserver eintragen.

## Livestream

Im Web-UI unter **Livestream**: Fluggerät, Kamera und Qualität wählen, Typ **RTMP**, Start.
Das Video erscheint direkt auf der Seite, darunter steht der Player-Status.

Ablauf: Pilot 2 schickt per RTMP an MediaMTX (`rtmp://<SERVER_HOST>:1935/live/<SN>-<Payload>`),
der Browser holt den Stream per WebRTC (WHEP, ca. 0,3 s Verzögerung). Kann der Browser H.264 nicht
über WebRTC dekodieren, wechselt der Player automatisch auf HLS (2 bis 4 s Verzögerung).
Die ersten Sekunden nach dem Start meldet der Player "waiting for stream", bis die Drohne sendet.

Typ **WEBRTC** (WHIP direkt vom Controller zu MediaMTX) ist vorbereitet, aber experimentell: ob Pilot 2
das für Mavic 3E/3T/3M und Matrice 4T anbietet, hängt von Firmware und Pilot-Version ab. RTMP ist der Standard.

Weitere Wege zum selben Stream:
- Liste aktiver Streams (auf dem Server): `curl http://127.0.0.1:9997/v3/paths/list`
- Eigener Browser-Tab: `http://<SERVER_HOST>:8889/live/<SN>-<Payload>`
- VLC: `rtsp://<SERVER_HOST>:8554/live/<SN>-<Payload>`

## Fehlersuche

- **Pilot verbindet MQTT nicht:** In den EMQX-Dashboard-Clients nachsehen. Die Bootstrap-Authentifizierung
  (Benutzer aus `runtime/emqx/auth-bootstrap.csv`) ist getestet: Backend und Pilot kommen rein, anonyme Clients
  und falsche Passwörter werden abgewiesen. Die Benutzer liegen danach in `data/emqx`. Passwortänderungen in `.env`
  deshalb auch im Dashboard nachziehen (oder `data/emqx` löschen und neu starten).
- **RC Plus 2 erscheint nicht, im Log `CloudSDKException ... DeviceEnum`:** Patch nicht angewendet.
  `runtime/upstream/` löschen und `./setup.sh` neu ausführen.
- **Medien-Upload schlägt fehl:** `minio-init` muss mit Exit 0 enden (`docker compose logs minio-init`);
  `SERVER_HOST:9000` muss vom Controller aus erreichbar sein.
- **SQL-Änderungen greifen nicht:** MySQL war schon initialisiert. Dann `runtime/initdb/02_*.sql` und `03_*.sql`
  per `docker compose exec -T mysql mysql -uroot -p... < datei.sql` einspielen.

## Sicherheit

Nur für LAN/VPN gedacht. Alles läuft unverschlüsselt (HTTP, MQTT ohne TLS).
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
