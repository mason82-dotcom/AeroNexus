"""Mavic 3M previews in the web API (computed in the background by multispectral.py).

A preview is named by the CaptureUUID of its shot. The web knows the object keys of the media list, so
/ms-preview/object.json?key=<object key> resolves any band file of a shot to its preview.
"""
import json
import re
import threading

from . import multispectral
from .server import HttpError, route, settings

_CAPTURE = re.compile(r"^[0-9a-f]{32}$")


def _prefix() -> str:
    return f"/vsis3/{settings.bucket}/ms-preview"


def _capture(capture: str) -> str:
    capture = capture.lower()
    if not _CAPTURE.match(capture):
        raise HttpError(400, "Ungueltige Aufnahme-Kennung")
    return capture


def _load(path: str) -> bytes:
    try:
        return multispectral._read(path)
    except FileNotFoundError as exc:
        raise HttpError(404, "Noch keine Vorschau fuer diese Aufnahme") from exc


@route("GET", "/api/farming/ms-preview/object.json")
def preview_by_object(req, user):
    key = req.query.get("key", "")
    if not key or len(key) > 512:
        raise HttpError(400, "Parameter key fehlt")
    if not key.lower().endswith(multispectral._TIFF_EXT):        # only the band TIFFs are scanned
        raise HttpError(404, "Keine Multispektral-Aufnahme")
    try:
        record = multispectral.object_record(_prefix(), key)
    except FileNotFoundError as exc:
        raise HttpError(404, "Datei noch nicht gepr\u00fcft") from exc
    if not record.get("capture"):
        raise HttpError(404, "Keine Multispektral-Aufnahme")
    return 200, json.loads(_load(f"{_prefix()}/{record['capture']}.json"))


@route("GET", "/api/farming/ms-preview/{capture}.json")
def preview_json(req, user, capture):
    return 200, json.loads(_load(f"{_prefix()}/{_capture(capture)}.json"))


@route("GET", "/api/farming/ms-preview/{capture}.png")
def preview_png(req, user, capture):
    return 200, _load(f"{_prefix()}/{_capture(capture)}.png"), "image/png"


def start_worker() -> None:
    multispectral.start(f"/vsis3/{settings.media_bucket}", _prefix(), threading.Event())
