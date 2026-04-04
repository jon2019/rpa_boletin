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
    python main.py              # Arranca el scheduler (modo producción)
    python main.py --run-now   # Ejecuta una vez inmediatamente (modo test)
    python main.py --preview   # Genera preview HTML sin enviar email
"""

import argparse
import fcntl
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# Módulos del proyecto
import db
import retrier
import scraper
import scorer
import translator
import emailer

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
#
# Estructura en disco:
#
#   rpa_boletin/                   ← raíz del proyecto  (PROJECT_ROOT)
#   ├── .env                       ← credenciales (mismo nivel que .venv)
#   ├── .venv/                     ← entorno virtual Python
#   ├── .lock                      ← archivo de bloqueo (se crea/borra automáticamente)
#   ├── logs/                      ← logs diarios (un archivo por día)
#   │   ├── boletin_2026-04-01.log
#   │   ├── boletin_2026-04-03.log
#   │   └── boletin_2026-04-05.log
#   └── boletin/                   ← código fuente (aquí vive este archivo)
#       ├── main.py
#       └── ...
#
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # rpa_boletin/

# Carga .env desde rpa_boletin/ (un nivel arriba del código)
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
HORA_ENV = int(os.getenv("HORA_ENVIO", 9))

# Logs diarios — un archivo por día en rpa_boletin/logs/
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_filename = LOG_DIR / f"boletin_{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")
logger.info("Log del día: %s", log_filename)


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL DE CONCURRENCIA — PROCESO ÚNICO
# ══════════════════════════════════════════════════════════════════════════════

LOCK_FILE = PROJECT_ROOT / ".lock"


@contextmanager
def proceso_unico(preview: bool = False):
    """
    Context manager que garantiza una sola instancia del pipeline activa.

    Mecanismo:
        Usa fcntl.flock() sobre rpa_boletin/.lock — un bloqueo exclusivo
        a nivel de sistema operativo. Si el lock ya está tomado (otra
        instancia corriendo), falla inmediatamente (LOCK_NB = non-blocking).
        El lock se libera automáticamente al salir del bloque, incluso
        ante excepciones o señales del sistema.

    En modo --preview el lock NO se aplica para permitir
    revisiones visuales mientras el scheduler está activo.

    Raises:
        SystemExit: si ya hay una instancia corriendo.
    """
    if preview:
        # Preview no bloquea — es solo lectura/generación local
        yield
        return

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        logger.warning(
            "⚠️  PROCESO YA EN EJECUCIÓN — Se encontró un lock activo en %s. "
            "Esta instancia termina sin ejecutar nada.",
            LOCK_FILE,
        )
        sys.exit(0)   # Salida limpia, no es un error

    try:
        logger.info("🔒 Lock adquirido — %s", LOCK_FILE)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("🔓 Lock liberado")


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(preview: bool = False) -> None:
    """
    Ejecuta el pipeline completo protegido por el lock de proceso único.
    Si ya hay una instancia corriendo, termina inmediatamente sin hacer nada.

    Pasos:
      1. Adquiere el lock exclusivo
      2. Scraping de fuentes pendientes hoy
      3. Filtrado de historial (noticias ya enviadas)
      4. Scoring con Claude IA + selección por cuota de país
      5. Traducción al inglés con Claude
      6. Envío del boletín por SMTP (o generación de preview)
      7. Registro en DB + liberación del lock
    """
    with proceso_unico(preview=preview):
        # Resetea el colector de errores para esta ejecución
        retrier.reset_collector()

        inicio = datetime.now(tz=timezone.utc)
        logger.info("═" * 60)
        logger.info("INICIO DEL PIPELINE — %s", inicio.isoformat())
        logger.info("═" * 60)

        try:
            # 1. Scraping
            logger.info("PASO 1/5 — Scraping de fuentes pendientes hoy...")
            todas = scraper.fetch_all()
            if not todas:
                logger.warning("Sin noticias nuevas que procesar. Finalizando.")
                return

            # 2. Filtrar ya enviadas
            logger.info("PASO 2/5 — Filtrando historial...")
            nuevas = db.filtrar_enviadas(todas)
            if not nuevas:
                logger.warning("Todas las noticias ya fueron enviadas anteriormente.")
                return

            # 3. Scoring y selección
            logger.info("PASO 3/5 — Scoring con Claude IA...")
            top = scorer.puntuar_y_seleccionar(nuevas)
            if not top:
                logger.error("No hay noticias seleccionadas para el boletín.")
                return

            # 4. Traducción
            logger.info("PASO 4/5 — Traducción al inglés...")
            top = translator.traducir(top)

            # 5. Envío o preview
            if preview:
                logger.info("PASO 5/5 — Generando preview (sin envío)...")
                emailer.preview_html(top, output_path="preview.html")
                logger.info("Preview guardado: preview.html")
            else:
                logger.info("PASO 5/5 — Enviando boletín...")
                ok = emailer.enviar(top)

                if ok:
                    db.marcar_enviadas(top)
                    db.registrar_envio(top, ok=True)
                    logger.info("✅ Boletín enviado exitosamente (%d noticias)", len(top))
                else:
                    db.registrar_envio(top, ok=False)
                    logger.error("❌ Error al enviar el boletín")

        except Exception as e:
            logger.exception("Error crítico en el pipeline: %s", e)

        finally:
            duracion = (datetime.now(tz=timezone.utc) - inicio).total_seconds()
            logger.info("Pipeline finalizado en %.1f segundos", duracion)

            # Resumen de fuentes procesadas
            try:
                resumen = db.resumen_ejecucion_hoy()
                logger.info(
                    "Resumen hoy: %d fuentes | %d scraping OK | %d IA OK | "
                    "%d completas | %d noticias obtenidas",
                    resumen["total"] or 0,
                    resumen["scraping_ok"] or 0,
                    resumen["ia_ok"] or 0,
                    resumen["completas"] or 0,
                    resumen["noticias_obtenidas"] or 0,
                )
            except Exception:
                pass

            # Envía resumen de errores agrupados si los hubo
            collector = retrier.get_collector()
            if collector.hay_errores():
                logger.warning(
                    "Se acumularon %d errores durante el pipeline — enviando resumen...",
                    collector.total(),
                )
                collector.enviar_resumen()
            else:
                logger.info("Sin errores que reportar en este ciclo.")

            logger.info("═" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════

def start_scheduler() -> None:
    """
    Arranca el scheduler permanente.
    Dispara run_pipeline() los martes y jueves a las HORA_ENV:00.
    El lock dentro de run_pipeline() garantiza que si el cron
    se dispara mientras hay una ejecución manual activa, se omite.
    """
    tz = ZoneInfo(TIMEZONE)
    scheduler = BlockingScheduler(timezone=TIMEZONE)

    scheduler.add_job(
        func=run_pipeline,
        trigger=CronTrigger(
            day_of_week="tue,thu",
            hour=HORA_ENV,
            minute=0,
            timezone=tz,
        ),
        id="boletin_job",
        name="Boletín Minero-Energético",
        misfire_grace_time=3600,  # Tolera hasta 1h de servidor caído
        coalesce=True,            # No acumula ejecuciones perdidas
    )

    proxima = scheduler.get_jobs()[0].next_run_time
    logger.info("Scheduler iniciado — Próxima: %s", proxima)
    logger.info("Zona horaria: %s | Hora: %02d:00", TIMEZONE, HORA_ENV)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler detenido manualmente.")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Boletín Minero-Energético")
    parser.add_argument("--run-now", action="store_true",
                        help="Ejecuta el pipeline una vez ahora")
    parser.add_argument("--preview", action="store_true",
                        help="Genera preview.html sin enviar email")
    args = parser.parse_args()

    # Health check PostgreSQL
    if not db.test_conexion():
        logger.error("No se pudo conectar a PostgreSQL. Verifica DB_* en .env")
        sys.exit(1)
    db.init_db()

    if args.preview:
        run_pipeline(preview=True)
    elif args.run_now:
        run_pipeline(preview=False)
    else:
        start_scheduler()


if __name__ == "__main__":
    main()
