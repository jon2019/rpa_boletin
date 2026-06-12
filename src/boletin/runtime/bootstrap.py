"""Bootstrap de infraestructura para el entrypoint del boletín."""

from __future__ import annotations

import logging
import sys



def bootstrap_database(logger: logging.Logger) -> None:
    """Valida conexión e inicializa schema de base de datos."""
    from boletin.infrastructure.db import facade as db

    if not db.test_conexion():
        logger.error("No se pudo conectar a PostgreSQL. Verifica DB_* en .env")
        sys.exit(1)

    db.init_db()
