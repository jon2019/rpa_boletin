"""Orquestación del scheduler del boletín."""

from __future__ import annotations

import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from boletin.config.runtime import RuntimeSettings



def start_scheduler(
    run_job: Callable[[], None],
    settings: RuntimeSettings,
    logger: logging.Logger,
) -> None:
    """Arranca el scheduler permanente del boletín."""
    tz = ZoneInfo(settings.timezone)
    scheduler = BlockingScheduler(timezone=settings.timezone)

    scheduler.add_job(
        func=run_job,
        trigger=CronTrigger(
            hour=settings.hora_envio,
            minute=0,
            timezone=tz,
        ),
        id="boletin_job",
        name="Boletín Minero-Energético",
        misfire_grace_time=3600,
        coalesce=True,
    )

    proxima = scheduler.get_jobs()[0].next_run_time
    logger.info("Scheduler iniciado — Próxima: %s", proxima)
    logger.info("Zona horaria: %s | Hora: %02d:00", settings.timezone, settings.hora_envio)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler detenido manualmente.")
