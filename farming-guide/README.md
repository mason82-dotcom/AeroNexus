# AeroNexus Farming Guide

Auswertung der Multispektralbilder der Mavic 3M fuer die Bestandsueberwachung.

Stufe A (umgesetzt): Mapping-Auftrag mit Profil "Multispectral" (M3M-Baender G/R/RE/NIR) ->
ODM-Multiband-Orthofoto auf dem x64-Rechenknoten -> NDVI, NDRE, GNDVI als COG und farbige Kartenlayer
mit Legende und Statistik in der Web-UI (Mapping). Werte sind **relativ** (kein Kalibrierpanel).

Stufe B (geplant): Zonenkarten (Klassen aus einem Index), Ausbringmengen je Zone, Export als
Shapefile, ISO-XML (ISOBUS TASKDATA) und KML/GeoJSON.

- `indices.json`: Index-Definitionen (Bandpaare, Wertebereich, Farbpalette), genutzt vom compute-agent
- `docs/adr-001-farming-guide.md`: Architekturentscheidung
