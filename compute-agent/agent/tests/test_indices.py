"""Vegetation indices from a multispectral orthophoto (aeronexus_agent/indices.py) with the real GDAL tools.

Synthetic ODM-like orthophoto: bands green/red/rededge/nir + alpha, constant reflectances, left 10 columns
transparent. Expected: NDVI = (0.5-0.1)/(0.5+0.1), NDRE = (0.5-0.3)/(0.5+0.3), GNDVI = (0.5-0.1)/(0.5+0.1).
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from aeronexus_agent import indices

ROOT = Path(__file__).resolve().parents[3]
DEFINITIONS = indices.load_definitions(str(ROOT / "farming-guide" / "indices.json"))
REFLECTANCE = {"Green": 0.1, "Red": 0.1, "RedEdge": 0.3, "NIR": 0.5}
EXPECTED = {"ndvi": 0.4 / 0.6, "ndre": 0.2 / 0.8, "gndvi": 0.4 / 0.6}
COLS, ROWS, TRANSPARENT = 64, 48, 10


def orthophoto(path: Path, bands=("Green", "Red", "RedEdge", "NIR")) -> None:
    ds = gdal.GetDriverByName("GTiff").Create(str(path), COLS, ROWS, len(bands) + 1, gdal.GDT_Float32)
    ds.SetGeoTransform((560000, 0.5, 0, 5670000, 0, -0.5))       # EPSG:25832, 0.5 m pixels
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25832)
    ds.SetProjection(srs.ExportToWkt())
    for i, name in enumerate(bands, 1):
        band = ds.GetRasterBand(i)
        band.SetDescription(name)
        band.WriteArray(np.full((ROWS, COLS), REFLECTANCE[name], dtype=np.float32))
    alpha = ds.GetRasterBand(len(bands) + 1)
    alpha.SetColorInterpretation(gdal.GCI_AlphaBand)
    mask = np.ones((ROWS, COLS), dtype=np.float32)
    mask[:, :TRANSPARENT] = 0
    alpha.WriteArray(mask)
    ds = None


class IndicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_band_detection(self):
        ortho = self.tmp / "ortho.tif"
        orthophoto(ortho)
        bands, alpha = indices.band_map(ortho, DEFINITIONS)
        self.assertEqual(bands, {"green": 1, "red": 2, "rededge": 3, "nir": 4})
        self.assertEqual(alpha, 5)
        self.assertTrue(indices.is_multispectral(ortho, DEFINITIONS))

    def test_index_values_mask_and_layers(self):
        ortho = self.tmp / "ortho.tif"
        orthophoto(ortho)
        files, layers = indices.build(ortho, self.tmp / "work", self.out, DEFINITIONS, "EPSG:25832", 16, 18, 1)
        self.assertEqual([l["kind"] for l in layers], ["ndvi", "ndre", "gndvi"])
        for layer in layers:
            kind = layer["kind"]
            with self.subTest(kind):
                ds = gdal.Open(str(self.out / f"{kind}.tif"))
                self.assertEqual(ds.GetMetadataItem("LAYOUT", "IMAGE_STRUCTURE"), "COG")
                band = ds.GetRasterBand(1)
                data = band.ReadAsArray()
                nodata = band.GetNoDataValue()
                self.assertEqual(nodata, indices.NODATA)
                valid = data[data != nodata]
                self.assertTrue(np.allclose(valid, EXPECTED[kind], atol=1e-4), f"{kind}: {valid[:3]}")
                self.assertTrue((data[:, :TRANSPARENT - 1] == nodata).all())      # alpha 0 -> nodata
                self.assertTrue(layer["relative"])
                self.assertAlmostEqual(layer["stats"]["mean"], EXPECTED[kind], places=3)
                self.assertAlmostEqual(layer["stats"]["valid_ratio"], (COLS - TRANSPARENT) / COLS, places=2)
                self.assertTrue((self.out / layer["path"]).is_dir())               # XYZ tiles written
                self.assertTrue(any((self.out / layer["path"]).rglob("*.png")))

    def test_missing_band_skips_index(self):
        ortho = self.tmp / "ortho.tif"
        orthophoto(ortho, bands=("Green", "Red", "NIR"))                       # no red edge -> no NDRE
        _, layers = indices.build(ortho, self.tmp / "work", self.out, DEFINITIONS, "EPSG:25832", 16, 17, 1)
        self.assertEqual([l["kind"] for l in layers], ["ndvi", "gndvi"])

    def test_rgb_orthophoto_is_not_multispectral(self):
        ortho = self.tmp / "rgb.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(ortho), 8, 8, 3, gdal.GDT_Byte)
        for i, ci in enumerate((gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand), 1):
            ds.GetRasterBand(i).SetColorInterpretation(ci)
        ds = None
        self.assertFalse(indices.is_multispectral(ortho, DEFINITIONS))


if __name__ == "__main__":
    unittest.main()
