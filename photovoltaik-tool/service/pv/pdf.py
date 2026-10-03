"""Minimal PDF writer for the inspection report (standard library + GDAL for JPEG): A4 pages, the standard
Helvetica fonts (WinAnsi encoding: German umlauts, degree sign, superscript 2) and embedded JPEG images.
"""
import io
import uuid

import numpy as np
from osgeo import gdal

A4 = (595.0, 842.0)


def _esc(text: str) -> bytes:
    raw = str(text).encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def png_to_jpeg(png: bytes, quality: int = 85) -> tuple[bytes, int, int]:
    """(jpeg bytes, width, height) of a PNG via GDAL."""
    src, dst = f"/vsimem/{uuid.uuid4()}.png", f"/vsimem/{uuid.uuid4()}.jpg"
    gdal.FileFromMemBuffer(src, png)
    try:
        ds = gdal.Open(src)
        w, h = ds.RasterXSize, ds.RasterYSize
        if ds.RasterCount < 3:                         # grey -> RGB, PDF DeviceRGB below
            band = ds.GetRasterBand(1).ReadAsArray()
            mem = gdal.GetDriverByName("MEM").Create("", w, h, 3, gdal.GDT_Byte)
            for i in range(3):
                mem.GetRasterBand(i + 1).WriteArray(band)
            ds = mem
        gdal.GetDriverByName("JPEG").CreateCopy(dst, ds, options=[f"QUALITY={quality}"])
        f = gdal.VSIFOpenL(dst, "rb")
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        data = gdal.VSIFReadL(1, size, f)
        gdal.VSIFCloseL(f)
        return data, w, h
    finally:
        for p in (src, dst, dst + ".aux.xml"):
            if gdal.VSIStatL(p):
                gdal.Unlink(p)


class Document:
    def __init__(self, title: str):
        self.title = title
        self.objects: list[bytes] = []       # 1-based object numbers = index + 1
        self.pages: list[int] = []
        self._images: list[tuple[int, bytes]] = []
        self.ops: list[bytes] | None = None
        self._page_images: dict[str, int] = {}
        self.font = self._add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        self.bold = self._add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        self.pages_obj = self._add(b"")                # filled in save()
        self.y = 0.0

    def _add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    # ---------------------------------------------------------------- page content

    def new_page(self) -> None:
        self._finish_page()
        self.ops = []
        self._page_images = {}
        self.y = A4[1] - 50

    def _finish_page(self) -> None:
        if self.ops is None:
            return
        content = b"\n".join(self.ops)
        stream = self._add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        xobj = b" ".join(b"/%s %d 0 R" % (k.encode(), v) for k, v in self._page_images.items())
        page = self._add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources "
                         b"<< /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << %s >> >> >>"
                         % (self.pages_obj, A4[0], A4[1], stream, self.font, self.bold, xobj))
        self.pages.append(page)
        self.ops = None

    def text(self, x: float, y: float, s: str, size: float = 10, bold: bool = False, rgb=(0, 0, 0)) -> None:
        self.ops.append(b"BT %.3f %.3f %.3f rg /%s %.1f Tf %.2f %.2f Td (%s) Tj ET"
                        % (*rgb, b"F2" if bold else b"F1", size, x, y, _esc(s)))

    def line_text(self, s: str, size: float = 10, bold: bool = False, x: float = 50, gap: float = 4,
                  rgb=(0, 0, 0)) -> None:
        if self.y < 60:
            self.new_page()
        self.text(x, self.y, s, size, bold, rgb)
        self.y -= size + gap

    def wrap(self, s: str, size: float = 9, width_chars: int = 105, x: float = 50) -> None:
        words, line = s.split(), ""
        for w in words:
            if len(line) + len(w) + 1 > width_chars:
                self.line_text(line, size, x=x, gap=2)
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            self.line_text(line, size, x=x, gap=2)

    def rect(self, x: float, y: float, w: float, h: float, rgb=(0.85, 0.85, 0.85), fill: bool = True) -> None:
        op = b"f" if fill else b"S"
        col = b"rg" if fill else b"RG"
        self.ops.append(b"q %.3f %.3f %.3f %s %.2f %.2f %.2f %.2f re %s Q" % (*rgb, col, x, y, w, h, op))

    def image(self, jpeg: bytes, px_w: int, px_h: int, x: float, y: float, w: float, h: float) -> None:
        """Place a JPEG fitted into the box (x, y = lower left), keeping the aspect ratio."""
        obj = self._add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                        b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (px_w, px_h, len(jpeg))
                        + jpeg + b"\nendstream")
        name = f"Im{obj}"
        self._page_images[name] = obj
        scale = min(w / px_w, h / px_h)
        dw, dh = px_w * scale, px_h * scale
        self.ops.append(b"q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q" % (dw, dh, x + (w - dw) / 2, y + (h - dh) / 2,
                                                                  name.encode()))

    # ---------------------------------------------------------------- output

    def save(self) -> bytes:
        self._finish_page()
        kids = b" ".join(b"%d 0 R" % p for p in self.pages)
        self.objects[self.pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(self.pages))
        info = self._add(b"<< /Title (%s) /Producer (AeroNexus) >>" % _esc(self.title))
        catalog = self._add(b"<< /Type /Catalog /Pages %d 0 R >>" % self.pages_obj)
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(self.objects, 1):
            offsets.append(out.tell())
            out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
        xref = out.tell()
        out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(self.objects) + 1))
        for off in offsets:
            out.write(b"%010d 00000 n \n" % off)
        out.write(b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                  % (len(self.objects) + 1, catalog, info, xref))
        return out.getvalue()


def grey_placeholder() -> tuple[bytes, int, int]:
    """Tiny grey JPEG for missing crops (keeps the layout)."""
    name = f"/vsimem/{uuid.uuid4()}.png"
    mem = gdal.GetDriverByName("MEM").Create("", 8, 8, 3, gdal.GDT_Byte)
    for i in range(3):
        mem.GetRasterBand(i + 1).WriteArray(np.full((8, 8), 225, dtype=np.uint8))
    gdal.GetDriverByName("PNG").CreateCopy(name, mem)
    f = gdal.VSIFOpenL(name, "rb")
    gdal.VSIFSeekL(f, 0, 2)
    size = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    png = gdal.VSIFReadL(1, size, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink(name)
    return png_to_jpeg(png)
