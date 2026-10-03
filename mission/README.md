# AeroNexus Mission

Aktuell: Wegpunkt-Bibliothek (KMZ/WPML hochladen in Control, Pilot 2 synchronisiert sie).
Mit Fernsteuerung werden Missionen in Pilot 2 gestartet; Start aus der Cloud geht nur mit Dock.

Geplant: Missionsplanung im Browser (Raster, Fassade, Korridor) mit WPML-Export.

## Einsatzplanung

Umgesetzt im Mapping-Dienst (`mapping-tool/service/app/missions.py`, Tabelle `mission`), Oberfläche im Menü
**Aufgabenpläne** (Web-Patch 0027). Mit Fernsteuerung (Pilot 2) ist ein Fernstart von Routen nicht möglich (nur
mit DJI Dock); ein Einsatz plant daher alles um den Flug herum: Zeit, Ort, Zweck, Routen, Drohne, Pilot,
Checkliste je Zweck und Status (geplant → bereit → geflogen → ausgewertet / abgesagt). Der Pilot sieht seine
Einsätze des Tages auf der Pilot-2-Cloudseite der Fernsteuerung und hakt die Checkliste dort ab.
