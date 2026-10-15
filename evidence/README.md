# AeroNexus Evidence

Medienablage: Pilot 2 lädt Fotos und Videos direkt per S3 in MinIO hoch (STS-Zugangsdaten vom Backend).

- `scripts/minio-init.sh` – legt Bucket und den STS-Benutzer an

## Beweissicherung (Mapping-Dienst, `mapping-tool/service/app/evidence.py`)

- Ein Hintergrundjob versiegelt jede neue Datei aus `cloud_sample.media_file` einmal: SHA-256 des Objekts in
  MinIO, Größe, Drohne, Kamera, Upload- und Versiegelungszeit (UTC) in `mapping.evidence_record`
  (Migration 0005). Einträge werden nie geändert oder gelöscht.
- Die Einträge bilden eine Hash-Kette: `record_hash` = SHA-256 über die kanonische Form des Eintrags
  einschließlich `prev_hash`. Eine geänderte Zeile, ein gelöschter oder umsortierter Eintrag bricht die Kette.
- Nachweisgrenze: Das Register belegt, dass eine Datei byte-gleich zu ihrem Stand bei der Versiegelung ist;
  was vor dem Upload geschah, kann es nicht belegen.

API (Web-Login, `x-auth-token`):

| Endpunkt | Zweck |
|---|---|
| `POST /api/mapping/evidence/status` | Siegel-Status für bis zu 500 `file_ids` (Medienliste) |
| `GET /api/mapping/evidence/chain` | Prüfung der gesamten Kette (Anzahl, Kopf-Hash) |
| `GET /api/mapping/evidence/{file_id}/verify` | Datei neu hashen: `intact` / `modified` / `missing`, Eintrag `ok` / `broken` |
| `GET /api/mapping/evidence/report?file_ids=a,b&fmt=json\|csv` | Prüfbericht mit Neuprüfung aller Dateien und Kettenstand |

Geplant: PDF-Prüfbericht, Siegel-Status und Export in der Medienliste der Weboberfläche, unveränderbare
Ablage (MinIO Object Lock, nur bei neuen Buckets mit Versionierung möglich).
