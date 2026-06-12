"""Fachada de compatibilidad para acceso a base de datos.

Este módulo mantiene la API histórica que usa el resto del sistema,
pero delega la implementación real a repositorios especializados bajo
`boletin.infrastructure.db`.
"""

from __future__ import annotations

import logging
from datetime import date

from boletin.infrastructure.db.connection import (
    get_connection as _infra_get_connection,
    test_conexion as _infra_test_conexion,
)
from boletin.infrastructure.db.execution_repository import (
    hoy_utc as _execution_hoy_utc,
    fuentes_pendientes_hoy as _repo_fuentes_pendientes_hoy,
    registrar_scraping as _repo_registrar_scraping,
    registrar_ia as _repo_registrar_ia,
    resumen_ejecucion_hoy as _repo_resumen_ejecucion_hoy,
    fecha_fue_procesada_completamente as _repo_fecha_fue_procesada_completamente,
    debe_ejecutar_proceso as _repo_debe_ejecutar_proceso,
    registrar_ejecucion_proceso as _repo_registrar_ejecucion_proceso,
    limpiar_antiguos as _repo_limpiar_antiguos,
)
from boletin.infrastructure.db.pending_articles_repository import (
    guardar_articulos_pendientes as _repo_guardar_articulos_pendientes,
    marcar_articulos_ia_procesada as _repo_marcar_articulos_ia_procesada,
    get_articulos_pendientes as _repo_get_articulos_pendientes,
    reconciliar_pendientes_con_enviadas as _repo_reconciliar_pendientes_con_enviadas,
    limpiar_articulos_pendientes as _repo_limpiar_articulos_pendientes,
    get_fuentes_solo_ia as _repo_get_fuentes_solo_ia,
)
from boletin.infrastructure.db.schema_repository import (
    init_db as _repo_init_db,
    seed_if_empty as _repo_seed_if_empty,
)
from boletin.infrastructure.db.ia_scoring_log_repository import (
    registrar_ia_scoring_log as _repo_registrar_ia_scoring_log,
    get_ia_scoring_log as _repo_get_ia_scoring_log,
)
from boletin.infrastructure.db.scraping_log_repository import (
    registrar_scraping_log as _repo_registrar_scraping_log,
    actualizar_ponderacion_scraping_log as _repo_actualizar_ponderacion_scraping_log,
    get_scraping_log as _repo_get_scraping_log,
)
from boletin.infrastructure.db.sent_news_repository import (
    hash_url as _repo_hash_url,
    filtrar_enviadas as _repo_filtrar_enviadas,
    marcar_enviadas as _repo_marcar_enviadas,
    registrar_envio as _repo_registrar_envio,
)
from boletin.infrastructure.db.sources_repository import (
    get_fuentes_activas as _repo_get_fuentes_activas,
    get_paises_activos as _repo_get_paises_activos,
    get_score_config as _repo_get_score_config,
    get_fuentes_sin_selector as _repo_get_fuentes_sin_selector,
    get_fuentes_omitidas_reglas_negocio as _repo_get_fuentes_omitidas_reglas_negocio,
    get_fuentes_con_problemas_operativos as _repo_get_fuentes_con_problemas_operativos,
    actualizar_feed_fuente as _repo_actualizar_feed_fuente,
    actualizar_selector_fuente as _repo_actualizar_selector_fuente,
)

logger = logging.getLogger(__name__)


def get_connection():
    return _infra_get_connection()


def init_db() -> None:
    _repo_init_db()


def _seed_if_empty() -> None:
    _repo_seed_if_empty()


def guardar_articulos_pendientes(url_fuente: str, articulos: list[dict], fecha: date) -> None:
    _repo_guardar_articulos_pendientes(url_fuente, articulos, fecha)


def marcar_articulos_ia_procesada(url_fuente: str, fecha: date) -> None:
    _repo_marcar_articulos_ia_procesada(url_fuente, fecha)


def get_articulos_pendientes(url_fuente: str, fecha: date) -> list[dict]:
    return _repo_get_articulos_pendientes(url_fuente, fecha)


def reconciliar_pendientes_con_enviadas(url_fuente: str, fecha: date) -> dict:
    return _repo_reconciliar_pendientes_con_enviadas(url_fuente, fecha)


def limpiar_articulos_pendientes(fecha: date) -> None:
    _repo_limpiar_articulos_pendientes(fecha)


def get_fuentes_solo_ia(todas_fuentes: list[dict], fecha: date) -> tuple[list[dict], list[dict]]:
    return _repo_get_fuentes_solo_ia(todas_fuentes, fecha)


def get_fuentes_sin_selector() -> list[dict]:
    return _repo_get_fuentes_sin_selector()


def get_fuentes_omitidas_reglas_negocio() -> list[dict]:
    return _repo_get_fuentes_omitidas_reglas_negocio()


def get_fuentes_con_problemas_operativos() -> list[dict]:
    return _repo_get_fuentes_con_problemas_operativos()


def obtener_fuentes_pendientes(fecha: date) -> list[dict]:
    """Devuelve las fuentes activas que aún no completaron scraping + IA."""
    fuentes = get_fuentes_activas()
    return fuentes_pendientes_hoy(fuentes, fecha)


def get_fuentes_activas() -> list[dict]:
    return _repo_get_fuentes_activas()


def get_paises_activos() -> list[dict]:
    return _repo_get_paises_activos()


def get_score_config() -> dict:
    return _repo_get_score_config()


def _hoy() -> date:
    return _execution_hoy_utc()


def fuentes_pendientes_hoy(fuentes: list[dict], fecha: date | None = None) -> list[dict]:
    return _repo_fuentes_pendientes_hoy(fuentes, fecha)


def registrar_scraping(
    url_fuente: str,
    nombre: str,
    ok: bool,
    noticias_obtenidas: int = 0,
    error: str | None = None,
    fecha: date | None = None,
) -> None:
    _repo_registrar_scraping(url_fuente, nombre, ok, noticias_obtenidas, error, fecha)


def registrar_ia(
    url_fuente: str,
    ok: bool,
    noticias_enviadas: int = 0,
    error: str | None = None,
    fecha: date | None = None,
) -> None:
    _repo_registrar_ia(url_fuente, ok, noticias_enviadas, error, fecha)


def resumen_ejecucion_hoy(fecha: date | None = None) -> dict:
    return _repo_resumen_ejecucion_hoy(fecha)


def fecha_fue_procesada_completamente(fecha: date) -> bool:
    return _repo_fecha_fue_procesada_completamente(fecha)


def _hash_url(url: str) -> str:
    return _repo_hash_url(url)


def filtrar_enviadas(noticias: list[dict]) -> list[dict]:
    return _repo_filtrar_enviadas(noticias)


def marcar_enviadas(noticias: list[dict]) -> None:
    _repo_marcar_enviadas(noticias)


def registrar_envio(noticias: list[dict], ok: bool = True, fecha: date | None = None) -> None:
    _repo_registrar_envio(noticias, ok, fecha)


def debe_ejecutar_proceso(nombre: str, cada_dias: int) -> bool:
    return _repo_debe_ejecutar_proceso(nombre, cada_dias)


def registrar_ejecucion_proceso(nombre: str, ok: bool, detalle: dict | None = None) -> None:
    _repo_registrar_ejecucion_proceso(nombre, ok, detalle)


def actualizar_feed_fuente(fuente_id: int, url_rss: str | None, metodo: str | None) -> None:
    _repo_actualizar_feed_fuente(fuente_id, url_rss, metodo)


def actualizar_selector_fuente(
    fuente_id: int,
    scrape_selector: str | None,
    nota: str | None = None,
) -> None:
    _repo_actualizar_selector_fuente(fuente_id, scrape_selector, nota)


def limpiar_antiguos(dias: int = 60) -> None:
    _repo_limpiar_antiguos(dias)


def registrar_ia_scoring_log(
    candidatas: list[dict],
    fecha: date | None = None,
    urls_boletin: set[str] | None = None,
) -> None:
    _repo_registrar_ia_scoring_log(candidatas, fecha, urls_boletin)


def get_ia_scoring_log(fecha: date, pais: str | None = None) -> list[dict]:
    return _repo_get_ia_scoring_log(fecha, pais)


def registrar_scraping_log(
    url_fuente: str,
    fecha,
    ok: bool,
    noticias: list[dict],
    error: str | None = None,
) -> int | None:
    return _repo_registrar_scraping_log(url_fuente, fecha, ok, noticias, error)


def actualizar_ponderacion_scraping_log(noticias: list[dict], fecha) -> None:
    _repo_actualizar_ponderacion_scraping_log(noticias, fecha)


def get_scraping_log(url_fuente: str, fecha) -> list[dict]:
    return _repo_get_scraping_log(url_fuente, fecha)


def test_conexion() -> bool:
    return _infra_test_conexion()
