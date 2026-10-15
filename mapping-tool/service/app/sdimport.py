"""Import DJI media and PPK raw data from a read-only SD card (run through edge/sd-import.sh).

    python3 -m app.sdimport --volume <id>                 inventory only: what is new, from which drone
    python3 -m app.sdimport --volume <id> --import        import new media + PPK raw data

Runs in a short-lived container of the mapping image with the card at /sd (read-only, checked) and the
log directory at /work. Media go the same way as a Pilot 2 upload: object to MinIO
(wayline/sdimport-<volume>/<sha256>/<name>, size and SHA-256 verified), then the DJI upload callback,
so ingest, flight sets and the evidence register pick them up. PPK raw data of the flight folders
(.MRK .OBS .NAV .RTK .PBK, every non-media file below DCIM) always comes along under
ppk/sdimport-<volume>/<path below DCIM>; they are not media and get no media_file row.
File types in PILOT_SKIP_UPLOAD_EXT (e.g. dng,mp4) are skipped unless named in --include-ext.
A file already on the server (same name or MD5 fingerprint) is never imported twice.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from . import mediameta

MEDIA_EXT = {".jpg": "image/jpeg", ".dng": "image/x-adobe-dng", ".tif": "image/tiff", ".mp4": "video/mp4"}
IGNORED_EXT = {".mov"}            # neither imported nor treated as PPK
# drone / camera model keys (CLAUDE.md device table, DeviceEnum)
MODEL_KEYS = {"M3T": ("0-77-1", "1-67-0"), "M3E": ("0-77-0", "1-66-0"), "M3M": ("0-77-2", "1-68-0"),
              "M4E": ("0-99-0", "1-88-0"), "M4T": ("0-99-1", "1-89-0")}
# model names DJI writes into the MP4 (moov atom at the start or the end of the file)
VIDEO_MODELS = (("Matrice 4T", "M4T"), ("Matrice 4E", "M4E"), ("Mavic 3T", "M3T"), ("Mavic 3E", "M3E"),
                ("Mavic 3M", "M3M"), ("M4T", "M4T"), ("M4E", "M4E"))
VIDEO_PROBE = 8 * 1024 * 1024
TELEMETRY = {"latitude": "lat", "longitude": "lon", "rel_alt": "rel_alt", "abs_alt": "abs_alt", "gb_yaw": "gimbal_yaw"}
_VOLUME_RE = re.compile(r"^[0-9A-Za-z-]{4,40}$")
SKIP_DIRS = ("System Volume Information", "$RECYCLE.BIN")


# ------------------------------------------------------------------ pure core (tested)

def media_files(dcim: Path):
    for root, dirs, files in os.walk(dcim):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if Path(name).suffix.lower() in MEDIA_EXT:
                yield Path(root) / name


def ppk_files(dcim: Path):
    """PPK raw data and sidecars of the flight folders: every non-media file below DCIM."""
    for root, dirs, files in os.walk(dcim):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            suffix = Path(name).suffix.lower()
            if suffix not in MEDIA_EXT and suffix not in IGNORED_EXT:
                yield Path(root) / name


def media_key(volume: str, sha256: str, name: str) -> str:
    return f"wayline/sdimport-{volume}/{sha256}/{name}"


def ppk_key(volume: str, rel_below_dcim: str) -> str:
    return f"ppk/sdimport-{volume}/{rel_below_dcim}"


def skipped_ext(env_value: str | None, include: str | None) -> set[str]:
    """Extensions to skip: PILOT_SKIP_UPLOAD_EXT minus the ones explicitly included for this run."""
    def parse(value):
        return {"." + e.strip().lstrip(".").lower() for e in (value or "").split(",") if e.strip()}
    return parse(env_value) - parse(include)


def video_meta(head: bytes, tail: bytes, srt: str | None) -> dict:
    """Model and telemetry of a DJI MP4: telemetry from the SRT sidecar, else from the embedded subtitle
    track; model from the MP4 itself. No serial is available, none is invented."""
    text = head.decode("latin-1") + tail.decode("latin-1")
    model = next((m for needle, m in VIDEO_MODELS if needle in text), None)
    if model is None:
        raise ValueError("video model not found in the MP4")
    source = srt if srt is not None else head.decode("latin-1")
    info = {"model": model}
    for key, dest in TELEMETRY.items():
        m = re.search(r"\b" + key + r":\s*([-+]?\d+(?:\.\d+)?)", source)
        if not m:
            raise ValueError("video telemetry lacks " + key)
        info[dest] = float(m.group(1))
    return info


def photo_meta(head: bytes, size: int, name: str, whole=None) -> tuple[dict, str]:
    """(metadata, drone serial) of a DJI photo; whole() returns the full file if the head is not enough."""
    try:
        info = mediameta.extract(head, size, name)
    except Exception:
        if whole is None:
            raise
        info = mediameta.extract(whole(), size, name)
    if info.get("model") not in MODEL_KEYS:
        raise ValueError(f"unknown DJI model {info.get('model')!r}")
    xmp = mediameta.xmp(head)
    serial = mediameta.serial(xmp.get("DroneSerialNumber"))
    if not serial:
        raise ValueError("no DroneSerialNumber in XMP")
    # no GNSS fix: hand the raw XMP values to the callback (validated there, not stored as a position)
    if (info.get("lat") is None or info.get("lon") is None) and xmp.get("GpsStatus") == "Invalid":
        try:
            info["lat"], info["lon"] = float(xmp["GpsLatitude"]), float(xmp["GpsLongitude"])
        except (KeyError, ValueError):
            pass
    xmp_yaw = xmp.get("GimbalYawDegree")
    if info.get("gimbal_yaw") is None and xmp_yaw is not None:
        info["gimbal_yaw"] = float(xmp_yaw)
    missing = [k for k in ("abs_alt", "rel_alt", "gimbal_yaw", "lat", "lon") if info.get(k) is None]
    if missing:
        raise ValueError("required geodata missing: " + ",".join(missing))
    return info, serial


def callback_body(info: dict, serial: str, md5: str, sha256: str, name: str, folder: str, key: str) -> dict:
    drone_key, payload_key = MODEL_KEYS[info["model"]]
    return {
        "ext": {"drone_model_key": drone_key, "file_group_id": "", "is_original": True,
                "payload_model_key": payload_key, "tinny_fingerprint": "sdimport-" + sha256, "sn": serial},
        "fingerprint": md5, "name": name, "path": folder, "object_key": key, "sub_file_type": 0,
        "metadata": {"absolute_altitude": info["abs_alt"], "relative_altitude": info["rel_alt"],
                     "gimbal_yaw_degree": info["gimbal_yaw"],
                     "shoot_position": {"lat": info["lat"], "lng": info["lon"]}},
    }


# ------------------------------------------------------------------ files, MinIO, MySQL, backend

def hashes(path: Path) -> tuple[str, str]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def read_meta(path: Path) -> tuple[dict, str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(VIDEO_PROBE if path.suffix.lower() == ".mp4" else min(size, mediameta.HEAD_BYTES))
        if path.suffix.lower() == ".mp4":
            handle.seek(max(0, size - VIDEO_PROBE))
            tail = handle.read()
    if path.suffix.lower() == ".mp4":
        srt = path.with_suffix(".SRT")
        return video_meta(head, tail, srt.read_text(errors="replace")[:5000] if srt.is_file() else None), ""
    return photo_meta(head, size, path.name, whole=path.read_bytes)


def _readonly(mount: str) -> bool:
    with open("/proc/mounts", encoding="utf-8") as handle:
        rows = [line.split() for line in handle if len(line.split()) > 3 and line.split()[1] == mount]
    return len(rows) == 1 and "ro" in rows[0][3].split(",")


class Server:
    """Backend login + callback, read access to cloud_sample.media_file, writes to the media bucket."""

    def __init__(self):
        import pymysql
        from minio import Minio
        self.base = os.environ.get("API_URL", "http://api:6789")
        self.db = pymysql.connect(host=os.environ.get("MYSQL_HOST", "mysql"), user="mapping",
                                  password=os.environ["MAPPING_DB_PASSWORD"], database="cloud_sample",
                                  cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                                  connect_timeout=10, read_timeout=30)
        self.minio = Minio(os.environ.get("MINIO_ENDPOINT", "minio:9000"), access_key=os.environ["MINIO_STS_USER"],
                           secret_key=os.environ["MINIO_STS_PASSWORD"], secure=False)
        self.bucket = os.environ["MINIO_BUCKET"]
        self.token = self.workspace = None

    def login(self):
        data = self._post("/manage/api/v1/login", {"username": os.environ.get("WEB_ADMIN_USER", "adminPC"),
                                                   "password": os.environ["WEB_ADMIN_PASSWORD"], "flag": 1})
        self.token, self.workspace = data["access_token"], data["workspace_id"]

    def _post(self, path: str, payload: dict):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["x-auth-token"] = self.token
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"API HTTP {exc.code}") from None
        if result.get("code") != 0:
            raise RuntimeError(f"API refused request (code {result.get('code')})")
        return result.get("data")

    def known(self, name: str, md5: str) -> list[dict]:
        with self.db.cursor() as cur:
            cur.execute("SELECT file_id, file_name, fingerprint, object_key, workspace_id FROM media_file"
                        " WHERE file_name = %s OR LOWER(fingerprint) = %s", (name, md5.lower()))
            return cur.fetchall()

    def fingerprints(self) -> set[str]:
        with self.db.cursor() as cur:
            cur.execute("SELECT LOWER(fingerprint) AS f FROM media_file WHERE fingerprint <> ''")
            return {r["f"] for r in cur.fetchall()}

    def put_verified(self, key: str, path: Path, size: int, sha256: str, content_type: str, volume: str) -> str:
        """Upload unless an identical object exists; returns 'stored' or 'present'."""
        from minio.error import S3Error
        try:
            stat = self.minio.stat_object(self.bucket, key)
            if stat.size != size or stat.metadata.get("x-amz-meta-sha256", "").lower() != sha256:
                raise RuntimeError(f"existing object differs: {key}")
            return "present"
        except S3Error as exc:
            if exc.code not in ("NoSuchKey", "NoSuchObject"):
                raise
        self.minio.fput_object(self.bucket, key, str(path), content_type=content_type,
                               metadata={"sha256": sha256, "sd-source-volume": volume})
        stat = self.minio.stat_object(self.bucket, key)
        if stat.size != size or stat.metadata.get("x-amz-meta-sha256", "").lower() != sha256:
            raise RuntimeError(f"size/SHA-256 verification failed: {key}")
        return "stored"

    def register(self, body: dict) -> None:
        returned = self._post(f"/media/api/v1/workspaces/{self.workspace}/upload-callback", body)
        if returned != body["object_key"]:
            raise RuntimeError("unexpected callback response")
        rows = [r for r in self.known(body["name"], body["fingerprint"])
                if r["object_key"] == body["object_key"] and r["workspace_id"] == self.workspace]
        if len(rows) != 1:
            raise RuntimeError("callback registration read-back differs")


class Log:
    def __init__(self, path: Path):
        self.path = path

    def write(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


# ------------------------------------------------------------------ runs

def inventory(dcim: Path, server: Server, skip: set[str]) -> int:
    """Per flight folder: files, drone, height, gimbal, how many are new. Changes nothing."""
    on_server = server.fingerprints()
    groups = defaultdict(list)
    for path in media_files(dcim):
        rel = path.relative_to(dcim)
        md5, _ = hashes(path)
        try:
            info, serial = read_meta(path)
            error = None
        except Exception as exc:
            info, serial, error = {}, "", str(exc)[:80]
        groups[rel.parts[0] if len(rel.parts) > 1 else "."].append(
            (path, info, serial, md5 in on_server, path.stat().st_size, error))
    new_total = 0
    for folder, rows in sorted(groups.items()):
        rel = [r[1]["rel_alt"] for r in rows if r[1].get("rel_alt") is not None]
        pitch = [r[1]["gimbal_pitch"] for r in rows if r[1].get("gimbal_pitch") is not None]
        new = [r for r in rows if not r[3] and r[0].suffix.lower() not in skip and not r[5]]
        new_total += len(new)
        print(f"{folder}")
        print(f"   {dict(Counter(r[0].suffix.upper() for r in rows))}  "
              f"Drohne {dict(Counter(r[2] or '-' for r in rows))}  Modell {dict(Counter(r[1].get('model', '-') for r in rows))}")
        if rel:
            print(f"   rel. Hoehe {min(rel):.0f}..{max(rel):.0f} m (Median {statistics.median(rel):.0f})"
                  + (f", Gimbal Median {statistics.median(pitch):.0f}" if pitch else ""))
        print(f"   schon auf dem Server {sum(r[3] for r in rows)}, uebersprungen (Typ) "
              f"{sum(1 for r in rows if not r[3] and r[0].suffix.lower() in skip)}, nicht lesbar "
              f"{sum(1 for r in rows if r[5])}, neu {len(new)} ({sum(r[4] for r in new) / 1048576:.0f} MB)")
        for r in [r for r in rows if r[5]][:3]:
            print(f"   nicht lesbar: {r[0].name}: {r[5]}")
    ppk = list(ppk_files(dcim))
    print(f"PPK-Rohdaten: {len(ppk)} Dateien ({sum(p.stat().st_size for p in ppk) / 1048576:.0f} MB), "
          f"kommen beim Import immer mit")
    print(f"neue Medien gesamt: {new_total}")
    return 0


def run_import(dcim: Path, server: Server, skip: set[str], volume: str, log: Log, max_errors: int = 3) -> int:
    server.login()
    totals = Counter()
    for path in ppk_files(dcim):                       # PPK raw data always comes along
        rel = path.relative_to(dcim).as_posix()
        size = path.stat().st_size
        _, sha = hashes(path)
        key = ppk_key(volume, rel)
        state = server.put_verified(key, path, size, sha, "application/octet-stream", volume)
        totals["ppk_" + state] += 1
        if state == "stored":
            log.write({"kind": "ppk", "source": rel, "object_key": key, "size": size, "sha256": sha,
                       "status": "verified"})
    for path in media_files(dcim):
        rel = path.relative_to(dcim).as_posix()
        if path.suffix.lower() in skip:
            totals["skipped_type"] += 1
            continue
        try:
            size = path.stat().st_size
            md5, sha = hashes(path)
            current = server.known(path.name, md5)
            if any(r["file_name"] == path.name and (r["fingerprint"] or "").lower() == md5 for r in current):
                totals["already_on_server"] += 1
                continue
            if current:
                raise RuntimeError("same name or fingerprint with other content already on the server")
            info, serial = read_meta(path)
            key = media_key(volume, sha, path.name)
            server.put_verified(key, path, size, sha, MEDIA_EXT[path.suffix.lower()], volume)
            if server.known(path.name, md5):              # a Pilot 2 upload of the same file wins
                raise RuntimeError("registered concurrently; verified object left for a later run")
            server.register(callback_body(info, serial, md5, sha, path.name, str(Path(rel).parent), key))
            log.write({"kind": "media", "source": rel, "object_key": key, "size": size, "sha256": sha,
                       "fingerprint": md5, "model": info["model"], "drone": serial, "status": "verified"})
            totals["imported"] += 1
            if totals["imported"] % 50 == 0:
                print("progress", json.dumps(dict(totals), sort_keys=True), flush=True)
        except Exception as exc:
            totals["failed"] += 1
            log.write({"kind": "media", "source": rel, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:200]})
            print(f"failed {rel}: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            if totals["failed"] >= max_errors:
                print(f"stopping after {max_errors} errors", flush=True)
                break
    print("result", json.dumps(dict(totals), sort_keys=True), flush=True)
    return 2 if totals["failed"] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.sdimport", description=__doc__.splitlines()[0])
    parser.add_argument("--volume", required=True, help="volume id of the card (edge/sd-import.sh reads it)")
    parser.add_argument("--import", dest="do_import", action="store_true", help="import (default: inventory only)")
    parser.add_argument("--include-ext", default="", help="import these types despite PILOT_SKIP_UPLOAD_EXT, e.g. mp4")
    parser.add_argument("--sd", default="/sd")
    parser.add_argument("--log", default="/work/sd-import.jsonl")
    args = parser.parse_args(argv)
    if not _VOLUME_RE.match(args.volume):
        parser.error("invalid volume id")
    dcim = Path(args.sd) / "DCIM"
    if not dcim.is_dir():
        print("no DCIM folder on the card", file=sys.stderr)
        return 1
    if not _readonly(args.sd):
        print(f"{args.sd} must be mounted read-only", file=sys.stderr)
        return 1
    skip = skipped_ext(os.environ.get("PILOT_SKIP_UPLOAD_EXT"), args.include_ext)
    print(f"Karte {args.volume}, uebersprungene Typen: {','.join(sorted(skip)) or '-'}", flush=True)
    server = Server()
    if not args.do_import:
        return inventory(dcim, server, skip)
    return run_import(dcim, server, skip, args.volume, Log(Path(args.log)))


if __name__ == "__main__":
    raise SystemExit(main())
