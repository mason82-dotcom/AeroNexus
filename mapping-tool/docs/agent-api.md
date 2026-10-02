# Mapping Agent API

Schnittstelle zwischen dem Mapping-Dienst auf dem Pi (`mapping-tool/service`) und dem
`compute-agent` auf dem x64-Knoten. Architektur: `adr-001-architektur.md`.

Basis-URL: `http://<SERVER_HOST>:6790/api/mapping/agent`
Authentifizierung: `Authorization: Bearer <MAPPING_AGENT_TOKEN>` bei jedem Aufruf.
Format: JSON, UTF-8. Zeiten in UTC, ISO 8601.

## Ablauf

```
loop alle 15-30 s:
  POST /claim                       -> 204 (nichts zu tun) oder 200 (Auftrag + Bild-URLs)
  Bilder per GET von den vorsignierten URLs laden
  NodeODM-Task starten (localhost:3000)
  waehrend der Rechnung alle <= lease/3 Sekunden: POST /jobs/{id}/heartbeat
  Ergebnisse erzeugen (COG, Kacheln, manifest.json)
  POST /jobs/{id}/upload-urls      -> vorsignierte PUT-URLs
  Dateien per PUT hochladen
  POST /jobs/{id}/complete          (oder /fail)
```

## Status eines Auftrags

| Status | Bedeutung |
|---|---|
| `QUEUED` | wartet auf einen Agenten |
| `CLAIMED` | einem Agenten zugeteilt, noch kein Heartbeat |
| `RUNNING` | Agent hat mindestens einen Heartbeat gesendet |
| `DONE` | `complete` angenommen, Layer angelegt |
| `FAILED` | `fail` gemeldet oder Lease 3-mal abgelaufen |
| `CANCELED` | in der Web-UI abgebrochen. Ein laufender Agent bekommt beim naechsten Aufruf `409` mit `detail: "job canceled by user"`, bricht ab und beendet seinen NodeODM-Task |

Lease: Laeuft `lease_until` ab, ohne dass ein Heartbeat kam, setzt der naechste `claim` (von
irgendeinem Agenten) den Auftrag wieder auf `QUEUED` (`attempts` + 1). Ab `attempts >= 3` wird er
`FAILED`. Ein Agent, dessen Lease abgelaufen ist, bekommt auf `heartbeat`, `upload-urls`, `complete`
und `fail` die Antwort `409` und muss den Auftrag verwerfen.

## Endpunkte

### POST /claim

```json
{
  "agent_id": "x64-werkstatt",
  "lease_seconds": 600,
  "capabilities": {"ram_gb": 64, "gpu": "RTX 4070 12GB", "engine": "nodeodm 3.6.2"}
}
```

`lease_seconds`: optional, 10 bis 3600, Standard 600.

Antwort `204`: kein Auftrag. Antwort `200`:

```json
{
  "job": {
    "id": "6f1c...",
    "name": "Halle Nord",
    "status": "CLAIMED",
    "options": {"profile": "standard", "odm": {"feature-quality": "high"}},
    "attempts": 0,
    "lease_until": "2026-10-02T10:15:00Z"
  },
  "images": [
    {"key": "wayline/DJI_20261001223818_0001_D.JPG",
     "url": "http://192.168.178.63:9000/dji-cloud/wayline/DJI_..._D.JPG?X-Amz-...",
     "expires_in": 3600}
  ],
  "results_prefix": "mapping-results/6f1c.../"
}
```

Die Bild-URLs gelten `PRESIGN_TTL_SECONDS` (Standard 3600). Laufen sie vor dem Download ab:
erneut `claim` ist nicht noetig, `POST /jobs/{id}/image-urls` liefert frische URLs.

### POST /jobs/{id}/image-urls

Body: `{"agent_id": "..."}`. Antwort: `{"images": [...]}` wie bei `claim`.

### POST /jobs/{id}/heartbeat

```json
{"agent_id": "x64-werkstatt", "progress": 42.5, "message": "opensfm: matching", "lease_seconds": 600}
```

Antwort `200`: `{"status": "RUNNING", "lease_until": "..."}`. Setzt `CLAIMED` auf `RUNNING`.
`progress` 0 bis 100. `message` hoechstens 500 Zeichen, keine Secrets.

### POST /jobs/{id}/upload-urls

```json
{"agent_id": "x64-werkstatt", "paths": ["orthophoto.tif", "tiles/18/137412/89512.png", "manifest.json"]}
```

Pfade sind relativ zu `mapping-results/{jobId}/`. Erlaubt: `A-Z a-z 0-9 . _ - /`, kein `..`,
kein fuehrendes `/`, hoechstens 1000 Pfade pro Aufruf. Antwort:

```json
{"urls": {"orthophoto.tif": "http://192.168.178.63:9000/mapping-results/6f1c.../orthophoto.tif?X-Amz-..."},
 "expires_in": 3600}
```

Upload: `PUT <url>` mit dem Dateiinhalt als Body. Content-Type ist frei (z. B. `image/png`).

### POST /jobs/{id}/complete

```json
{
  "agent_id": "x64-werkstatt",
  "manifest": {
    "crs": "EPSG:32632",
    "bounds_wgs84": [8.585, 49.155, 8.595, 49.160],
    "files": [
      {"path": "orthophoto.tif", "kind": "orthophoto_cog", "sha256": "ab12...", "size": 123456789},
      {"path": "dsm.tif", "kind": "dsm_cog", "sha256": "...", "size": 1}
    ],
    "tiles": {"path": "tiles", "format": "png", "minzoom": 14, "maxzoom": 20}
  }
}
```

Der Dienst prueft, dass jede Datei in `files` in MinIO existiert (und bei `tiles` mindestens eine
Kachel unter dem Pfad), legt pro Datei einen `mapping_result`-Eintrag an und bei `tiles` einen
`map_layer` (XYZ, Name = Auftragsname). Antwort `200`: `{"status": "DONE", "layer_id": "..."}`.
Fehlende Dateien: `422` mit Liste.

`kind`-Werte (frei erweiterbar): `orthophoto_cog`, `dsm_cog`, `dtm_cog`, `report`, `log`, `other`.
`bounds_wgs84` ist `[west, south, east, north]`.

### POST /jobs/{id}/fail

```json
{"agent_id": "x64-werkstatt", "error": "ODM exit code 1: not enough images matched"}
```

Antwort `200`: `{"status": "FAILED"}`. `error` hoechstens 2000 Zeichen.

## Fehlercodes

| Code | Bedeutung |
|---|---|
| 401 | Token fehlt oder falsch |
| 404 | Auftrag unbekannt |
| 409 | Auftrag gehoert nicht (mehr) diesem Agenten oder ist nicht aktiv |
| 422 | Ungueltige Eingabe (Pfad, Manifest, fehlende Dateien) |

## Simulation

`python3 mapping-tool/service/tools/sim_agent.py` spielt den Ablauf ohne x64-Knoten durch
(nur Python-Standardbibliothek, liest `edge/.env`): Auftrag anlegen, Claim mit kurzer Lease,
Lease ablaufen lassen (409 fuer den alten Agenten, Auftrag wieder `QUEUED`), erneuter Claim,
Upload erzeugter Testkacheln und `manifest.json`, `complete`, Layer und Kachel pruefen.
