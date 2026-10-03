"""AeroNexus mapping service: jobs, agent API and map layers (see docs/adr-001-architektur.md)."""
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, mediaauth, missions
from .api import build_router
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mapping")


class _RedactToken(logging.Filter):
    """Tile URLs carry the login JWT as ?token=...; keep it out of the access log."""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r"token=[^&\s]+", "token=***", a) if isinstance(a, str) else a
                                for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactToken())

settings = config.load()
storage = Storage(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    applied = db.migrate(settings)
    log.info("database %s ready, %d migration(s) applied", settings.mysql_database, len(applied))
    yield


app = FastAPI(title="AeroNexus Mapping", version="0.1.0", lifespan=lifespan)

# Only the web UI (another port of the same host) may call the API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["x-auth-token", "content-type", "authorization"],
)


app.include_router(build_router(settings, storage))
app.include_router(mediaauth.build_router(settings))
app.include_router(missions.build_router(settings))


@app.get("/health")
def health() -> dict:
    with db.transaction(settings) as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [row["version"] for row in cur.fetchall()]
    return {"status": "ok", "migrations": versions}
