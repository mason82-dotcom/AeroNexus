# AeroNexus compute agent: Python 3.12 standard library + GDAL tools (gdalwarp/COG, gdal2tiles).
# Base image pinned by digest (multi-arch: x64 node, ARM64 for tests on the Pi).
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.11.4@sha256:4b94b5e76e8b5616a4d5dc38e6e20def10ab70c3487eeb923d90547372e70a03

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY aeronexus_agent ./aeronexus_agent
RUN useradd --system --uid 10002 --create-home agent && mkdir -p /work && chown agent /work
USER agent
CMD ["python3", "-m", "aeronexus_agent.main"]
