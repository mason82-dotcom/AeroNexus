# ADR-001: Farming Guide auf Basis der Mapping-Kette

- Status: angenommen
- Datum: 2026-10-02
- Bezug: `mapping-tool/docs/adr-001-architektur.md`, Studie Abschnitt "Multispektral (M3M)" und Phase 6

## Kontext

Die Mavic 3M liefert je Aufnahme vier 16-bit-Baender (Green 560 nm, Red 650 nm, Red Edge 730 nm,
NIR 860 nm) plus RGB. ODM rechnet daraus seit 3.5.3 ein Multiband-Orthofoto
(`--radiometric-calibration camera+sun`, `--primary-band NIR`). Die Kalibrierung gilt als experimentell,
die DJI-Sensoren sind nur relativ kalibriert. Ein Reflektanzpanel ist nicht vorhanden.

Ziel laut Nutzer: zuerst Bestandsueberwachung (Vitalitaetskarten, Vergleich von Fluegen), danach Zonen
und Applikationskarten (Shapefile, ISO-XML, KML/GeoJSON).

## Entscheidungen

1. **Kein eigener Dienst.** Der Farming Guide nutzt Auftraege, Rechenknoten und Layer des Mapping-Tools.
   Neu sind nur ein Auftragsprofil `multispectral` und die Indexberechnung im compute-agent.
2. **Indizes als normierte Differenzen** zweier Baender, definiert in `farming-guide/indices.json`
   (keine auswertbaren Formeln). Erste Auswahl: NDVI, NDRE, GNDVI.
3. **Berechnung auf dem x64-Knoten** (numpy/GDAL im Agent-Image): Float32-COG je Index in EPSG:25832,
   eingefaerbte RGBA-Kacheln (`gdaldem color-relief`, Palette aus `indices.json`), Statistik
   (Mittel, Streuung, Perzentile 10/50/90) je Index im Manifest.
4. **Mehrere Layer je Auftrag.** Das Manifest bekommt eine Liste `layers` (Art, Legende, Statistik,
   Kennzeichen "relativ"); der Pi legt je Eintrag einen `map_layer` an (Migration 0003).
5. **Kennzeichnung "relativ"** in Layername, Legende und API, solange kein Panel-Workflow existiert.
6. **Baender per Banddescription** des ODM-Orthofotos zuordnen (Namen aus `indices.json` -> `bands`),
   nicht per Reihenfolge. Fehlt ein Band, entfaellt der Index mit Hinweis im Log, statt den Auftrag
   abzubrechen.

## Folgen

- Die echte ODM-Multispektralrechnung ist erst mit einem M3M-Flug und dem Windows-Knoten pruefbar;
  bis dahin Test mit simuliertem 4-Band-Orthofoto (`compute-agent/tools/fake_nodeodm.py --multispectral`).
- Stufe B (Zonen, Exporte) baut auf den Index-COGs auf.
