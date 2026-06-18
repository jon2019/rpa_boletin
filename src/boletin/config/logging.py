"""Bootstrap de logging para el boletín."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging
from pathlib import Path
import sys


def build_log_file_path(project_root: Path) -> Path:
    """Construye la ruta del log diario usando la timezone local configurada en TIMEZONE."""
    tz_name = os.getenv("TIMEZONE", "America/Santiago")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("America/Santiago")
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"boletin_{datetime.now(tz=tz).strftime('%Y-%m-%d')}.log"



def configure_logging(project_root: Path, logger_name: str = "main") -> logging.Logger:
    """Configura logging global y devuelve el logger principal."""
    log_filename = build_log_file_path(project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(log_filename, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )

    logger = logging.getLogger(logger_name)
    logger.info("Log del día: %s", log_filename)
    return logger
