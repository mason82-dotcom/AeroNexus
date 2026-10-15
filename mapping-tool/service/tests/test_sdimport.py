"""SD card import (app/sdimport.py): file selection, PPK raw data, video and photo metadata (no network)."""
import tempfile
import unittest
from pathlib import Path

from app import sdimport

TELEMETRY = (b"[iso: 400] [focal_len: 24.00], [latitude: 0.000000] [longitude: 0.000000] "
             b"[rel_alt: -0.000 abs_alt: 106.456] [gb_yaw: -99.4 gb_pitch: 0.0 gb_roll: 0.0]")
XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
       b'drone-dji:DroneSerialNumber="%s" drone-dji:GpsStatus="RTK" drone-dji:GpsLatitude="51.16" '
       b'drone-dji:GpsLongitude="10.45" drone-dji:AbsoluteAltitude="+277.2" drone-dji:RelativeAltitude="+118.9" '
       b'drone-dji:GimbalYawDegree="+130.8" drone-dji:GimbalPitchDegree="-90.0"/></rdf:RDF></x:xmpmeta>')


class CardLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dcim = Path(self.tmp.name) / "DCIM"
        flight = self.dcim / "DJI_202504271730_001_SeeRutsche"
        for name in ("DJI_20250427173919_0003_V.JPG", "DJI_20250427173642_0002_D.MRK",
                     "DJI_20250427173642_0002_D.OBS", "DJI_20250427173642_0002_D.NAV",
                     "DJI_20250427173642_0002_D.RTK", "ppk_file_name.pbk", "DJI_x_V.MP4", "DJI_x_V.DNG"):
            (flight / name).parent.mkdir(parents=True, exist_ok=True)
            (flight / name).write_bytes(b"x")
        (self.dcim / "System Volume Information").mkdir()
        (self.dcim / "System Volume Information" / "IndexerVolumeGuid").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_media_and_ppk_are_separated(self):
        media = [p.name for p in sdimport.media_files(self.dcim)]
        ppk = sorted(p.suffix.upper() for p in sdimport.ppk_files(self.dcim))
        self.assertEqual(media, ["DJI_20250427173919_0003_V.JPG", "DJI_x_V.DNG", "DJI_x_V.MP4"])
        self.assertEqual(ppk, [".MRK", ".NAV", ".OBS", ".PBK", ".RTK"])      # system folder left out

    def test_object_keys(self):
        self.assertEqual(sdimport.media_key("2118-FB33", "ab" * 32, "DJI_1_V.JPG"),
                         f"wayline/sdimport-2118-FB33/{'ab' * 32}/DJI_1_V.JPG")
        self.assertEqual(sdimport.ppk_key("2118-FB33", "DJI_202504271730_001_SeeRutsche/a.OBS"),
                         "ppk/sdimport-2118-FB33/DJI_202504271730_001_SeeRutsche/a.OBS")


class RulesTest(unittest.TestCase):
    def test_skipped_types_follow_pilot_setting_unless_included(self):
        self.assertEqual(sdimport.skipped_ext("dng,mp4", ""), {".dng", ".mp4"})
        self.assertEqual(sdimport.skipped_ext("dng, .MP4", "mp4"), {".dng"})
        self.assertEqual(sdimport.skipped_ext("", None), set())


class VideoTest(unittest.TestCase):
    def test_telemetry_embedded_and_model_at_the_end(self):
        # Matrice 4 cards carry no SRT; the _S video names its model only in the moov atom at the end
        info = sdimport.video_meta(b"\x00ftyp" + TELEMETRY, b"...DJI DJI Matrice 4T...", None)
        self.assertEqual(info, {"model": "M4T", "lat": 0.0, "lon": 0.0, "rel_alt": -0.0, "abs_alt": 106.456,
                                "gimbal_yaw": -99.4})

    def test_srt_sidecar_wins(self):
        srt = TELEMETRY.decode().replace("106.456", "50.0")
        self.assertEqual(sdimport.video_meta(b"Mavic 3M", b"", srt)["abs_alt"], 50.0)

    def test_unknown_model_is_refused(self):
        with self.assertRaises(ValueError):
            sdimport.video_meta(TELEMETRY, b"", None)


class PhotoTest(unittest.TestCase):
    def test_serial_and_geodata_from_xmp(self):
        exif_model = {"model": "M4T"}
        orig = sdimport.mediameta.extract
        sdimport.mediameta.extract = lambda data, size, name: {**orig(data, size, name), **exif_model}
        try:
            info, serial = sdimport.photo_meta(XMP % b"1581F7K3C251C00C7XZT", 1000, "DJI_1_V.JPG")
        finally:
            sdimport.mediameta.extract = orig
        self.assertEqual(serial, "1581F7K3C251C00C7XZT")
        self.assertEqual((info["rel_alt"], info["gimbal_yaw"]), (118.9, 130.8))
        body = sdimport.callback_body(info, serial, "m" * 32, "s" * 64, "DJI_1_V.JPG", "F", "k")
        self.assertEqual((body["ext"]["drone_model_key"], body["ext"]["payload_model_key"]), ("0-99-1", "1-89-0"))

    def test_photo_without_serial_is_refused(self):
        orig = sdimport.mediameta.extract
        sdimport.mediameta.extract = lambda data, size, name: {**orig(data, size, name), "model": "M4T"}
        try:
            with self.assertRaises(ValueError):
                sdimport.photo_meta(XMP % b"", 1000, "DJI_1_V.JPG")
        finally:
            sdimport.mediameta.extract = orig


if __name__ == "__main__":
    unittest.main()
