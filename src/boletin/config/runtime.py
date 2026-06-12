"""Configuración runtime del entrypoint del boletín."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from boletin.config.environment import load_project_env
from boletin.config.settings import PROJECT_ROOT


class RuntimeConfigurationError(ValueError):
    """Error de configuración inválida para el arranque del sistema."""


@dataclass(frozen=True)
class RuntimeSettings:
    project_root: Path
    timezone: str
    hora_envio: int


def load_runtime_settings() -> RuntimeSettings:
    """Carga y valida la configuración runtime desde `.env`."""
    load_project_env()

    timezone = os.getenv("TIMEZONE", "America/Santiago")

    raw_hora_envio = os.getenv("HORA_ENVIO", "9")
    try:
        hora_envio = int(raw_hora_envio)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            f"HORA_ENVIO={raw_hora_envio} no es un entero válido"
        ) from exc

    if not 0 <= hora_envio <= 23:
        raise RuntimeConfigurationError(
            f"HORA_ENVIO={hora_envio} fuera de rango (debe ser 0-23)"
        )

    return RuntimeSettings(
        project_root=PROJECT_ROOT,
        timezone=timezone,
        hora_envio=hora_envio,
    )
