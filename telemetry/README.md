# AeroNexus Telemetry

Echtzeitdaten von Fernsteuerung und Fluggerät.

- **MQTT (EMQX 5.8.6):** Telemetrie (OSD/State), Befehle, Ereignisse. Nutzer werden aus `.env` angelegt.
- **Livestream (`mediamtx/`, MediaMTX 1.15.1):** RTMP-Eingang von Pilot 2, Ausgabe als WebRTC (WHEP), HLS und RTSP.

Geplant: Flugprotokolle aus OSD-Daten aufzeichnen und abspielen.
