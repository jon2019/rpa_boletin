"""Configuración central del proyecto.

Placeholder inicial para consolidar lectura de .env en siguientes refactors.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
TEMPLATES_DIR = PROJECT_ROOT / "src" / "boletin" / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"
FLARESOLVERR_HEARTBEAT_FILE = PROJECT_ROOT / "flaresolverr_heartbeat.json"
