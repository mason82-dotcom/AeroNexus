# AeroNexus Control

Web-Oberfläche auf Basis von Cloud-API-Demo-Web (Vue 3, Vite 2). Lagebild, Geräte, Livestream,
Medien, Wegpunkte, Karte. Enthält auch die Login-Seite für DJI Pilot 2 (`/pilot-login`).

- `patches/` – Änderungen gegen Upstream
- `docker/` – Build (Node 16) und Auslieferung (nginx)

Eigene Bausteine in den Patches: `src/vendors/amap-leaflet-shim.ts` (Karte),
`src/vendors/whep-player.ts` und `src/vendors/live-player.ts` (Livestream).
