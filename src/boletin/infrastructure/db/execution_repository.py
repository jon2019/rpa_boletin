"""Estado operativo, checkpoints y procesos programados."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import psycopg2.extras

from boletin.infrastructure.resilience import retrier
from boletin.infrastructure.db.connection import get_connection
from boletin.infrastructure.db.sources_repository import (
    get_fuentes_activas,
    get_fuentes_con_problemas_operativos,
    get_fuentes_omitidas_reglas_negocio,
)

logger = logging.getLogger(__name__)


def hoy_utc() -> date:
    return datetime.now(tz=timezone.utc).date()


def fuentes_pendientes_hoy(fuentes: list[dict], fecha: date | None = None) -> list[dict]:
    """
    Retorna solo las fuentes que todavía no completaron scraping+IA exitosamente
    para la fecha indicada.
    """
    fecha_check = fecha or hoy_utc()
    urls = [f["url"] for f in fuentes]

    if not urls:
        return []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT url_fuente FROM ejecucion_fuentes
                WHERE fecha_ejecucion = %s
                  AND scraping_ok = TRUE
                  AND ia_ok = TRUE
                  AND url_fuente = ANY(%s)
                """,
                (fecha_check, urls),
            )
            completadas = {row[0] for row in cur.fetchall()}

    pendientes = [f for f in fuentes if f["url"] not in completadas]
    logger.info(
        "Fuentes [%s]: %d total | %d completadas | %d pendientes",
        fecha_check, len(fuentes), len(completadas), len(pendientes)
    )
    return pendientes


def registrar_scraping(
    url_fuente: str,
    nombre: str,
    ok: bool,
    noticias_obtenidas: int = 0,
    error: str | None = None,
    fecha: date | None = None,
) -> None:
    fecha_reg = fecha or hoy_utc()
    ahora = datetime.now(tz=timezone.utc)

    def _upsert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ejecucion_fuentes
                        (url_fuente, nombre_fuente, fecha_ejecucion,
                         scraping_ok, noticias_obtenidas, error_detalle, actualizado_en)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (url_fuente, fecha_ejecucion) DO UPDATE SET
                        scraping_ok        = (EXCLUDED.scraping_ok OR ejecucion_fuentes.scraping_ok),
                        noticias_obtenidas = GREATEST(EXCLUDED.noticias_obtenidas, ejecucion_fuentes.noticias_obtenidas),
                        error_detalle      = CASE
                                                WHEN ejecucion_fuentes.scraping_ok THEN NULL
                                                WHEN EXCLUDED.scraping_ok         THEN NULL
                                                ELSE EXCLUDED.error_detalle
                                            END,
                        actualizado_en     = EXCLUDED.actualizado_en
                    """,
                    (url_fuente, nombre, fecha_reg, ok, noticias_obtenidas, error[:500] if error else None, ahora),
                )

    _, db_ok, _ = retrier.con_reintentos(
        fn=_upsert,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=nombre,
        url=url_fuente,
        registrar_en_collector=False,
    )
    estado = "OK" if ok else "FALLO"
    if db_ok:
        logger.info("Scraping [%s] %s — %d noticias", nombre, estado, noticias_obtenidas)
    else:
        logger.error("No se pudo registrar checkpoint de scraping para %s", nombre)


def registrar_ia(
    url_fuente: str,
    ok: bool,
    noticias_enviadas: int = 0,
    error: str | None = None,
    fecha: date | None = None,
) -> None:
    fecha_reg = fecha or hoy_utc()
    ahora = datetime.now(tz=timezone.utc)

    def _update():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ejecucion_fuentes SET
                        ia_ok             = %s,
                        noticias_enviadas = %s,
                        error_detalle     = CASE WHEN %s THEN NULL ELSE %s END,
                        actualizado_en    = %s
                    WHERE url_fuente = %s AND fecha_ejecucion = %s
                    """,
                    (ok, noticias_enviadas, ok, error[:500] if error else None, ahora, url_fuente, fecha_reg),
                )

    _, db_ok, _ = retrier.con_reintentos(
        fn=_update,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=url_fuente,
        url=url_fuente,
        registrar_en_collector=False,
    )
    if not db_ok:
        logger.error("No se pudo registrar resultado IA para %s en DB", url_fuente)


def resumen_ejecucion_hoy(fecha: date | None = None) -> dict:
    fecha_check = fecha or hoy_utc()

    def _limpiar_texto(valor: str | None) -> str:
        return (valor or "").strip()

    def _motivo_sin_candidatas(row: dict) -> str:
        problema_operativo = _limpiar_texto(row.get("problema_operativo"))
        if problema_operativo:
            return problema_operativo

        error_detalle = _limpiar_texto(row.get("error_detalle"))
        scraping_ok = row.get("scraping_ok")
        ia_ok = row.get("ia_ok")
        noticias_obtenidas = int(row.get("noticias_obtenidas") or 0)
        noticias_candidatas = int(row.get("noticias_candidatas") or 0)

        if noticias_candidatas > 0:
            return ""
        if scraping_ok is None:
            return "Sin ejecución registrada para la fecha efectiva."
        if scraping_ok is False:
            return error_detalle or "No se encontraron noticias en la fuente."
        if noticias_obtenidas == 0:
            return error_detalle or "No se encontraron noticias candidatas en el scraping."
        if ia_ok is False and error_detalle:
            return error_detalle
        if noticias_obtenidas > 0 and noticias_candidatas == 0:
            return "No superaron los filtros de scoring/IA para integrar el resumen."
        return error_detalle

    def _resumen_noticias_encontradas(row: dict) -> str:
        scraping_ok = row.get("scraping_ok")
        noticias_obtenidas = int(row.get("noticias_obtenidas") or 0)
        noticias_candidatas = int(row.get("noticias_candidatas") or 0)

        if scraping_ok is None:
            return "Sin ejecución registrada en la fecha efectiva."
        if noticias_obtenidas > 0 and noticias_candidatas > 0:
            return f"Se encontraron {noticias_obtenidas} noticias; {noticias_candidatas} quedaron como candidatas."
        if noticias_obtenidas > 0:
            return f"Se encontraron {noticias_obtenidas} noticias, pero ninguna quedó como candidata."
        if scraping_ok is False:
            return "La fuente no devolvió noticias por error o falta de resultados."
        return "No se encontraron noticias en la fuente."

    problemas_operativos = get_fuentes_con_problemas_operativos()
    problemas_por_url = {
        (p.get("url") or "").strip(): (p.get("problema") or "").strip()
        for p in problemas_operativos
    }

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total_fuentes_activas
                FROM fuentes
                WHERE activa = TRUE
                """
            )
            total_fuentes_activas = cur.fetchone()["total_fuentes_activas"]

            cur.execute(
                """
                SELECT
                    COUNT(*)                                         AS total,
                    SUM(CASE WHEN scraping_ok THEN 1 ELSE 0 END)    AS scraping_ok,
                    SUM(CASE WHEN ia_ok       THEN 1 ELSE 0 END)    AS ia_ok,
                    SUM(CASE WHEN scraping_ok AND ia_ok THEN 1 ELSE 0 END) AS completas,
                    SUM(noticias_obtenidas)                          AS noticias_obtenidas,
                    SUM(noticias_enviadas)                           AS noticias_al_boletin
                FROM ejecucion_fuentes WHERE fecha_ejecucion = %s
                """,
                (fecha_check,),
            )
            resumen = dict(cur.fetchone())
            resumen["total_fuentes_activas"] = total_fuentes_activas

            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE scraping_ok = TRUE AND COALESCE(noticias_obtenidas, 0) > 0
                    ) AS links_con_noticias,
                    COUNT(*) FILTER (
                        WHERE scraping_ok = FALSE
                          AND COALESCE(noticias_obtenidas, 0) = 0
                          AND (
                                error_detalle IS NULL
                                OR TRIM(error_detalle) = ''
                                OR LOWER(error_detalle) LIKE '%%sin noticias%%'
                                OR LOWER(error_detalle) LIKE '%%no encontr%%'
                                OR LOWER(error_detalle) LIKE '%%no devolvi%%'
                                OR LOWER(error_detalle) LIKE '%%no obtuvo noticias%%'
                              )
                    ) AS links_sin_noticias,
                    COUNT(*) FILTER (
                        WHERE scraping_ok = FALSE
                          AND NOT (
                                COALESCE(noticias_obtenidas, 0) = 0
                                AND (
                                    error_detalle IS NULL
                                    OR TRIM(error_detalle) = ''
                                    OR LOWER(error_detalle) LIKE '%%sin noticias%%'
                                    OR LOWER(error_detalle) LIKE '%%no encontr%%'
                                    OR LOWER(error_detalle) LIKE '%%no devolvi%%'
                                    OR LOWER(error_detalle) LIKE '%%no obtuvo noticias%%'
                                )
                          )
                    ) AS links_con_errores
                FROM ejecucion_fuentes
                WHERE fecha_ejecucion = %s
                """,
                (fecha_check,),
            )
            detalle_links = dict(cur.fetchone())
            resumen.update(detalle_links)

            cur.execute(
                """
                SELECT nombre_fuente, error_detalle
                FROM ejecucion_fuentes 
                WHERE fecha_ejecucion = %s AND scraping_ok = TRUE AND ia_ok = FALSE
                ORDER BY nombre_fuente
                """,
                (fecha_check,),
            )
            resumen["fuentes_ia_fallidas"] = [
                {
                    "nombre": row.get("nombre_fuente"),
                    "error": row.get("error_detalle") or "Error desconocido",
                }
                for row in cur.fetchall()
            ]

            omitidas = get_fuentes_omitidas_reglas_negocio()
            resumen["fuentes_omitidas_reglas"] = omitidas
            resumen["fuentes_omitidas_reglas_total"] = len(omitidas)

            resumen["fuentes_problemas_operativos"] = problemas_operativos
            resumen["fuentes_problemas_operativos_total"] = len(problemas_operativos)

            cur.execute(
                """
                SELECT
                    f.nombre AS nombre_fuente,
                    f.url AS url_fuente,
                    f.pais AS pais,
                    ef.scraping_ok,
                    ef.ia_ok,
                    COALESCE(ef.noticias_obtenidas, 0) AS noticias_obtenidas,
                    COALESCE(ef.noticias_enviadas, 0) AS noticias_candidatas,
                    ef.error_detalle
                FROM fuentes f
                LEFT JOIN ejecucion_fuentes ef
                    ON ef.url_fuente = f.url
                   AND ef.fecha_ejecucion = %s
                WHERE f.activa = TRUE
                ORDER BY f.pais, f.nombre
                """,
                (fecha_check,),
            )
            detalle_fuentes = [dict(row) for row in cur.fetchall()]

            for row in detalle_fuentes:
                row["problema_operativo"] = problemas_por_url.get((row.get("url_fuente") or "").strip(), "")
                row["resumen_noticias"] = _resumen_noticias_encontradas(row)
                row["motivo_sin_candidatas"] = _motivo_sin_candidatas(row)

            resumen["detalle_fuentes"] = detalle_fuentes

            cur.execute(
                """
                SELECT
                    f.pais AS pais,
                    f.nombre AS nombre_fuente,
                    f.url AS url_fuente,
                    ef.scraping_ok,
                    ef.ia_ok,
                    ef.error_detalle
                FROM ejecucion_fuentes ef
                INNER JOIN fuentes f
                    ON f.url = ef.url_fuente
                WHERE f.activa = TRUE
                  AND ef.fecha_ejecucion = %s
                  AND (
                        ef.scraping_ok = FALSE
                        OR (ef.scraping_ok = TRUE AND COALESCE(ef.ia_ok, FALSE) = FALSE)
                      )
                  AND (
                        ef.error_detalle IS NOT NULL
                        AND TRIM(ef.error_detalle) <> ''
                      )
                  AND NOT (
                        LOWER(ef.error_detalle) LIKE '%%sin noticias%%'
                        OR LOWER(ef.error_detalle) LIKE '%%no encontr%%'
                        OR LOWER(ef.error_detalle) LIKE '%%no devolvi%%'
                        OR LOWER(ef.error_detalle) LIKE '%%no obtuvo noticias%%'
                      )
                ORDER BY f.pais, f.nombre
                """,
                (fecha_check,),
            )
            errores_ejecucion = [dict(row) for row in cur.fetchall()]

            for row in errores_ejecucion:
                if row.get("scraping_ok") is False:
                    row["etapa_fallida"] = "Scraping"
                elif row.get("scraping_ok") is True and row.get("ia_ok") is False:
                    row["etapa_fallida"] = "IA"
                else:
                    row["etapa_fallida"] = "Desconocida"

            resumen["fuentes_con_error_ejecucion"] = errores_ejecucion
            resumen["fuentes_con_error_ejecucion_total"] = len(errores_ejecucion)

            resumen["nota_reglas_negocio"] = [
                "Si una fuente tiene usuario y clave, se procesa por login y no por RSS.",
                "Si una fuente tiene url_rss válida, se procesa por RSS.",
                "Si no tiene RSS pero tiene scrape_selector, se procesa por scraping HTML.",
                "Si no tiene rss, scrape_selector ni credenciales, queda omitida del ciclo operativo actual.",
            ]

    return resumen


def fecha_fue_procesada_completamente(fecha: date) -> bool:
    """
    Una fecha se considera procesada si todas las fuentes activas tienen
    scraping_ok = TRUE y ia_ok = TRUE.
    """
    fuentes_activas = get_fuentes_activas()
    if not fuentes_activas:
        return False

    urls_activas = [f["url"] for f in fuentes_activas]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM ejecucion_fuentes
                WHERE fecha_ejecucion = %s
                  AND scraping_ok = TRUE
                  AND ia_ok = TRUE
                  AND url_fuente = ANY(%s)
                """,
                (fecha, urls_activas),
            )
            completadas = cur.fetchone()[0]

    return completadas == len(urls_activas)


def debe_ejecutar_proceso(nombre: str, cada_dias: int) -> bool:
    """
    Retorna True si el proceso no tiene una ejecución exitosa reciente dentro
    de la ventana configurada.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ultima_ejecucion, ultimo_estado_ok
                FROM procesos_programados
                WHERE nombre = %s
                """,
                (nombre,),
            )
            row = cur.fetchone()

    if not row:
        return True

    ultima_ejecucion, ultimo_estado_ok = row
    if not ultima_ejecucion or not ultimo_estado_ok:
        return True

    limite = datetime.now(tz=timezone.utc) - timedelta(days=cada_dias)
    return ultima_ejecucion <= limite


def registrar_ejecucion_proceso(nombre: str, ok: bool, detalle: dict | None = None) -> None:
    detalle_json = json.dumps(detalle or {}, ensure_ascii=False)
    ahora = datetime.now(tz=timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO procesos_programados
                    (nombre, ultima_ejecucion, ultimo_estado_ok, detalle, actualizado_en)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (nombre) DO UPDATE SET
                    ultima_ejecucion = EXCLUDED.ultima_ejecucion,
                    ultimo_estado_ok = EXCLUDED.ultimo_estado_ok,
                    detalle = EXCLUDED.detalle,
                    actualizado_en = EXCLUDED.actualizado_en
                """,
                (nombre, ahora, ok, detalle_json, ahora),
            )


def limpiar_antiguos(dias: int = 60) -> None:
    limite = datetime.now(tz=timezone.utc) - timedelta(days=dias)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM noticias_enviadas WHERE enviado_en < %s", (limite,))
            n1 = cur.rowcount
            cur.execute("DELETE FROM ejecucion_fuentes WHERE creado_en < %s", (limite,))
            n2 = cur.rowcount
    logger.info("Limpieza DB: %d noticias + %d checkpoints eliminados", n1, n2)
