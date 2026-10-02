"""AeroNexus mapping service: jobs, agent API and map layers (see docs/adr-001-architektur.md)."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mapping")

settings = config.load()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    applied = db.migrate(settings)
    log.info("database %s ready, %d migration(s) applied", settings.mysql_database, len(applied))
    yield


app = FastAPI(title="AeroNexus Mapping", version="0.1.0", lifespan=lifespan)

# Same policy as the DJI backend: the web UI runs on another port of the same host.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["x-auth-token", "content-type", "authorization"],
)


@app.get("/health")
def health() -> dict:
    with db.transaction(settings) as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [row["version"] for row in cur.fetchall()]
    return {"status": "ok", "migrations": versions}
