# AeroNexus mapping service (FastAPI). Base image pinned by digest (multi-arch: Pi ARM64 and x64).

# PPK (app/ppk.py): RTKLIB rnx2rtkp (rtklibexplorer demo5) and crx2rnx (RNXCMP, Hatanaka RINEX), pinned
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS gnss
ARG RTKLIB_TAG=b34k
ARG RTKLIB_COMMIT=55a0f2c742a605a3d27a90b17556b2cfdf98ad50
ARG RNXCMP_VERSION=4.2.0
ARG RNXCMP_SHA256=9a452d2908a27c5b3152cc424e048847221c065e97f6b1f1a585c7b0c34fbace
RUN apt-get update && apt-get install -y --no-install-recommends gcc make libc6-dev git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git clone -q --depth 1 --branch "$RTKLIB_TAG" https://github.com/rtklibexplorer/RTKLIB.git rtklib \
 && test "$(git -C rtklib rev-parse HEAD)" = "$RTKLIB_COMMIT" \
 && make -s -C rtklib/app/consapp/rnx2rtkp/gcc LDLIBS=-lm \
 && install -m 755 rtklib/app/consapp/rnx2rtkp/gcc/rnx2rtkp /usr/local/bin/rnx2rtkp
RUN curl -fsSL -o rnxcmp.tar.gz "https://terras.gsi.go.jp/ja/crx2rnx/RNXCMP_${RNXCMP_VERSION}_src.tar.gz" \
 && echo "$RNXCMP_SHA256  rnxcmp.tar.gz" | sha256sum -c - \
 && tar xzf rnxcmp.tar.gz \
 && gcc -O2 -o /usr/local/bin/crx2rnx "RNXCMP_${RNXCMP_VERSION}_src/source/crx2rnx.c"

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY --from=gnss /usr/local/bin/rnx2rtkp /usr/local/bin/crx2rnx /usr/local/bin/
COPY app ./app
COPY migrations ./migrations

RUN useradd --system --uid 10001 mapping && mkdir -p /cache && chown mapping /cache
USER mapping
EXPOSE 6790
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6790", "--proxy-headers"]
