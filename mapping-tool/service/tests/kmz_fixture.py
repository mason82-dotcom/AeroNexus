"""Synthetic DJI Pilot 2 KMZ (mapping2d), structure copied from a real Pilot 2 file (RC Pro, M3E, WPML 1.0.6).

Coordinates are generic test values (centre of Germany, as in edge/.env.example), not a real site.
"""
import io
import zipfile

BASE_LON, BASE_LAT = 10.45, 51.16


def _p(dlon: float, dlat: float) -> list[float]:
    return [round(BASE_LON + dlon, 7), round(BASE_LAT + dlat, 7)]


# polygon: ~150 x 110 m rectangle, Pilot 2 writes it without closing point
POLYGON = [_p(0, 0), _p(0.002, 0), _p(0.002, 0.001), _p(0, 0.001)]

TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <wpml:createTime>1714638796796</wpml:createTime>
    <wpml:updateTime>1714827258623</wpml:updateTime>
    <wpml:missionConfig>
      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>
      <wpml:finishAction>goHome</wpml:finishAction>
      <wpml:exitOnRCLost>executeLostAction</wpml:exitOnRCLost>
      <wpml:executeRCLostAction>goBack</wpml:executeRCLostAction>
      <wpml:takeOffSecurityHeight>80</wpml:takeOffSecurityHeight>
      <wpml:globalTransitionalSpeed>10</wpml:globalTransitionalSpeed>
      <wpml:droneInfo>
        <wpml:droneEnumValue>77</wpml:droneEnumValue>
        <wpml:droneSubEnumValue>0</wpml:droneSubEnumValue>
      </wpml:droneInfo>
    </wpml:missionConfig>
    <Folder>
      <wpml:templateType>{template_type}</wpml:templateType>
      <wpml:templateId>0</wpml:templateId>
      <wpml:waylineCoordinateSysParam>
        <wpml:coordinateMode>WGS84</wpml:coordinateMode>
        <wpml:heightMode>relativeToStartPoint</wpml:heightMode>
        <wpml:globalShootHeight>70</wpml:globalShootHeight>
        <wpml:surfaceFollowModeEnable>1</wpml:surfaceFollowModeEnable>
        <wpml:isRealtimeSurfaceFollow>1</wpml:isRealtimeSurfaceFollow>
        <wpml:surfaceRelativeHeight>70</wpml:surfaceRelativeHeight>
      </wpml:waylineCoordinateSysParam>
      <wpml:autoFlightSpeed>15</wpml:autoFlightSpeed>
      <Placemark>
        <wpml:shootType>time</wpml:shootType>
        <wpml:direction>0</wpml:direction>
        <wpml:margin>0</wpml:margin>
        <wpml:overlap>
          <wpml:orthoLidarOverlapH>80</wpml:orthoLidarOverlapH>
          <wpml:orthoLidarOverlapW>80</wpml:orthoLidarOverlapW>
          <wpml:orthoCameraOverlapH>80</wpml:orthoCameraOverlapH>
          <wpml:orthoCameraOverlapW>80</wpml:orthoCameraOverlapW>
        </wpml:overlap>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>
{coordinates}
              </coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
        <wpml:ellipsoidHeight>120</wpml:ellipsoidHeight>
        <wpml:height>70</wpml:height>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""

WAYLINES = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document>
    <Folder>
      <wpml:templateId>0</wpml:templateId>
      <wpml:executeHeightMode>realTimeFollowSurface</wpml:executeHeightMode>
      <wpml:autoFlightSpeed>15</wpml:autoFlightSpeed>
{placemarks}
    </Folder>
  </Document>
</kml>
"""

PLACEMARK = """      <Placemark>
        <Point>
          <coordinates>
            {lon},{lat}
          </coordinates>
        </Point>
        <wpml:index>{index}</wpml:index>
        <wpml:executeHeight>70</wpml:executeHeight>
      </Placemark>"""

# flight path: two passes along the polygon
PATH = [_p(0, 0.0002), _p(0.002, 0.0002), _p(0.002, 0.0008), _p(0, 0.0008)]


def template_text(template_type: str = "mapping2d", closed: bool = False) -> str:
    ring = POLYGON + [POLYGON[0]] if closed else POLYGON
    coords = "\n".join(f"                {lon:.15g},{lat:.15g},0" for lon, lat in ring)
    return TEMPLATE.replace("{template_type}", template_type).replace("{coordinates}", coords)


def waylines_text() -> str:
    marks = "\n".join(PLACEMARK.format(lon=f"{lon:.15g}", lat=f"{lat:.15g}", index=i)
                      for i, (lon, lat) in enumerate(PATH))
    return WAYLINES.replace("{placemarks}", marks)


def make_kmz(template_type: str = "mapping2d", closed: bool = False, with_template: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if with_template:
            zf.writestr("wpmz/template.kml", template_text(template_type, closed))
        zf.writestr("wpmz/waylines.wpml", waylines_text())
    return buf.getvalue()


def read(kmz: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(kmz)) as zf:
        return zf.read(name)
