"""Reglas de calendario operativo para la fecha efectiva del pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Optional

from boletin.infrastructure.db import facade as db


def calcular_fecha_efectiva(
    hoy: datetime,
    logger: logging.Logger,
    permitir_reprocesada: bool = False,
) -> Optional[datetime.date]:
    """
    Calcula la fecha efectiva para el checkpoint de fuentes consultando la DB.
    """
    weekday = hoy.weekday()
    fecha_actual = hoy.date()

    if weekday == 1:  # martes
        fecha_efectiva = fecha_actual
    elif weekday == 3:  # jueves
        fecha_efectiva = fecha_actual
    elif weekday == 2:  # miércoles -> compensa martes
        fecha_efectiva = (hoy - timedelta(days=1)).date()
    else:
        dias_atras = (weekday - 3) % 7
        if dias_atras == 0:
            dias_atras = 7
        fecha_efectiva = (hoy - timedelta(days=dias_atras)).date()

    if db.fecha_fue_procesada_completamente(fecha_efectiva):
        if permitir_reprocesada:
            logger.info(
                "Fecha efectiva %s ya fue procesada completamente, pero preview fuerza la ejecución.",
                fecha_efectiva,
            )
            return fecha_efectiva
        logger.info(
            "Fecha efectiva %s ya fue procesada completamente. No se ejecuta.",
            fecha_efectiva,
        )
        return None

    logger.info(
        "Fecha efectiva calculada: %s (día actual: %s)",
        fecha_efectiva,
        fecha_actual,
    )
    return fecha_efectiva
