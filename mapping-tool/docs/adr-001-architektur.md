# ADR-001: Architektur des Mapping-Tools

- Status: angenommen
- Datum: 2026-10-02
- Grundlage: `AeroNexus_Mapping_Studie.md` (Stand 2. Oktober 2026)

## Kontext

AeroNexus soll aus Drohnenbildern, die Pilot 2 automatisch nach MinIO hochlaedt, Orthofotos und
Hoehenmodelle rechnen und als Layer in der Leaflet-Karte zeigen. Photogrammetrie braucht viel RAM
(16 GB fuer 250 Bilder, 64 GB fuer 1500) und laeuft praktisch nur auf x64. Der Raspberry Pi 5 betreibt
bereits den Edge-Stack (DJI-Backend, MySQL, Redis, EMQX, MinIO, MediaMTX, Web-UI).

Die Studie empfiehlt ein neues Modul im DJI-Backend (Spring Boot, Flyway). Das Backend ist aber ein
Fork-by-Patch des archivierten DJI-Demos: Jede Aenderung dort ist ein Patch gegen fremden Code.

## Entscheidungen

### 1. Eigener Dienst statt Patch am DJI-Backend (Abweichung von der Studie)

Das Mapping-Backend ist ein eigener Dienst `mapping-tool/service`:

- Python 3.12, FastAPI, alle Abhaengigkeiten in `requirements.txt` exakt gepinnt (inkl. transitiver),
  Basis-Image per Digest gepinnt.
- Eingebunden in `edge/docker-compose.yml` als Dienst `mapping`, Port 6790.
- Eigene Datenbank `mapping` im vorhandenen MySQL, Migrationen als nummerierte SQL-Dateien
  (`service/migrations/NNNN_*.sql`), die der Dienst beim Start anwendet (Tabelle `schema_migrations`).
- Web-Logins: Der Dienst prueft das JWT des DJI-Backends (HMAC256, gemeinsames `JWT_SECRET`,
  Header `x-auth-token`). Kein zweites Benutzerkonzept.
- Medienliste: liest `cloud_sample.media_file` nur lesend, gefiltert auf den Workspace aus dem JWT.

Gruende: kein weiterer Patch gegen archivierten Fremdcode, unabhaengig deploybar und testbar,
Python passt zur Geo-Werkzeugkette (GDAL, rasterio) der spaeteren Phasen.

Bekannte Vereinfachung (MVP): Der Dienst verbindet sich als MySQL-root (Datenbank anlegen,
Migrationen, Lesezugriff auf `cloud_sample`). Ein eigener MySQL-Benutzer folgt, sobald es ein
Init-Verfahren fuer bereits initialisierte MySQL-Datenverzeichnisse gibt.

### 2. Pull-Agent statt Push

Ein x64-Rechenknoten betreibt einen eigenen `compute-agent`. Er fragt den Pi ab (`claim`), nicht
umgekehrt. Der Knoten darf jederzeit aus sein; Wake-on-LAN ist optional (Phase 2,
`WOL_MAC`/`WOL_BROADCAST` sind in `.env` schon vorgesehen).

- Auftraege bekommen eine **Lease**. Der Agent verlaengert sie per Heartbeat. Laeuft sie ab
  (Knoten aus, abgestuerzt), setzt der Pi den Auftrag beim naechsten Claim wieder auf `QUEUED`.
  Nach 3 abgelaufenen Leases wird der Auftrag `FAILED`.
- Der Agent bekommt keine Dauer-Credentials: Eingangsbilder ueber **vorsignierte GET-URLs**,
  Ergebnisse ueber **vorsignierte PUT-URLs** nur unter `mapping-results/{jobId}/`.
- Agent-Authentifizierung: `Authorization: Bearer <MAPPING_AGENT_TOKEN>`.
- Schnittstelle: `agent-api.md`.

### 3. Kopplung nur an die NodeODM-REST-API, Engine ODM 3.6.x per Digest

Der Agent steuert die Engine ausschliesslich ueber die NodeODM-API (`/task/new/init|upload|commit`,
`/task/{uuid}/info`, `/task/{uuid}/download/...`). NodeODM laeuft nur an `127.0.0.1` des x64-Knotens
und mit `--token`.

Gepinnte Engine-Linie (ermittelt 2026-10-02 per `docker buildx imagetools inspect`):

| Image | Digest |
|---|---|
| `opendronemap/nodeodm:3.6.2` | `sha256:fcd99eb23d8db194a7d1dc95f029f518283b3d6be32dd64422395f4e7af56720` |
| `opendronemap/odm:3.6.2` | `sha256:a96f56dbc4f775e352fb2b3acc472bb59f456c0948a699eb1c433659dccfbdf7` |
| `opendronemap/odm:3.6.2-gpu` | `sha256:4588fbf8b3574752dce9903345ecfb98d71b20c7834d376a84bfce2f8315ef28` |
| `opendronemap/odm:3.6.1-gpu` | `sha256:b53124846c00846fb05cc484fb61a314e55b19ae498e596446f4b82e02abb6d1` |

GPU: Das fertige `opendronemap/nodeodm:gpu` ist laut NodeODM-Issue #271 veraltet und faellt
unbemerkt auf CPU zurueck. Fuer GPU wird NodeODM aus dem gepinnten `odm:3.6.2-gpu` selbst gebaut,
und der Agent prueft im Health-Check, ob SIFT wirklich auf der GPU laeuft.

### 4. ODX/NodeODX als Alternative

Seit der Trennung am 6. April 2026 pflegt WebODM die Forks ODX/NodeODX mit derselben NodeODM-API.
Sie bleiben austauschbare Alternative: Wechsel = anderes Image im Agent-Compose, keine Codeaenderung.

NodeODX hat auf Docker Hub keine Versions-Tags (nur `latest`, `master`, `gpu`). Pin daher per Digest:
`webodm/nodeodx@sha256:0c02fa1e4dcbbc93bd4c33c4a8c28e30b58cfc8d770d76d4f8b578271a44721d`
(`latest`, Stand 2026-09-24). Die in der Studie genannte "NodeODX 2.3.1" existiert nicht als Image-Tag.

### 5. Darstellung: vorberechnete Kacheln aus MinIO

Der Agent erzeugt Web-Mercator-XYZ-Kacheln (PNG mit Transparenz) und laedt sie nach
`mapping-results/{jobId}/tiles/{z}/{x}/{y}.png`. Leaflet zeigt sie als `L.tileLayer` mit
Transparenzregler. Die Kachel-URL zeigt auf den Mapping-Dienst, der pro Kachel auf eine kurzlebige
vorsignierte MinIO-URL umleitet (302). Der Bucket bleibt privat. COGs bleiben als Download-Artefakt.
Leaflet bleibt (Phasen 1 bis 3), MapLibre wird in Phase 4 geprueft.

## Folgen

- Neue Secrets in `edge/.env`: `MAPPING_AGENT_TOKEN`, eigener MinIO-Benutzer `MAPPING_MINIO_USER`/
  `MAPPING_MINIO_PASSWORD` (Rechte: lesen `dji-cloud`, lesen/schreiben `mapping-results`, `basemaps`).
- Neuer Port 6790 (Mapping-API fuer Web-UI und Agent).
- MinIO-Buckets `mapping-results` und `basemaps`, CORS mit `Range`/`Content-Range`.
- Lizenz: NodeODM/ODM (AGPL-3.0) nur als unveraenderter, separater Container ueber HTTP.
  Kein Code aus WebODM in AeroNexus.
