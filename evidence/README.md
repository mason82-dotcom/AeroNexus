# AeroNexus Evidence

Medienablage: Pilot 2 lädt Fotos und Videos direkt per S3 in MinIO hoch (STS-Zugangsdaten vom Backend).

- `scripts/minio-init.sh` – legt Bucket und den STS-Benutzer an

Geplant: Beweissicherung mit SHA-256 je Datei beim Upload, Metadaten (Zeit, Position, Gerät, Pilot),
unveränderbare Ablage (Object Lock) und exportierbarer Prüfbericht.
