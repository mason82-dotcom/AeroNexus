"""Temperatures (deg C) of an R-JPEG via the DJI Thermal SDK (TSDK).

The SDK is proprietary (DJI license) and therefore not part of the repository: it is unpacked into
edge/runtime/dji-tsdk by photovoltaik-tool/tools/install-tsdk.sh and mounted read-only at TSDK_DIR.
Its command line tool dji_irp converts one image ("measure" action, float32 output, 640 x 512).
"""
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .rjpeg import RAW_H, RAW_W


class ThermalError(RuntimeError):
    pass


@dataclass
class ThermalParams:
    emissivity: float = 0.85        # glass cover of PV modules (0.85 .. 0.90)
    reflected_temp: float = 23.0    # deg C, reflected apparent temperature (sky/surroundings)
    humidity: float = 70.0          # % relative humidity
    distance: float | None = None   # m camera -> target; None = from the image (LRF / altitude)

    @classmethod
    def from_dict(cls, d: dict) -> "ThermalParams":
        p = cls()
        for k in ("emissivity", "reflected_temp", "humidity", "distance"):
            if d.get(k) not in (None, ""):
                setattr(p, k, float(d[k]))
        if not 0.1 <= p.emissivity <= 1.0 or not 0 <= p.humidity <= 100 or not -40 <= p.reflected_temp <= 500:
            raise ValueError("Emissionsgrad 0,1-1, Luftfeuchte 0-100 %, Reflexionstemperatur -40-500 \u00b0C")
        return p


class Tsdk:
    def __init__(self, tsdk_dir: str):
        self.dir = Path(tsdk_dir)

    def _tool(self) -> Path | None:
        for cand in sorted(self.dir.rglob("dji_irp")):
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
        return None

    def available(self) -> bool:
        return self._tool() is not None

    def temperatures(self, jpeg: bytes, p: ThermalParams, distance: float) -> np.ndarray:
        tool = self._tool()
        if tool is None:
            raise ThermalError("DJI Thermal SDK nicht installiert (photovoltaik-tool/tools/install-tsdk.sh)")
        dist = p.distance if p.distance is not None else distance
        dist = min(max(dist, 1.0), 25.0)            # TSDK accepts 1..25 m
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "in.jpg", Path(tmp) / "out.raw"
            src.write_bytes(jpeg)
            cmd = [str(tool), "-s", str(src), "-a", "measure", "-o", str(out), "--measurefmt", "float32",
                   "--distance", f"{dist:.1f}", "--humidity", f"{p.humidity:.0f}",
                   "--emissivity", f"{p.emissivity:.2f}", "--reflection", f"{p.reflected_temp:.1f}"]
            env = dict(os.environ, LD_LIBRARY_PATH=str(tool.parent))
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, env=env, timeout=60)
            if proc.returncode != 0 or not out.exists():
                raise ThermalError(f"dji_irp fehlgeschlagen: {(proc.stderr or proc.stdout).strip()[-300:]}")
            data = np.fromfile(out, dtype="<f4")
        if data.size != RAW_W * RAW_H:
            raise ThermalError(f"dji_irp: unerwartete Gr\u00f6\u00dfe {data.size}")
        return data.reshape(RAW_H, RAW_W)
