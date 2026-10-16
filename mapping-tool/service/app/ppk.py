"""PPK: camera positions of a flight from the drone's GNSS raw data and a reference station (edge/ppk.sh).

    python3 -m app.ppk --set <flight set id or prefix> --base /base          report + geo.txt in /work
    python3 -m app.ppk --set <id> --base /base --create-job [--name ...]       also a mapping job using it

Inputs: the PPK raw data of every flight folder of the set, stored by the SD import under
ppk/sdimport-<volume>/<folder>/ (rover .OBS/.NAV RINEX 3, .MRK shutter times + antenna->camera offset), and
reference station observations in /base (SAPOS GPPS / VRS, EPN highrate, own base; RINEX 2/3, Hatanaka .crx,
.gz and the BKG 15-minute .tar.gz archives). RTKLIB (rnx2rtkp, demo5) computes the antenna track; per photo
the position at the MRK time is interpolated and the MRK offset applied (N/E/down, mm). Output: ODM geo.txt
(EPSG:4326, ellipsoidal height) with accuracies per solution quality.

rnx2rtkp only uses the FIRST base observation file it is given: base files are merged into one first.
"""
import argparse
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

GPS_EPOCH = datetime(1980, 1, 6)
WEEK = 604800
# accuracy floor per RTKLIB quality (1 fix, 2 float, 4 dgps, 5 single): formal float sigmas are optimistic
ACCURACY_FLOOR = {1: (0.02, 0.04), 2: (0.10, 0.20), 4: (0.5, 1.0), 5: (2.0, 4.0)}
QUALITY_NAME = {1: "fix", 2: "float", 3: "sbas", 4: "dgps", 5: "single", 6: "ppp"}
MAX_GAP_S = 1.0                   # interpolate only between solutions at most this far apart
RTKLIB_CONF = """\
pos1-posmode       =kinematic
pos1-frequency     =l1+l2+l5
pos1-soltype       =combined
pos1-elmask        =15
pos1-navsys        =45
pos2-armode        =continuous
pos2-gloarmode     =off
pos2-arthres       =3
out-solformat      =llh
out-timesys        =gpst
out-timeform       =tow
out-height         =ellipsoidal
ant2-postype       =rinexhead
"""
_IMAGE_INDEX = re.compile(r"^DJI_\d{14}_(\d{4})_[A-Z]+\.(JPG|TIF|DNG)$", re.I)


@dataclass
class Mark:
    index: int
    t: float            # GPS seconds since GPS epoch
    dn: float           # antenna -> camera offset, metres north / east / down
    de: float
    dd: float


@dataclass
class Fix:
    t: float
    lat: float
    lon: float
    h: float
    q: int
    sdn: float
    sde: float
    sdu: float


# ------------------------------------------------------------------ pure core (tested)

def parse_mrk(text: str) -> list[Mark]:
    """DJI .MRK: index, GPS time of week, [week], N/E/V offsets in mm. Lines with an invalid time are skipped
    (seen 2026-10-16: a trailing '-259200.000000 [-522]' line)."""
    marks = []
    for line in text.splitlines():
        f = line.split("\t")
        m = re.match(r"\[(-?\d+)\]", f[2].strip()) if len(f) > 5 else None
        if not m:
            continue
        try:
            index, tow, week = int(f[0]), float(f[1]), int(m.group(1))
            dn, de, dv = (float(f[i].split(",")[0]) / 1000 for i in (3, 4, 5))
        except ValueError:
            continue
        if week <= 0 or not 0 <= tow < WEEK:
            continue
        marks.append(Mark(index, week * WEEK + tow, dn, de, dv))
    return marks


def parse_pos(text: str) -> list[Fix]:
    """rnx2rtkp output (llh, GPST week + time of week)."""
    out = []
    for line in text.splitlines():
        if line.startswith("%") or not line.strip():
            continue
        f = line.split()
        out.append(Fix(int(f[0]) * WEEK + float(f[1]), float(f[2]), float(f[3]), float(f[4]), int(f[5]),
                       float(f[7]), float(f[8]), float(f[9])))
    return sorted(out, key=lambda s: s.t)


def interpolate(track: list[Fix], t: float) -> Fix | None:
    """Linear between the two solutions around t (at most MAX_GAP_S apart); quality = the worse of both."""
    lo, hi = 0, len(track) - 1
    if not track or t < track[0].t or t > track[-1].t:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid].t <= t:
            lo = mid
        else:
            hi = mid
    a, b = track[lo], track[hi]
    if b.t - a.t > MAX_GAP_S:
        return None
    w = 0.0 if b.t == a.t else (t - a.t) / (b.t - a.t)
    lerp = lambda x, y: x + (y - x) * w                                  # noqa: E731
    return Fix(t, lerp(a.lat, b.lat), lerp(a.lon, b.lon), lerp(a.h, b.h), max(a.q, b.q),
               max(a.sdn, b.sdn), max(a.sde, b.sde), max(a.sdu, b.sdu))


def camera_position(antenna: Fix, mark: Mark) -> tuple[float, float, float]:
    """Antenna phase centre + MRK offset (north, east, down in metres) -> camera (lat, lon, ellipsoidal h)."""
    a, e2 = 6378137.0, 6.69437999014e-3
    lat = math.radians(antenna.lat)
    rn = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    rm = rn * (1 - e2) / (1 - e2 * math.sin(lat) ** 2)
    return (antenna.lat + math.degrees(mark.dn / (rm + antenna.h)),
            antenna.lon + math.degrees(mark.de / ((rn + antenna.h) * math.cos(lat))),
            antenna.h - mark.dd)


def accuracy(fix: Fix) -> tuple[float, float]:
    floor_h, floor_v = ACCURACY_FLOOR.get(fix.q, (5.0, 10.0))
    return max(math.hypot(fix.sdn, fix.sde), floor_h), max(fix.sdu, floor_v)


def image_index(name: str) -> int | None:
    m = _IMAGE_INDEX.match(name)
    return int(m.group(1)) if m else None


def build_geo(images: dict[str, list[str]], marks: dict[str, list[Mark]], track: list[Fix]) -> tuple[str, dict]:
    """images/marks keyed by flight folder. Returns (geo.txt, report). Images without a mark or a solution at
    their time are left out of geo.txt (ODM then uses their EXIF position)."""
    lines, quality, missing = ["EPSG:4326"], {}, []
    for folder, names in sorted(images.items()):
        by_index = {m.index: m for m in marks.get(folder, [])}
        for name in sorted(names):
            mark = by_index.get(image_index(name))
            fix = interpolate(track, mark.t) if mark else None
            if fix is None:
                missing.append(name)
                continue
            lat, lon, h = camera_position(fix, mark)
            hacc, vacc = accuracy(fix)
            lines.append(f"{name} {lon:.9f} {lat:.9f} {h:.4f} 0 0 0 {hacc:.3f} {vacc:.3f}")
            q = QUALITY_NAME.get(fix.q, str(fix.q))
            quality[q] = quality.get(q, 0) + 1
    total = sum(len(v) for v in images.values())
    return "\n".join(lines) + "\n", {"images": total, "positioned": total - len(missing), "quality": quality,
                                      "missing": missing[:20], "missing_count": len(missing)}


def merge_rinex_obs(texts: list[str]) -> str:
    """One RINEX observation file from several of one station (header of the first, epochs of all in time
    order, an epoch present in two files kept once): rnx2rtkp reads only the first base file."""
    head, sep, rest = texts[0].partition("END OF HEADER")
    header = head + sep + rest.split("\n", 1)[0] + "\n"
    epochs: dict[float, list[str]] = {}
    for text in texts:
        _, sep, rest = text.partition("END OF HEADER")
        body = rest.split("\n", 1)[1] if sep and "\n" in rest else ""
        lines = None
        for line in body.splitlines(keepends=True):
            if line.startswith(">"):
                t = _epoch_seconds(line)
                lines = None if t is None or t in epochs else epochs.setdefault(t, [line])
            elif lines is not None:
                lines.append(line)
    return header + "".join("".join(epochs[t]) for t in sorted(epochs))


def obs_window(text: str) -> tuple[float, float] | None:
    """(first, last) epoch of a RINEX 3 observation file as GPS seconds."""
    times = [_epoch_seconds(line) for line in text.splitlines() if line.startswith(">")]
    times = [t for t in times if t is not None]
    return (min(times), max(times)) if times else None


def _epoch_seconds(line: str) -> float | None:
    f = line[1:].split()
    try:
        y, mo, d, h, mi, s = int(f[0]), int(f[1]), int(f[2]), int(f[3]), int(f[4]), float(f[5])
    except (ValueError, IndexError):
        return None
    return (datetime(y, mo, d, h, mi) - GPS_EPOCH).total_seconds() + s


# ------------------------------------------------------------------ files and tools

def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(result.stderr or result.stdout)[-300:]}")


_LONG_NAME = re.compile(r"_(\d{4})(\d{3})(\d{2})(\d{2})_(\d{2})([MHD])_")


def name_span(name: str) -> tuple[float, float] | None:
    """(start, end) in GPS seconds from a RINEX 3 long file name (..._YYYYDDDHHMM_15M_...), else None."""
    m = _LONG_NAME.search(name)
    if not m:
        return None
    y, doy, hh, mm, n, unit = int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), m[6]
    start = (datetime(y, 1, 1) + timedelta(days=doy - 1, hours=hh, minutes=mm) - GPS_EPOCH).total_seconds()
    return start, start + n * {"M": 60, "H": 3600, "D": 86400}[unit]


def wanted(name: str, windows: list[tuple[float, float]], margin: float = 300) -> bool:
    """Base files whose name says they cover none of the flight windows are not unpacked (BKG day archives)."""
    span = name_span(name)
    return span is None or any(span[0] <= w[1] + margin and span[1] >= w[0] - margin for w in windows)


def unpack_base(src: Path, dst: Path, windows: list[tuple[float, float]]) -> tuple[list[Path], list[Path]]:
    """Copy/unpack the reference station files covering the windows into dst; (observation, navigation) RINEX."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.rglob("*")):
        if f.is_dir():
            continue
        name = f.name
        if name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(f) as tar:
                for member in tar.getmembers():
                    if (member.isfile() and "/" not in member.name.strip("/") and not member.name.startswith(".")
                            and wanted(member.name, windows)):
                        tar.extract(member, dst, filter="data")
        elif not wanted(name, windows):
            continue
        elif name.endswith(".gz"):
            with gzip.open(f) as fin, (dst / name[:-3]).open("wb") as fout:
                shutil.copyfileobj(fin, fout)
        else:
            shutil.copy(f, dst / name)
    for f in sorted(dst.iterdir()):
        if f.suffix.lower() == ".crx" or re.search(r"\.\d\dd$", f.name, re.I):
            _run(["crx2rnx", "-f", f.name], dst)
            f.unlink()
    obs, nav = [], []
    for f in sorted(dst.iterdir()):
        head = f.read_text(errors="replace")[:2000]
        if "OBSERVATION DATA" in head:
            obs.append(f)
        elif "NAV" in head[:200] and "RINEX VERSION" in head:
            nav.append(f)
    return obs, nav


def solve(rover_obs: Path, rover_nav: Path, base_obs: list[Path], base_nav: list[Path], work: Path) -> list[Fix]:
    """RTKLIB kinematic solution of one rover file against the base files overlapping its time window."""
    window = obs_window(rover_obs.read_text(errors="replace"))
    if window is None:
        raise ValueError(f"no epochs in {rover_obs.name}")
    texts = []
    for f in base_obs:
        text = f.read_text(errors="replace")
        w = obs_window(text)
        if w and w[1] >= window[0] - 60 and w[0] <= window[1] + 60:
            texts.append(text)
    if not texts:
        raise ValueError("no reference station data covers the flight time")
    merged = work / f"base_{rover_obs.stem}.obs"
    merged.write_text(merge_rinex_obs(sorted(texts, key=lambda t: obs_window(t)[0])))
    conf = work / "ppk.conf"
    conf.write_text(RTKLIB_CONF)
    out = work / f"{rover_obs.stem}.pos"
    _run(["rnx2rtkp", "-k", conf.name, "-o", out.name, str(rover_obs), merged.name, str(rover_nav),
          *[str(n) for n in base_nav]], work)
    return parse_pos(out.read_text())


# ------------------------------------------------------------------ database, MinIO, API

def _flight_set(settings, prefix: str) -> tuple[str, dict[str, list[tuple[str, str]]]]:
    """(set id, {flight folder: [(file name, object key)]}) of the set's photos."""
    from . import db
    with db.transaction(settings) as cur:
        cur.execute("SELECT id FROM flight_set WHERE id LIKE %s", (prefix + "%",))
        ids = [r["id"] for r in cur.fetchall()]
        if len(ids) != 1:
            raise SystemExit(f"flight set {prefix!r}: {len(ids)} matches")
        cur.execute("SELECT file_id FROM media_ingest WHERE flight_set_id = %s AND category <> 'video'", (ids[0],))
        file_ids = [r["file_id"] for r in cur.fetchall()]
    conn = db.connect(settings, settings.media_database)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_name, file_path, object_key FROM media_file WHERE file_id IN (%s)"
                        % ",".join(["%s"] * len(file_ids)), file_ids)
            rows = cur.fetchall()
    finally:
        conn.close()
    folders: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        m = re.match(r"^wayline/sdimport-([0-9A-Za-z-]+)/", r["object_key"])
        if not m:
            raise SystemExit(f"{r['file_name']} was not imported from an SD card: no PPK raw data")
        folder = re.sub(r"^DCIM(/|$)", "", r["file_path"] or "")
        folders.setdefault(f"ppk/sdimport-{m.group(1)}/{folder}", []).append((r["file_name"], r["object_key"]))
    return ids[0], folders


def _download_ppk(storage, settings, prefix: str, dst: Path) -> dict[str, Path] | None:
    """.OBS / .NAV / .MRK of one flight folder (not of subfolders); None if the folder has no PPK data at all."""
    dst.mkdir(parents=True, exist_ok=True)
    found = {}
    base = prefix.rstrip("/") + "/"                                  # card root: folder "" -> no double slash
    for obj in storage.internal.list_objects(settings.media_bucket, prefix=base):
        name = obj.object_name.rsplit("/", 1)[-1]
        ext = Path(name).suffix.upper()
        if ext in (".OBS", ".NAV", ".MRK") and "/" not in obj.object_name[len(base):]:
            if ext in found:
                raise RuntimeError(f"more than one {ext} in {prefix}")
            storage.internal.fget_object(settings.media_bucket, obj.object_name, str(dst / name))
            found[ext] = dst / name
    missing = {".OBS", ".NAV", ".MRK"} - set(found)
    if missing == {".OBS", ".NAV", ".MRK"}:
        return None
    if missing:
        raise RuntimeError(f"PPK files missing in {prefix}: {', '.join(sorted(missing))}")
    return found


def _create_job(name: str, keys: list[str], geo: str) -> dict:
    def post(url, body, token=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", **({"x-auth-token": token} if token else {})})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    login = post(os.environ.get("API_URL", "http://api:6789") + "/manage/api/v1/login",
                 {"username": os.environ.get("WEB_ADMIN_USER", "adminPC"),
                  "password": os.environ["WEB_ADMIN_PASSWORD"], "flag": 1})
    return post(os.environ.get("MAPPING_URL", "http://mapping:6790") + "/api/mapping/jobs",
                {"name": name, "image_keys": keys, "profile": "standard", "geo": geo}, login["data"]["access_token"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.ppk", description=__doc__.splitlines()[0])
    parser.add_argument("--set", required=True, help="flight set id (or unique prefix)")
    parser.add_argument("--base", default="/base", help="directory with the reference station files")
    parser.add_argument("--work", default="/work")
    parser.add_argument("--create-job", action="store_true", help="create a mapping job using the PPK positions")
    parser.add_argument("--name", default="", help="job name")
    args = parser.parse_args(argv)
    from . import config
    from .storage import Storage
    settings = config.load()
    storage = Storage(settings)
    set_id, folders = _flight_set(settings, args.set)
    work = Path(args.work) / f"ppk-{set_id[:8]}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    rovers = {prefix: _download_ppk(storage, settings, prefix, work / f"rover{i}")
              for i, prefix in enumerate(sorted(folders))}
    for prefix in [p for p, r in rovers.items() if r is None]:
        print(f"   {prefix.split('/', 2)[-1] or '(Kartenwurzel)'}: keine PPK-Rohdaten, "
              f"{len(folders[prefix])} Bilder behalten ihre EXIF-Position", flush=True)
    windows = [w for r in rovers.values() if r and (w := obs_window(r[".OBS"].read_text(errors="replace")))]
    base_obs, base_nav = unpack_base(Path(args.base), work / "base", windows)
    print(f"Flugsatz {set_id[:8]}: {sum(len(v) for v in folders.values())} Bilder in {len(folders)} Ordnern, "
          f"Basis: {len(base_obs)} Beobachtungs-, {len(base_nav)} Navigationsdateien", flush=True)
    track, marks, images = [], {}, {}
    for i, (prefix, files) in enumerate(sorted(folders.items())):
        ppk = rovers[prefix]
        images[prefix] = [n for n, _ in files]
        if ppk is None:
            continue
        marks[prefix] = parse_mrk(ppk[".MRK"].read_text(errors="replace"))
        part = solve(ppk[".OBS"], ppk[".NAV"], base_obs, base_nav, work / f"rover{i}")
        print(f"   {prefix.split('/', 2)[-1]}: {len(marks[prefix])} Ausloesungen, {len(part)} Loesungen "
              f"({', '.join(f'{QUALITY_NAME.get(q, q)} {sum(1 for p in part if p.q == q)}' for q in sorted({p.q for p in part}))})",
              flush=True)
        track += part
    track.sort(key=lambda s: s.t)
    geo, report = build_geo(images, marks, track)
    (work / "geo.txt").write_text(geo)
    (work / "report.json").write_text(json.dumps(report, indent=1))
    print("Ergebnis:", json.dumps({k: v for k, v in report.items() if k != "missing"}), flush=True)
    print(f"geo.txt: {work / 'geo.txt'}", flush=True)
    if args.create_job:
        keys = sorted(k for files in folders.values() for _, k in files)
        job = _create_job(args.name or f"Orthomosaik PPK {set_id[:8]}", keys, geo)
        print(f"Kartierungsauftrag {job['id'][:8]} angelegt ({job['image_count']} Bilder, mit PPK-Positionen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
