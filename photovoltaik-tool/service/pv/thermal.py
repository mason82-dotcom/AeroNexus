"""Temperatures (deg C) of an R-JPEG via the DJI Thermal SDK (TSDK).

The SDK is proprietary (DJI license, Linux x64 only) and not part of the repository. It runs in the helper
container `tsdk` (photovoltaik-tool/tsdk: natively on x64, with Box64 on the Pi), which this module calls
over HTTP: R-JPEG in, 640 x 512 float32 temperatures out.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

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
        # value ranges of the DJI Thermal SDK (dji_irp)
        if not 0.1 <= p.emissivity <= 1.0 or not 20 <= p.humidity <= 100 or not -40 <= p.reflected_temp <= 500:
            raise ValueError("Emissionsgrad 0,1-1, Luftfeuchte 20-100 %, Reflexionstemperatur -40-500 \u00b0C")
        return p


class Tsdk:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=5) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError):
            return {"sdk": False, "reachable": False}

    def available(self) -> bool:
        return bool(self.health().get("sdk"))

    def temperatures(self, jpeg: bytes, p: ThermalParams, distance: float) -> np.ndarray:
        dist = p.distance if p.distance is not None else distance
        query = urllib.parse.urlencode({"distance": round(dist, 1), "humidity": p.humidity,
                                        "emissivity": p.emissivity, "reflection": p.reflected_temp})
        req = urllib.request.Request(f"{self.url}/measure?{query}", data=jpeg, method="POST",
                                     headers={"Content-Type": "image/jpeg"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise ThermalError(str(detail)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ThermalError(f"DJI-Thermal-SDK-Dienst nicht erreichbar: {exc}") from exc
        data = np.frombuffer(raw, dtype="<f4")
        if data.size != RAW_W * RAW_H:
            raise ThermalError(f"dji_irp: unerwartete Gr\u00f6\u00dfe {data.size}")
        return data.reshape(RAW_H, RAW_W)
