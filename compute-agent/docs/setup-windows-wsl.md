# Compute-Agent auf Windows mit WSL2 einrichten

Schritt-für-Schritt-Anleitung für den x64-Rechenknoten des AeroNexus Mapping Tools
(Windows 10/11, NVIDIA-GPU, 16 GB RAM). Am Ende holt sich der Rechner Mapping-Aufträge vom Pi,
rechnet sie mit ODM und lädt Orthofotos, Höhenmodelle und Kartenkacheln zurück.

Zeitbedarf: etwa 1 bis 2 Stunden, davon 30 bis 60 Minuten für Downloads und den ersten Build.

> Überblick: Windows → WSL2 (Ubuntu 24.04) → Docker Desktop → zwei Container:
> `nodeodm` (rechnet, nur lokal erreichbar) und `agent` (spricht mit dem Pi).
> Der Rechner baut nur **ausgehende** Verbindungen zum Pi auf (Port 6790 und 9000),
> eingehend muss nichts freigegeben werden.

---

## 0. Voraussetzungen prüfen

| Punkt | Soll | Prüfen |
|---|---|---|
| Windows | Windows 11 oder Windows 10 22H2 (Build 19045+) | `Win + R` → `winver` |
| Virtualisierung | im BIOS/UEFI aktiv (Intel VT-x / AMD-V, SVM) | Task-Manager → Leistung → CPU → "Virtualisierung: Aktiviert" |
| Arbeitsspeicher | 16 GB (mehr ist besser) | Task-Manager → Leistung → Arbeitsspeicher |
| Grafikkarte | NVIDIA GeForce/RTX/Quadro ab GTX 9xx, 8 GB VRAM empfohlen | Geräte-Manager → Grafikkarten |
| Freier Speicher auf `C:` | mindestens 150 GB (Images ~25 GB, Swap 32 GB, Arbeitsdaten je Auftrag 10- bis 20-fache Bildmenge) | Explorer |
| Netzwerk | Rechner im selben LAN wie der Pi (192.168.178.x) | `ping 192.168.178.63` in PowerShell |

Falls "Virtualisierung: Deaktiviert": im BIOS/UEFI die Option **Intel Virtualization Technology** bzw.
**SVM Mode** einschalten.

---

## 1. NVIDIA-Treiber unter Windows

1. Aktuellen Treiber von https://www.nvidia.com/Download/index.aspx installieren
   (Game Ready oder Studio, beides geht). Neustart.
2. In PowerShell prüfen:

   ```powershell
   nvidia-smi
   ```

   Es muss die Karte mit Treiberversion und "CUDA Version" erscheinen.

**Wichtig:** In WSL/Ubuntu später **keinen** NVIDIA- oder CUDA-Treiber installieren.
Der Windows-Treiber stellt die GPU automatisch in WSL bereit.

---

## 2. WSL2 mit Ubuntu 24.04 installieren

PowerShell **als Administrator** öffnen:

```powershell
wsl --install -d Ubuntu-24.04
```

Neustart, wenn verlangt. Danach öffnet sich Ubuntu und fragt nach einem **Linux-Benutzernamen und Passwort**
(frei wählbar, z. B. `aeronexus`).

Prüfen (normale PowerShell):

```powershell
wsl --update
wsl -l -v
```

Erwartet: `Ubuntu-24.04` mit `VERSION 2`. Steht dort 1: `wsl --set-version Ubuntu-24.04 2`.

---

## 3. Arbeitsspeicher und Swap für WSL2 festlegen

**Dieser Schritt ist entscheidend.** WSL2 bekommt sonst nur die Hälfte des RAM (8 GB), und ODM bricht
bei größeren Aufträgen ohne klare Meldung ab.

1. Im Explorer `%UserProfile%` eingeben (z. B. `C:\Users\Roman`).
2. Dort die Datei `.wslconfig` anlegen (Editor → "Speichern unter" → Dateityp "Alle Dateien",
   Name genau `.wslconfig`, ohne `.txt`):

   ```ini
   [wsl2]
   # Windows behält 2 GB, der Rest geht an WSL/Docker
   memory=14GB
   # Auslagerung für große ODM-Aufträge (liegt als Datei auf C:)
   swap=32GB
   # logische Kerne minus 2 (Task-Manager → Leistung → CPU → "Logische Prozessoren")
   processors=8
   ```

   Optional, wenn eine zweite, schnelle SSD vorhanden ist: `swapFile=D:\\wsl\\swap.vhdx`

3. WSL neu starten:

   ```powershell
   wsl --shutdown
   ```

4. Ubuntu wieder öffnen und prüfen:

   ```bash
   free -h      # "Mem: total" ~13-14 GiB, "Swap: total" ~32 GiB
   nproc        # Anzahl wie in processors=
   nvidia-smi   # GPU auch in WSL sichtbar
   ```

---

## 4. Docker Desktop installieren

1. Docker Desktop für Windows installieren: https://www.docker.com/products/docker-desktop/
   Beim Setup **"Use WSL 2 instead of Hyper-V"** angehakt lassen. Abmelden/Neustart, wenn verlangt.
2. Docker Desktop öffnen → **Settings**:
   - **General:** "Start Docker Desktop when you sign in to your computer" einschalten,
     "Use the WSL 2 based engine" eingeschaltet.
   - **Resources → WSL integration:** "Enable integration with my default WSL distro" und den Schalter
     bei **Ubuntu-24.04** einschalten. *Apply & restart*.
   - Unter **Resources** gibt es mit WSL2 keine RAM-Regler; das regelt die `.wslconfig` aus Schritt 3.
3. In **Ubuntu** prüfen:

   ```bash
   docker version
   docker run --rm hello-world
   docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu24.04 nvidia-smi
   ```

   Der letzte Befehl muss die Grafikkarte anzeigen. Wenn nicht: Abschnitt "Fehlersuche".

---

## 5. Verbindung zum Pi testen

In Ubuntu:

```bash
curl -s http://192.168.178.63:6790/health
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.178.63:9000/minio/health/live
```

Erwartet: `{"status":"ok",...}` und `200`. Wenn nicht: Pi läuft? Rechner im selben Netz?
Ein VPN auf dem Windows-Rechner kann das LAN blockieren.

---

## 6. Projekt holen

Das Projekt **in Ubuntu** ablegen, nicht unter `C:\` bzw. `/mnt/c` (dort ist Docker deutlich langsamer):

```bash
sudo apt update && sudo apt install -y git openssl
cd ~
git clone https://github.com/mason82-dotcom/AeroNexus.git
cd AeroNexus/compute-agent
```

---

## 7. Konfiguration (`.env`)

```bash
cp .env.example .env
nano .env
```

| Eintrag | Wert |
|---|---|
| `PI_URL` | `http://192.168.178.63:6790` |
| `MAPPING_AGENT_TOKEN` | **derselbe** Wert wie in `edge/.env` auf dem Pi (siehe unten) |
| `AGENT_ID` | Name des Rechners in der Web-UI, z. B. `x64-werkstatt` (nur `A-Z a-z 0-9 . _ -`) |
| `NODEODM_TOKEN` | Zufallswert, nur intern: Ausgabe von `openssl rand -hex 24` |
| `MAX_IMAGES` | `200` bei 16 GB RAM |
| `TARGET_CRS` | `EPSG:25832` (Standard für Baden-Württemberg/Westdeutschland) |

Den Agent-Token auf dem **Pi** auslesen (SSH oder Terminal am Pi):

```bash
grep '^MAPPING_AGENT_TOKEN=' ~/Projekte/AeroNexus/edge/.env
```

Speichern in nano: `Strg+O`, `Enter`, `Strg+X`. Die `.env` ist in Git ignoriert und bleibt lokal.

---

## 8. Bauen und starten (mit GPU)

```bash
cd ~/AeroNexus/compute-agent
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Der erste Build lädt das ODM-GPU-Image (mehrere GB) und baut NodeODM; je nach Leitung 20 bis 60 Minuten.
Ohne NVIDIA-GPU stattdessen: `docker compose up -d --build`.

Prüfen:

```bash
docker compose ps                         # nodeodm und agent: "running"
docker compose logs --tail 20 agent       # "agent x64-werkstatt polling http://192.168.178.63:6790 every 20s"
docker compose exec nodeodm nvidia-smi    # GPU im NodeODM-Container sichtbar
curl -s "http://127.0.0.1:3000/info?token=$(grep ^NODEODM_TOKEN .env | cut -d= -f2)"   # NodeODM-Version 3.6.2
```

Damit die Befehle nicht bei jedem Aufruf die GPU-Datei brauchen, kann man sie einmal setzen:

```bash
echo 'export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml' >> ~/.bashrc && source ~/.bashrc
```

Danach reichen `docker compose up -d`, `docker compose logs -f agent` usw.

---

## 9. Erster Auftrag

In der Warteschlange auf dem Pi steht bereits der Auftrag **"Test"** (10 Bilder). Der Agent holt ihn
sofort ab. Diese Bilder sind Bodenaufnahmen ohne Überlappung; ODM wird sehr wahrscheinlich mit
"not enough images / matches" abbrechen. Das ist ein guter Test für den Fehlerfall: der Auftrag
erscheint in der Web-UI als **FAILED** mit der ODM-Meldung.

Für ein echtes Ergebnis: eine Flächenroute fliegen (z. B. "VogelparkAltlussheim"), Fotos werden
automatisch hochgeladen, dann in der Web-UI **Mapping → New job**, Bilder "RGB only", Profil "Fast"
oder "Standard".

Fortschritt verfolgen:

- Web-UI → **Mapping**: Status, Prozent, Meldung des Agenten
- Rechner: `docker compose logs -f agent`
- Nach Abschluss in der Agent-Log-Zeile `done, layer ..., gpu_sift=True` → GPU wurde genutzt.

---

## 10. Dauerbetrieb

- **Autostart:** Docker Desktop startet bei der Anmeldung (Schritt 4), die Container haben
  `restart: unless-stopped`. Docker Desktop läuft erst **nach der Windows-Anmeldung**; für einen Start
  ohne Anmeldung die automatische Anmeldung von Windows einrichten oder den Rechner angemeldet lassen.
- **Energiesparen:** Windows → Einstellungen → System → Netzbetrieb und Akku → **Ruhezustand/Standby bei
  Netzbetrieb: Nie**. Schläft der Rechner während eines Auftrags, läuft die Lease ab und der Pi stellt den
  Auftrag wieder in die Warteschlange (nach 3 Versuchen: FAILED).
- **Windows-Updates:** Nutzungszeit (Einstellungen → Windows Update → Erweiterte Optionen) so legen, dass
  nachts gerechnet werden kann.
- **Ausschalten ist erlaubt:** Ein laufender Auftrag geht beim nächsten Start einfach erneut in Arbeit.

---

## 11. Aktualisieren

```bash
cd ~/AeroNexus
git pull
cd compute-agent
docker compose up -d --build        # mit COMPOSE_FILE aus Schritt 8 inkl. GPU
```

---

## 12. Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `nvidia-smi` in Ubuntu: "command not found" oder keine GPU | Windows-Treiber zu alt → Schritt 1; `wsl --update`; Neustart |
| `docker run --gpus all ...`: "could not select device driver" | Docker Desktop neu starten; WSL integration für Ubuntu-24.04 an (Schritt 4) |
| Agent-Log: "NodeODM or Pi not reachable" | Pi aus/anderes Netz; `curl` aus Schritt 5; `PI_URL` prüfen |
| Agent-Log: `HTTP 401` beim Claim | `MAPPING_AGENT_TOKEN` stimmt nicht mit `edge/.env` auf dem Pi überein |
| Auftrag FAILED: "exceed MAX_IMAGES" | Auftrag hat mehr als 200 Bilder → aufteilen oder mehr RAM |
| Auftrag FAILED mit "Killed" / Exit-Code 137 | zu wenig Speicher: `.wslconfig` (Schritt 3) prüfen, Profil "Fast" wählen, weniger Bilder |
| `gpu_sift=False` trotz GPU-Variante | Docker ohne GPU gestartet (GPU-Compose-Datei vergessen) oder VRAM zu klein bei "High" |
| Auftrag springt zurück auf QUEUED | Rechner hat geschlafen oder war offline → Energiesparen (Schritt 10) |
| `C:` läuft voll | `docker system df`; alte Images entfernen: `docker image prune`; nach Aufträgen räumt der Agent seine Arbeitsdaten selbst auf |
| Build bricht ab (Download) | erneut `docker compose ... up -d --build`; bereits geladene Teile werden wiederverwendet |

Logs sammeln für eine Fehlermeldung:

```bash
docker compose logs --tail 200 agent nodeodm > ~/aeronexus-agent.log
```

---

## 13. Stoppen und entfernen

```bash
docker compose down              # Container stoppen (Arbeitsdaten im Volume bleiben)
docker compose down -v           # zusätzlich NodeODM- und Agent-Volumes löschen
```

Der Pi merkt das nicht direkt: Ein laufender Auftrag geht nach Ablauf der Lease (10 Minuten) zurück in die
Warteschlange.
