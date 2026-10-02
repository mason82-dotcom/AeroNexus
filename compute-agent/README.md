# AeroNexus Compute-Agent

Rechnet Mapping-Auftraege des Pi (Modul `mapping-tool`) auf einem x64-Rechner mit NodeODM/ODM.
Der Agent holt sich Auftraege selbst ab (Pull), der Rechner darf jederzeit aus sein. Laeuft eine
Lease ab, stellt der Pi den Auftrag wieder in die Warteschlange.

Ablauf je Auftrag: Bilder per vorsignierter URL aus MinIO laden, NodeODM-Task rechnen,
Orthofoto/DSM/DTM als COG (Standard EPSG:25832) und Web-Mercator-Kacheln erzeugen, nach
`mapping-results/<jobId>/` hochladen, beim Pi abschliessen. Der Layer erscheint dann in der Web-UI
unter Mapping. Schnittstelle: `mapping-tool/docs/agent-api.md`, Architektur: `mapping-tool/docs/adr-001-architektur.md`.

| Teil | Version (gepinnt) | Lizenz |
|---|---|---|
| Agent (`agent/`) | Python 3.12 Standardbibliothek, GDAL 3.11.4 (Image per Digest) | Projekt |
| NodeODM / ODM (CPU) | `opendronemap/nodeodm:3.6.2@sha256:fcd99eb2...` | AGPL-3.0, unveraendert |
| NodeODM / ODM (GPU) | gebaut aus NodeODM-Commit `d45bc41` (v3.6.2) auf `odm:3.6.2-gpu@sha256:4588fbf8...` | AGPL-3.0, unveraendert |

## Voraussetzungen (Windows mit WSL2)

1. **Docker Desktop** mit WSL2-Backend, unter Einstellungen "Start Docker Desktop when you sign in" aktivieren.
2. **NVIDIA-Treiber** fuer Windows (aktueller Game-Ready- oder Studio-Treiber). Kein CUDA in WSL installieren,
   Docker Desktop reicht die GPU durch. Test: `docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi`
3. **Arbeitsspeicher fuer WSL2 freigeben.** Standardmaessig bekommt WSL2 nur die Haelfte des RAM (bei 16 GB also 8 GB),
   das reicht fuer ODM nicht. Datei `%UserProfile%\.wslconfig` anlegen:

   ```ini
   [wsl2]
   memory=14GB
   swap=32GB
   processors=8
   ```

   `processors` auf die Zahl der logischen Kerne minus 2 setzen. Danach in PowerShell `wsl --shutdown` und Docker Desktop neu starten.
   Der Swap liegt auf `C:` (Datei in `%Temp%`), dort mindestens 40 GB frei halten.
4. **Speicherort:** Das Repo in WSL klonen (z. B. `~/AeroNexus` in der Ubuntu-Shell), nicht unter `C:\` oder `/mnt/c`.
   Die Docker-Volumes liegen dann im schnellen WSL-Dateisystem.

Auf Linux statt Windows: Docker Engine + NVIDIA Container Toolkit, Schritte 3 und 4 entfallen.

## Einrichten

```bash
cd AeroNexus/compute-agent
cp .env.example .env
nano .env        # PI_URL, MAPPING_AGENT_TOKEN (aus edge/.env auf dem Pi), AGENT_ID, NODEODM_TOKEN (zufaellig)
```

Mit NVIDIA-GPU (erster Build dauert, das ODM-GPU-Image ist mehrere GB gross):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Ohne GPU:

```bash
docker compose up -d --build
```

Logs: `docker compose logs -f agent`. Der Agent meldet sich nur beim Pi, wenn NodeODM erreichbar ist.
Laufende Auftraege und Fortschritt stehen in der Web-UI unter Mapping.

## Grenzen bei 16 GB RAM

- `MAX_IMAGES=200` (Standard): groessere Auftraege lehnt der Agent mit klarer Meldung ab, statt waehrend der
  Rechnung abzustuerzen. ODM-Richtwerte (RAM oder RAM+Swap): 250 Bilder ~ 16 GB, 500 ~ 32 GB, 1500 ~ 64 GB.
- Profil "Fast" (fast-orthophoto) braucht deutlich weniger Speicher als "Standard"/"High".
- Bei Speichermangel bricht ODM ab; der Auftrag wird mit der ODM-Fehlermeldung als FAILED gemeldet.

## Farming Guide (Multispektral)

Auftraege mit Profil "Multispectral" (Mavic-3M-Baender) liefern zusaetzlich NDVI, NDRE und GNDVI als
Float32-COG und eingefaerbte Layer mit Statistik (relative Werte, siehe `farming-guide/`). Die Definitionen
kommen aus `farming-guide/indices.json`; deshalb baut das Agent-Image aus dem Repo-Wurzelverzeichnis
(`compute-agent/agent/Dockerfile.dockerignore` laesst nur die benoetigten Pfade durch).
Hauptlayer eines Multispektral-Auftrags ist ein Falschfarbenbild NIR-Rot-Gruen.

## GPU pruefen

Nach einem Auftrag steht im Agent-Log `gpu_sift=True`, wenn ODM die Merkmalsextraktion auf der GPU gerechnet hat.
`False` trotz GPU-Variante heisst: ODM ist auf die CPU ausgewichen (Treiber, VRAM, `feature-quality: ultra`).

## Test ohne echte Photogrammetrie

`tools/fake_nodeodm.py` simuliert NodeODM und liefert ein georeferenziertes Testbild als Orthofoto
(mit `--multispectral` ein synthetisches 5-Band-Orthofoto wie ODM es fuer die Mavic 3M schreibt). Damit
laesst sich der ganze Ablauf (Auftrag, Download, GDAL, Upload, Layer) auch auf dem Pi pruefen:

```bash
docker run -d --rm --name fake-nodeodm --network host -v "$PWD/tools:/tools:ro" -e HOME_POINT=<lon>,<lat> \
  aeronexus/compute-agent:local python3 -W ignore /tools/fake_nodeodm.py --port 3001 --token testtoken
docker run --rm --network host -e PI_URL=http://<pi>:6790 -e MAPPING_AGENT_TOKEN=... \
  -e NODEODM_URL=http://127.0.0.1:3001 -e NODEODM_TOKEN=testtoken aeronexus/compute-agent:local
```

Achtung: Der Test-Agent rechnet den aeltesten wartenden Auftrag. Echte Auftraege vorher nicht in der Warteschlange haben.

## Noch nicht enthalten (Phase 2)

Wake-on-LAN durch den Pi, automatisches Herunterfahren nach Leerlauf, Wiederaufnahme eines NodeODM-Tasks
nach Neustart des Agenten, Ressourcenpruefung schon beim Pi.
