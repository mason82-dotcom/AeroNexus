# AeroNexus Mapping Tool

Aktuell:
- Leaflet-Karte mit OpenStreetMap und Satellitenbild, Annotationen (Pin, Linie, Polygon) und Flugzonen,
  synchron mit Pilot 2.
- Mapping-Dienst `service/` (Python 3.12, FastAPI, Port 6790): Auftraege aus den von Pilot 2 hochgeladenen
  Bildern, Pull-Schnittstelle fuer einen x64-Rechenknoten, Ergebnis-Layer (Kacheln aus MinIO).
- Web-Seite "Mapping" (control-Patch 0007): Auftraege, Bildauswahl, Ergebnis-Layer mit Transparenz.

Dokumente:
- `docs/AeroNexus_Mapping_Studie.md`: Architektur- und Machbarkeitsstudie
- `docs/adr-001-architektur.md`: Architekturentscheidung (eigener Dienst, Pull-Agent, NodeODM-API)
- `docs/agent-api.md`: Schnittstelle fuer den compute-agent

Test ohne Rechenknoten: `python3 mapping-tool/service/tools/sim_agent.py`

Offline-Basiskarte: `basemaps/fetch-basemap.sh` (Protomaps-Vektorkarte als PMTiles, Region per GeoJSON;
Details in edge/README.md, Abschnitt Karte).

Geplant: Messwerkzeuge, WPML-Flugplanung.
