"""Offline orthophotos (LGL Baden-Wuerttemberg DOP20, open data) for an area -> MBTiles (resumable).

Source: WMTS WMTS_LGL-BW_ATKIS_DOP_20_C, tile matrix set GoogleMapsCompatible (web mercator XYZ), JPEG.
License: Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0),
         attribution "LGL-BW (<year of download>) dl-de/by-2-0".
Area: a circle around a point (--center lon,lat --radius-km) or a GeoJSON polygon (--region file).
Run by fetch-orthophoto.sh (pinned GDAL image, standard library only); fetch-orthophoto.sh converts the
MBTiles into PMTiles for the web map. Interrupted downloads continue where they stopped.
"""
import argparse
import json
import math
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WMTS = ("https://owsproxy.lgl-bw.de/owsproxy/ows/WMTS_LGL-BW_ATKIS_DOP_20_C?SERVICE=WMTS&REQUEST=GetTile"
        "&VERSION=1.0.0&LAYER=DOP_20_C&STYLE=default&TILEMATRIXSET=GoogleMapsCompatible"
        "&TILEMATRIX=GoogleMapsCompatible:{z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg")
USER_AGENT = "AeroNexus-offline-basemap/1.0 (self-hosted drone platform; DOP20 open data)"
R_EARTH = 6378137.0


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees."""
    n = 2 ** z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def circle(lon: float, lat: float, radius_km: float, segments: int = 72) -> list[list[float]]:
    k = math.cos(math.radians(lat))
    return [[lon + radius_km * 1000 * math.sin(a) / (111320 * k), lat + radius_km * 1000 * math.cos(a) / 111320]
            for a in (2 * math.pi * i / segments for i in range(segments))]


def _inside(px: float, py: float, ring: list[list[float]]) -> bool:
    hit = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i][:2], ring[(i + 1) % len(ring)][:2]
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def tile_in_area(x: int, y: int, z: int, rings: list[list[list[float]]]) -> bool:
    """Tile touches the area: a tile corner/centre inside a ring or a ring vertex inside the tile."""
    w, s, e, n = tile_bounds(x, y, z)
    probes = [(w, s), (w, n), (e, s), (e, n), ((w + e) / 2, (s + n) / 2)]
    for ring in rings:
        if any(_inside(px, py, ring) for px, py in probes):
            return True
        if any(w <= p[0] <= e and s <= p[1] <= n for p in ring):
            return True
    return False


def tiles(rings: list[list[list[float]]], minzoom: int, maxzoom: int):
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    for z in range(minzoom, maxzoom + 1):
        x0, y0 = lonlat_to_tile(min(lons), max(lats), z)
        x1, y1 = lonlat_to_tile(max(lons), min(lats), z)
        for x in range(int(x0), int(x1) + 1):
            for y in range(int(y0), int(y1) + 1):
                if tile_in_area(x, y, z, rings):
                    yield z, x, y


def read_rings(path: str) -> list[list[list[float]]]:
    geo = json.load(open(path, encoding="utf-8"))
    feats = geo["features"] if geo.get("type") == "FeatureCollection" else [geo]
    rings = []
    for f in feats:
        g = f.get("geometry", f)
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        rings += [p[0] for p in polys]
    return rings


def open_mbtiles(path: str, name: str, rings, minzoom: int, maxzoom: int) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,"
               " tile_data BLOB, PRIMARY KEY (zoom_level, tile_column, tile_row))")
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    meta = {"name": name, "format": "jpg", "type": "baselayer", "minzoom": str(minzoom), "maxzoom": str(maxzoom),
            "bounds": f"{min(lons):.6f},{min(lats):.6f},{max(lons):.6f},{max(lats):.6f}",
            "attribution": f"LGL-BW ({time.strftime('%Y')}) dl-de/by-2-0",
            "description": "Digitale Orthophotos DOP20, LGL Baden-Wuerttemberg, Datenlizenz Deutschland - "
                           "Namensnennung - Version 2.0 (https://www.govdata.de/dl-de/by-2-0)"}
    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", meta.items())
    db.commit()
    return db


class TileError(RuntimeError):
    pass


def fetch(url: str, attempts: int = 4) -> bytes | None:
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if data[:2] == b"\xff\xd8":
                return data
            return None                       # outside the data area the service answers without an image
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(2 ** i)
    raise TileError(url)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="MBTiles file (resumed if it exists)")
    ap.add_argument("--name", default="Luftbild")
    ap.add_argument("--center", help="lon,lat")
    ap.add_argument("--radius-km", type=float, default=5.0)
    ap.add_argument("--region", help="GeoJSON Polygon/MultiPolygon instead of a circle")
    ap.add_argument("--minzoom", type=int, default=10)
    ap.add_argument("--maxzoom", type=int, default=19)    # 19 = ~0.3 m/px in BW; 20 quadruples the tiles
    ap.add_argument("--threads", type=int, default=4)     # be gentle with the public service
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.region:
        rings = read_rings(a.region)
    elif a.center:
        lon, lat = map(float, a.center.split(","))
        rings = [circle(lon, lat, a.radius_km)]
    else:
        ap.error("--center or --region required")
    if not 0 <= a.minzoom <= a.maxzoom <= 20:
        ap.error("zoom 0..20")
    todo = list(tiles(rings, a.minzoom, a.maxzoom))
    print(f"{len(todo)} tiles z{a.minzoom}-{a.maxzoom}, approx. {len(todo) * 13 / 1024:.0f} MB", flush=True)
    if a.dry_run:
        return 0
    db = open_mbtiles(a.out, a.name, rings, a.minzoom, a.maxzoom)
    have = {(z, x, (2 ** z - 1 - r)) for z, x, r in db.execute("SELECT zoom_level, tile_column, tile_row FROM tiles")}
    todo = [t for t in todo if t not in have]
    print(f"{len(have)} already there, {len(todo)} to download", flush=True)
    lock = threading.Lock()
    done = [0, 0]                              # downloaded, empty
    failed: list[tuple[int, int, int]] = []
    t0 = time.time()

    def work(t, attempts=4):
        z, x, y = t
        try:
            data = fetch(WMTS.format(z=z, x=x, y=y), attempts)
        except TileError:
            with lock:
                failed.append(t)             # the public service hiccups now and then: retried below
            return
        with lock:
            if data:
                # MBTiles uses TMS row order (south to north)
                db.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", (z, x, 2 ** z - 1 - y, data))
                done[0] += 1
            else:
                done[1] += 1
            n = done[0] + done[1]
            if n % 500 == 0:
                db.commit()
                rate = n / max(time.time() - t0, 1)
                print(f"{n}/{len(todo)} ({rate:.1f}/s, ~{(len(todo) - n) / max(rate, 0.1) / 60:.0f} min left)",
                      flush=True)

    with ThreadPoolExecutor(max_workers=max(1, min(a.threads, 8))) as pool:
        list(pool.map(work, todo))
    for round_ in (1, 2):                    # slow retry passes for the tiles that failed
        if not failed:
            break
        retry, failed[:] = list(failed), []
        print(f"retry pass {round_}: {len(retry)} tiles", flush=True)
        time.sleep(30)
        for t in retry:
            work(t, attempts=6)
            time.sleep(0.5)
    db.commit()
    db.close()
    print(f"done: {done[0]} tiles, {done[1]} without data, {len(failed)} failed", flush=True)
    if failed:
        print("some tiles failed: run the same command again to fetch the rest", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
