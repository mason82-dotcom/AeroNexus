# AeroNexus Pilot

Anbindung der Fernsteuerungen über **DJI Pilot 2** (Cloud Services → Open Platform).
Pilot 2 lädt `http://<server>:8085/pilot-login`, prüft die DJI-Lizenz per JSBridge und verbindet
MQTT, API, Karte, Medien, Wegpunkte und Livestream.

| Fernsteuerung | Gerätecode | Fluggeräte |
|---|---|---|
| DJI RC Pro Enterprise | 2-144-0 | Mavic 3E (0-77-0), 3T (0-77-1), 3M (0-77-2) |
| DJI RC Plus 2 Enterprise | 2-174-0 | Matrice 4E (0-99-0), 4T (0-99-1) |

Geplant: eigene Android-App auf Basis von DJI MSDK v5 für Funktionen, die Pilot 2 nicht bietet.
