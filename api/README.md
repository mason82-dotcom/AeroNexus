# AeroNexus API

Backend auf Basis von DJI-Cloud-API-Demo (Java 11, Spring Boot 2.7). Spricht das Cloud-API-Protokoll
mit DJI Pilot 2 (MQTT, HTTP, WebSocket) und stellt die REST-API für AeroNexus Control bereit.

- `patches/` – Änderungen gegen Upstream, angewendet von `edge/setup.sh`
- `docker/Dockerfile` – Multi-Stage-Build (Maven → Temurin 11 JRE)
- `docker/application.yml` – Konfiguration, alle Adressen und Secrets aus Umgebungsvariablen

Neuen Patch erzeugen: in `edge/runtime/upstream/backend` committen, dann
`git format-patch <letzter-patch-commit>..HEAD --start-number <n> -o ../../../../api/patches`.
