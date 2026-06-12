"""Conexión y configuración base de PostgreSQL."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import logging
import os

import psycopg2
from boletin.config.environment import load_project_env

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_db_config() -> dict[str, object]:
    """Carga y cachea la configuración de PostgreSQL desde `.env`."""
    load_project_env()
    return {
        "host": os.environ["DB_HOST"],
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 10,
        "options": "-c client_encoding=UTF8",
    }


@contextmanager
def get_connection():
    conn = psycopg2.connect(**get_db_config())
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def test_conexion() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
        logger.info("Conexión PostgreSQL OK — %s", version)
        return True
    except Exception as exc:
        logger.error("Error de conexión PostgreSQL: %s", exc)
        return False
