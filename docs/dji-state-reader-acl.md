# Read-only DJI State topic für AeroNexusAdvanced

DJI Pilot 2 sendet `thing/product/{sn}/osd` regelmäßig und
`thing/product/{sn}/state` nur bei Änderungen. AeroNexusAdvanced abonniert
`state` ausschließlich nach explizitem Opt-in. Dieses Änderungspaket fügt
für **aeronexus_osd** ausschließlich das entsprechende Subscribe-Recht hinzu.

## Rechte

| Benutzer | Topic | MQTT-Aktion |
|---|---|---|
| `aeronexus_osd` | `thing/product/+/osd` | subscribe |
| `aeronexus_osd` | `thing/product/+/state` | subscribe |
| `aeronexus_osd` | `thing/product/+/events` | subscribe |
| `aeronexus_osd` | `#` | publish **verboten** |
| `aeronexus_osd` | alle sonstigen Topics | subscribe **verboten** |

Die expliziten Deny-Regeln für `aeronexus_osd` liegen vor den allgemeinen
DJI-Pilot-Regeln. Es gibt keine neuen Rechte für Pilot-2-Geräte, keine
`property/set`-, `services`- oder `drc/down`-Freigabe, keine Änderungen
an Authentisierung oder Zugangsdaten. Die Kontoberechtigung muss weiterhin
`is_superuser=false` haben; ein Superuser würde die ACL umgehen.

## Bestätigter Pi-Betriebsstand (18.10.2026)

Die aktive Installation verwendet den Docker-Container
`aeronexusadvanced-cloud-emqx-1` und NICHT unmittelbar das hier
versionierte `AeroNexus/edge`-Compose-Deployment. Der aktive Host-Bind-Mount
zeigt auf:

```text
/home/mason/Projekte/AeroNexusAdvanced/cloud-server/telemetry/emqx/acl.conf
```

Der Nutzer `aeronexus_osd` darf darin derzeit nur OSD und HMS lesen;
`state` fehlt. Das Mergen dieses PR ändert **nicht automatisch** diese
produktive, möglicherweise separat versionierte ACL. Vor dem Rollout die
aktive Authorizer-Dateiquelle überprüfen: EMQX kann nach Dashboard-/API-
Änderungen `data/authz/acl.conf` statt des eingebundenen `etc/acl.conf`
verwenden. Erst die tatsächlich geladene Quelle patchen oder über die
zuständige Dashboard-Authorizer-Konfiguration aktualisieren.

Keine Ersetzung der bind-gemounteten Datei durch atomisches Umbenennen:
bei einem Datei-Bind-Mount kann ein neuer Host-Inode vom laufenden
Container entkoppelt sein. Datei vorab sichern und nach Read-only-Prüfung
kontrolliert **im bestehenden Inode** aktualisieren; erst bei abgestellter
Drohne und erlaubter Wartungsunterbrechung Broker neu laden bzw. neu starten.

Offizielle Hinweise:
- https://docs.emqx.com/en/emqx/latest/guides/access-control/authz/file.html

## Rollout-Voraussetzungen

Diese GitHub-Änderung aktualisiert **nicht** die produktive EMQX-Instanz.
Der Compose-Stack `edge/docker-compose.yml` bindet
`../telemetry/emqx/acl.conf` als
`/opt/emqx/etc/acl.conf:ro` ein.

Vor Freigabe muss am Pi überprüft werden, dass der *aktive* Broker auf
Port 1884 tatsächlich genau diese Datei verwendet. Bestehende Cloud-API-
Verbindungen und andere Dienstkonfigurationen dürfen nicht blind verändert
werden. Erst nach Freigabe der aktiven ACL und erfolgreichem Abonnement darf
in **AeroNexusAdvanced** die Einstellung

```dotenv
AERONEXUS_OSD_STATE_ENABLED=1
```

aktiviert werden. `AERONEXUS_CLOUD_MQTT_ENABLED` und die vorhandenen
MQTT-Zugangsdaten bleiben unverändert. Die App muss danach
`live_feed.state=subscribed` und
`live_feed.state_topic_enabled=true` melden. Das allein ist **kein**
Nachweis eines tatsächlich eingetroffenen DJI-State-Events.

Die M4T-/RC-Plus-2-Abnahme und die getrennten State-/OSD-Zähler beschreibt
`AeroNexusAdvanced` in `docs/dji-state-m4t-rc-plus2-acceptance.md`
(PR #68). Die Anwendung hat weiter **keine Publish-Rechte**.

## Rollback

Bei `subscription_denied` oder Ausfall des OSD-Lesers zuerst
`AERONEXUS_OSD_STATE_ENABLED=0` in der privaten
AeroNexusAdvanced-`.env` setzen und nur dessen Dienst neu starten.
Danach `live_feed.state=subscribed` und steigende OSD-Zähler überprüfen.
Ein Rücksetzen dieser zusätzlichen ACL-Regel ist erst nach sorgfältiger
Prüfung der aktiven Brokerkonfiguration sinnvoll. Keine produktiven
Datenbanken und kein FlightHub-Zugang müssen dafür verändert werden.
