"""
main.py
-------
Punto de entrada del sistema de boletín automatizado.
Orquesta el pipeline completo y programa la ejecución automática
los martes y jueves a las 9:00 AM (hora configurable por env).

Control de concurrencia:
    Se usa un archivo .lock en rpa_boletin/ para garantizar que nunca
    haya dos instancias del pipeline ejecutándose al mismo tiempo.
    Si el proceso ya está corriendo y alguien lanza otro, este segundo
    termina inmediatamente con un mensaje en el log — sin errores,
    sin cobros duplicados a la API, sin doble envío de email.

Uso:
    python main.py             # Arranca el scheduler (modo producción)
    python main.py --run-now   # Ejecuta una vez inmediatamente (modo test)
    python main.py --preview   # Genera preview HTML sin enviar email
"""

import argparse
import logging
import sys

from boletin.config.logging import configure_logging
from boletin.config.runtime import RuntimeConfigurationError, load_runtime_settings
from boletin.config.settings import PROJECT_ROOT
from boletin.infrastructure.runtime_lock import single_process_lock
from boletin.runtime.bootstrap import bootstrap_database
from boletin.runtime.flaresolverr_process import bootstrap_flaresolverr
from boletin.services.scheduler_service import start_scheduler

logger = logging.getLogger("main")

LOCK_FILE = PROJECT_ROOT / ".lock"



def run_pipeline(preview: bool = False) -> None:
    """Ejecuta el pipeline completo protegido por lock de proceso único."""
    from boletin.services.pipeline_service import ejecutar_pipeline

    with single_process_lock(LOCK_FILE, logger=logger, preview=preview):
        ejecutar_pipeline(preview=preview, logger=logger)



def main() -> None:
    parser = argparse.ArgumentParser(description="Boletín Minero-Energético")
    parser.add_argument("--run-now", action="store_true", help="Ejecuta el pipeline una vez ahora")
    parser.add_argument("--preview", action="store_true", help="Genera preview.html sin enviar email")
    args = parser.parse_args()

    try:
        settings = load_runtime_settings()
    except RuntimeConfigurationError as exc:
        print(f"[CRÍTICO] Configuración inválida en .env: {exc}", flush=True)
        sys.exit(1)

    configure_logging(settings.project_root, logger_name="main")
    bootstrap_database(logger)
    bootstrap_flaresolverr(logger)

    if args.preview:
        run_pipeline(preview=True)
    elif args.run_now:
        run_pipeline(preview=False)
    else:
        start_scheduler(run_job=run_pipeline, settings=settings, logger=logger)


if __name__ == "__main__":
    main()
