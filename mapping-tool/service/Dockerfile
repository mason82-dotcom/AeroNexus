# AeroNexus mapping service (FastAPI). Base image pinned by digest (multi-arch: Pi ARM64 and x64).
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY app ./app
COPY migrations ./migrations

RUN useradd --system --uid 10001 mapping
USER mapping
EXPOSE 6790
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6790", "--proxy-headers"]
