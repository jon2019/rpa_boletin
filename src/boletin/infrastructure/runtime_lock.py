"""Primitivas de bloqueo de proceso para el runtime del boletín."""

from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
import sys

import portalocker


@contextmanager
def single_process_lock(lock_file: Path, logger: logging.Logger, preview: bool = False):
    """Garantiza una sola instancia activa del pipeline usando un archivo lock."""
    if preview:
        yield
        return

    lock = portalocker.Lock(lock_file, "w", timeout=0)
    try:
        lock.acquire()
    except portalocker.LockException:
        logger.warning(
            "PROCESO YA EN EJECUCION - Se encontró un lock activo en %s. "
            "Esta instancia termina sin ejecutar nada.",
            lock_file,
        )
        sys.exit(0)

    try:
        logger.info("Lock adquirido - %s", lock_file)
        yield
    finally:
        lock.release()
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("Lock liberado")
