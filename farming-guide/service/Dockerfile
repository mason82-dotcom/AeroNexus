# AeroNexus Farming Guide service: standard library HTTP + GDAL/OGR/numpy from the pinned GDAL image
# (same base as the compute agent; ARM64 for the Pi, no extra Python packages).
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.11.4@sha256:4b94b5e76e8b5616a4d5dc38e6e20def10ab70c3487eeb923d90547372e70a03

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AWS_HTTPS=NO AWS_VIRTUAL_HOSTING=FALSE CPL_VSIL_USE_TEMP_FILE_FOR_RANDOM_WRITE=YES GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR
WORKDIR /app
COPY farming ./farming
RUN useradd --system --uid 10003 farming
USER farming
EXPOSE 6791
CMD ["python3", "-m", "farming"]
