"""Mavic 3M calibration and NDVI preview (farming/multispectral.py) with synthetic band TIFFs in /vsimem/."""
import json
import unittest
import uuid

import numpy as np
from osgeo import gdal

from farming import multispectral as ms

XMP_TEMPLATE = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" drone-dji:BandName="{band}"'
    ' drone-dji:ImageSource="{source}" drone-dji:CaptureUUID="{capture}"'
    ' drone-dji:BlackLevel="3200" drone-dji:SensorGain="{gain}" drone-dji:ExposureTime="1953"'
    ' drone-dji:SensorGainAdjustment="{pcam}" drone-dji:Irradiance="{irr}"'
    ' drone-dji:CalibratedOpticalCenterX="{cx}" drone-dji:CalibratedOpticalCenterY="{cy}"'
    ' drone-dji:VignettingData="0, 0, 0, 0, 0, 0"'
    ' drone-dji:CalibratedHMatrix="1,0,0,0,1,0,0,0,1"/></rdf:RDF></x:xmpmeta>')


SOURCES = {"Green": "MS_G_CAMERA", "Red": "MS_R_CAMERA", "RedEdge": "MS_RE_CAMERA", "NIR": "MS_NIR_CAMERA"}
CAP1, CAP2 = "c0b53d9a3dd045db9c77bf6c744608d7", "0123456789abcdef0123456789abcdef"


def xmp(band="NIR", gain=5.0, pcam=1.0, irr=900.0, cx=80, cy=60, source=None, capture=CAP1) -> str:
    return XMP_TEMPLATE.format(band=band, gain=gain, pcam=pcam, irr=irr, cx=cx, cy=cy,
                               source=SOURCES.get(band, "") if source is None else source, capture=capture)


def band_tiff(path: str, value: int, band: str, gain: float, pcam: float, irr: float, size=(160, 120),
              capture=CAP1):
    ds = gdal.GetDriverByName("GTiff").Create(path, size[0], size[1], 1, gdal.GDT_UInt16)
    ds.GetRasterBand(1).Fill(value)
    ds.SetMetadata([xmp(band, gain, pcam, irr, size[0] / 2, size[1] / 2, capture=capture)], "xml:XMP")
    ds = None


def expected_ref(dn, gain, pcam, irr):
    return (dn - 3200) / 65535 / (gain * 1953e-6) * pcam / irr


class CalibrationTest(unittest.TestCase):
    def meta(self, **kw):
        base = dict(black_level=3200, gain=5.0, exposure_s=1953e-6, pcam=1.0, irradiance=900.0,
                    center=(100.0, 80.0), vignetting=(), hmatrix=np.eye(3))
        base.update(kw)
        return ms.BandMeta(**base)

    def test_parse_real_xmp_fields(self):
        meta = ms.band_meta(ms.parse_xmp(xmp("NIR", 5.939, 1.009263, 915.075, 1296, 972).encode()))
        self.assertAlmostEqual(meta.exposure_s, 0.001953)
        self.assertEqual((meta.gain, meta.pcam, meta.irradiance), (5.939, 1.009263, 915.075))
        self.assertEqual(meta.center, (1296.0, 972.0))
        np.testing.assert_array_equal(meta.hmatrix, np.eye(3))

    def test_vignetting_is_one_at_the_centre_and_grows_outwards(self):
        meta = self.meta(vignetting=(1e-3, 0, 0, 0, 0, 0), center=(8.0, 8.0))
        f = ms.vignetting_factor((4, 4), meta, scale=4)
        self.assertAlmostEqual(f[1, 1], 1 + 1e-3 * np.hypot(6 - 8, 6 - 8))
        self.assertGreater(f[3, 3], f[2, 2])

    def test_reflectance_formula(self):
        dn = np.full((2, 2), 20000)
        meta = self.meta(gain=4.8, pcam=0.526279, irradiance=991.154)
        np.testing.assert_allclose(ms.reflectance(dn, meta, 1), expected_ref(20000, 4.8, 0.526279, 991.154))

    def test_warp_identity_and_shift(self):
        img = np.arange(16, dtype=float).reshape(4, 4)
        np.testing.assert_array_equal(ms.warp(img, np.eye(3), 1), img)
        shift = np.array([[1, 0, 1], [0, 1, 0], [0, 0, 1]], dtype=float)   # band pixel x -> x + 1
        out = ms.warp(img, shift, 1)
        np.testing.assert_array_equal(out[:, 1:], img[:, :3])
        self.assertTrue(np.isnan(out[:, 0]).all())

    def test_ndvi_and_statistics(self):
        index = ms.ndvi(np.array([0.5, 0.3, 0.0, np.nan]), np.array([0.1, 0.3, 0.0, 0.2]))
        np.testing.assert_allclose(index[:2], [0.4 / 0.6, 0.0])
        self.assertTrue(np.isnan(index[2:]).all())
        stats = ms.statistics(index)
        self.assertEqual(stats["valid_fraction"], 0.5)

    def test_band_and_capture_from_xmp(self):
        read = lambda **kw: ms.parse_xmp(xmp(**kw).encode())                       # noqa: E731
        self.assertEqual(ms.band_of(read(band="Red")), "R")
        self.assertEqual(ms.band_of(read(band="Red edge", source="")), "RE")     # BandName as fallback
        self.assertEqual(ms.band_of(read(band="Green", source="")), "G")
        self.assertIsNone(ms.band_of(read(band="", source="MS_CAMERA")))        # _F.JPG composite
        self.assertEqual(ms.capture_of(read(capture=CAP1.upper())), CAP1)
        self.assertIsNone(ms.capture_of(read(capture="../../x")))


class PipelineTest(unittest.TestCase):
    def setUp(self):
        root = f"/vsimem/{uuid.uuid4()}"
        self.media, self.out = f"{root}/media", f"{root}/farming/ms-preview"

    def test_preview_from_band_tiffs_with_pilot2_object_names(self):
        # Pilot 2 stores uploads as org_<hash>_<ms>.tiff (2026-10-15): bands and shot come from the XMP only
        nir_key, red_key = "wayline/org_31d64f349f64b626_1792065762000.tiff", "wayline/DJI_20261015140240_0001_MS_R.TIF"
        band_tiff(f"{self.media}/{nir_key}", 30000, "NIR", 5.939, 1.009263, 915.075)
        band_tiff(f"{self.media}/{red_key}", 9000, "Red", 4.756, 0.526279, 993.725)
        band_tiff(f"{self.media}/wayline/org_aa_1.tiff", 30000, "NIR", 5.9, 1.0, 900.0, capture=CAP2)  # no red
        gdal.FileFromMemBuffer(f"{self.media}/wayline/org_bb_2.jpg", b"composite")
        self.assertEqual(ms.process_pending(self.media, self.out), 1)
        self.assertEqual(ms.process_pending(self.media, self.out), 0)            # done once
        result = json.loads(ms._read(f"{self.out}/{CAP1}.json"))
        nir = expected_ref(30000, 5.939, 1.009263, 915.075)
        red = expected_ref(9000, 4.756, 0.526279, 993.725)
        self.assertAlmostEqual(result["stats"]["mean"], round((nir - red) / (nir + red), 4), places=3)
        self.assertEqual(result["stats"]["valid_fraction"], 1.0)
        self.assertEqual(result["bands_present"], ["R", "NIR"])
        self.assertEqual(result["objects"], {"R": red_key, "NIR": nir_key})
        self.assertEqual(ms.object_record(self.out, nir_key)["capture"], CAP1)
        png = gdal.Open(f"{self.out}/{CAP1}.png")
        self.assertEqual((png.RasterXSize, png.RasterYSize, png.RasterCount), (40, 30, 3))

    def test_previews_for_a_page_of_the_media_list(self):
        band_tiff(f"{self.media}/wayline/org_a_1.tiff", 30000, "NIR", 5.9, 1.0, 900.0)
        band_tiff(f"{self.media}/wayline/org_b_2.tiff", 9000, "Red", 4.7, 0.5, 990.0)
        band_tiff(f"{self.media}/wayline/org_c_3.tiff", 30000, "NIR", 5.9, 1.0, 900.0, capture=CAP2)  # no red
        ms.process_pending(self.media, self.out)
        got = ms.previews_for(self.out, ["wayline/org_a_1.tiff", "wayline/org_b_2.tiff", "wayline/org_c_3.tiff",
                                         "wayline/org_d_4.jpg", "wayline/unknown.tif"])
        self.assertEqual(got["wayline/org_a_1.tiff"]["capture"], CAP1)
        self.assertIs(got["wayline/org_a_1.tiff"], got["wayline/org_b_2.tiff"])      # one read per capture
        self.assertEqual([got[k] for k in ("wayline/org_c_3.tiff", "wayline/org_d_4.jpg", "wayline/unknown.tif")],
                         [None, None, None])

    def test_band_uploaded_later_completes_the_capture(self):
        band_tiff(f"{self.media}/wayline/org_a_1.tiff", 30000, "NIR", 5.9, 1.0, 900.0)
        self.assertEqual(ms.process_pending(self.media, self.out), 0)
        band_tiff(f"{self.media}/wayline/org_b_2.tiff", 9000, "Red", 4.7, 0.5, 990.0)
        self.assertEqual(ms.process_pending(self.media, self.out), 1)

    def test_each_tiff_is_read_once(self):
        band_tiff(f"{self.media}/wayline/org_a_1.tiff", 30000, "NIR", 5.9, 1.0, 900.0)
        band_tiff(f"{self.media}/wayline/other.tif", 1, "", 1.0, 1.0, 1.0, capture="")   # not a M3M band
        self.assertEqual(ms.scan_new(self.media, self.out), 2)
        self.assertEqual(ms.scan_new(self.media, self.out), 0)
        self.assertIsNone(ms.object_record(self.out, "wayline/other.tif")["capture"])
        self.assertEqual(ms.captures(self.out), {CAP1: {"NIR": "wayline/org_a_1.tiff"}})


if __name__ == "__main__":
    unittest.main()
