# AGENTS.md - AeroNexus

Rules for every AI agent on this machine (Hermes, Codex, other Claude sessions). Development rules for
the maintainer live in `CLAUDE.md`; this file is about not breaking the live system.

AeroNexus runs live on this Raspberry Pi (Docker Compose project `aeronexus-edge`, directory `edge/`).
Remote controllers (DJI Pilot 2) are connected to it during flights.

## Do not
- run `docker compose down`, `stop`, `rm`, `restart` or `up <single service>` in `edge/`, or stop,
  remove or rebuild any `aeronexus-edge-*` container. A partial `up` leaves the stack half running
  (happened on 2026-10-02 19:14 and 2026-10-03 01:51).
- replace the user crontab (`echo ... | crontab -`, `crontab file` without the existing lines). The line
  `15 3 * * * .../edge/backup.sh` is the nightly backup and must stay. Add lines, never overwrite.
- change files in this repository, `edge/.env`, `edge/data/` or `edge/runtime/`.
- remove the label `com.centurylinklabs.watchtower.enable=false` or let Watchtower update these containers
  (images are pinned on purpose).
- bind other services to the ports AeroNexus uses: 1884, 1935, 6789, 6790, 6791, 6792, 8084, 8085, 8189/udp,
  8554, 8888, 8889, 9000 (and 9001, 9997, 18083 on 127.0.0.1).

## If something looks wrong
Ask the user (RomaN) instead of restarting. Read-only checks are fine:
`docker compose -f edge/docker-compose.yml ps`, `... logs <service>`.
