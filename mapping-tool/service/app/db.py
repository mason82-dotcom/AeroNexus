"""MySQL access and a minimal migration runner (numbered SQL files in ../migrations)."""
import logging
import re
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings

log = logging.getLogger("mapping.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def connect(settings: Settings, database: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


@contextmanager
def transaction(settings: Settings):
    conn = connect(settings, settings.mysql_database)
    try:
        with conn.cursor() as cur:
            cur.execute("SET time_zone = '+00:00'")
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _split_statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def migrate(settings: Settings) -> list[str]:
    """Create the database if needed and apply pending migrations in order. Returns applied versions."""
    conn = connect(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        conn.commit()
    finally:
        conn.close()

    applied: list[str] = []
    conn = connect(settings, settings.mysql_database)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version CHAR(4) NOT NULL PRIMARY KEY,"
                " name VARCHAR(200) NOT NULL,"
                " applied_at DATETIME(3) NOT NULL DEFAULT (UTC_TIMESTAMP(3)))"
            )
            cur.execute("SELECT version FROM schema_migrations")
            done = {row["version"] for row in cur.fetchall()}
        conn.commit()

        for path in sorted(MIGRATIONS_DIR.iterdir()):
            match = _MIGRATION_RE.match(path.name)
            if not match or match.group(1) in done:
                continue
            # MySQL DDL is not transactional: a failing migration must be fixed by hand.
            with conn.cursor() as cur:
                for stmt in _split_statements(path.read_text(encoding="utf-8")):
                    cur.execute(stmt)
                cur.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                    (match.group(1), path.name),
                )
            conn.commit()
            applied.append(path.name)
            log.info("applied migration %s", path.name)
    finally:
        conn.close()
    return applied
