# AeroNexus tsdk helper: runs the DJI Thermal SDK command line tool dji_irp for the pv service.
# DJI ships the SDK for Linux x64 only. On the Pi 5 (ARM64, 16 KB memory pages) qemu cannot load its
# libraries (4 KB aligned segments, mmap fails), Box64 can. On an x64 host dji_irp runs natively.
# The SDK itself is NOT in the image (DJI license): mounted read-only at /opt/dji-tsdk.
FROM debian:trixie-20260918-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
ARG TARGETARCH
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3=3.13.5-1; \
    if [ "$TARGETARCH" = "arm64" ]; then \
      # native libgomp: Box64 wraps it instead of emulating the x64 one
      apt-get install -y --no-install-recommends box64=0.3.4+dfsg-1 libgomp1=14.2.0-19; \
      dpkg --add-architecture amd64; apt-get update; \
      cd /tmp && apt-get download libgomp1:amd64=14.2.0-19 && mkdir -p /opt/x86libs && \
      dpkg -x libgomp1_*_amd64.deb /tmp/gomp && cp -L /tmp/gomp/usr/lib/x86_64-linux-gnu/libgomp.so.1 /opt/x86libs/ && \
      rm -rf /tmp/gomp /tmp/*.deb; \
    else \
      apt-get install -y --no-install-recommends libgomp1=14.2.0-19; \
    fi; \
    rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BOX64_LOG=0 BOX64_NOBANNER=1
WORKDIR /app
COPY tsdk_server.py .
RUN useradd --system --uid 10005 tsdk
USER tsdk
EXPOSE 6793
CMD ["python3", "tsdk_server.py"]
