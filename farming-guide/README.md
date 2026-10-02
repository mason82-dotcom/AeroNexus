# AeroNexus Farming Guide

Auswertung der Multispektralbilder der Mavic 3M fuer die Bestandsueberwachung.

Stufe A (umgesetzt): Mapping-Auftrag mit Profil "Multispectral" (M3M-Baender G/R/RE/NIR) ->
ODM-Multiband-Orthofoto auf dem x64-Rechenknoten -> NDVI, NDRE, GNDVI als COG und farbige Kartenlayer
mit Legende und Statistik in der Web-UI (Mapping). Werte sind **relativ** (kein Kalibrierpanel).

Stufe B: Dienst `service/` (Port 6791, Standardbibliothek + GDAL im gepinnten GDAL-Image):
Zonenkarten aus einem Index-Layer (3-5 Klassen, Quantile oder gleiche Intervalle, Rasterweite =
Arbeitsbreite, Kleinstflaechen per Sieve), Ausbringmengen je Zone (kg/ha oder l/ha) und Export als
Shapefile (zip, WGS84), KML, GeoJSON und ISO-XML (ISO 11783-10 TASKDATA, Grid Typ 1 + Behandlungszonen,
DDI 0006/0001). **ISO-XML ist ungeprueft am Terminal: vor dem Einsatz testen.**

- `indices.json`: Index-Definitionen (Bandpaare, Wertebereich, Farbpalette), genutzt vom compute-agent
- `docs/adr-001-farming-guide.md`: Architekturentscheidung
