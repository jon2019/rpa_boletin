"""Persistencia temporal de artículos pendientes y retry solo-IA."""

from __future__ import annotations

import logging
from datetime import date

import psycopg2.extras

from boletin.infrastructure.resilience import retrier
from boletin.infrastructure.db.connection import get_connection

logger = logging.getLogger(__name__)


def guardar_articulos_pendientes(url_fuente: str, articulos: list[dict], fecha: date) -> None:
    """
    Guarda artículos scrapeados para reintentar el paso de IA sin re-scrapear.
    """
    if not articulos:
        return

    rows = [
        (
            url_fuente,
            fecha,
            n.get("titulo", ""),
            n.get("url", ""),
            n.get("resumen", ""),
            n.get("fecha", ""),
            n.get("fuente", ""),
            n.get("pais", ""),
        )
        for n in articulos
        if n.get("url")
    ]
    if not rows:
        return

    def _insert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO articulos_pendientes
                        (url_fuente, fecha_ef, titulo, url, resumen, fecha, fuente, pais)
                    VALUES %s
                    ON CONFLICT (url, fecha_ef) DO NOTHING
                    """,
                    rows,
                )

    _, ok, err = retrier.con_reintentos(
        fn=_insert,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=url_fuente,
        url=url_fuente,
        registrar_en_collector=False,
    )
    if ok:
        logger.info("Artículos pendientes guardados para IA-retry [%s]: %d", url_fuente, len(rows))
    else:
        logger.error("No se pudo guardar artículos pendientes para [%s]: %s", url_fuente, err)


def marcar_articulos_ia_procesada(url_fuente: str, fecha: date) -> None:
    """
    Marca como procesados por IA todos los artículos de una fuente/fecha.
    """

    def _update():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE articulos_pendientes
                    SET ia_procesada = TRUE
                    WHERE url_fuente = %s AND fecha_ef = %s
                    """,
                    (url_fuente, fecha),
                )

    _, ok, err = retrier.con_reintentos(
        fn=_update,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=url_fuente,
        url=url_fuente,
        registrar_en_collector=False,
    )
    if ok:
        logger.info("Artículos marcados ia_procesada=TRUE [%s] fecha %s", url_fuente, fecha)
    else:
        logger.error(
            "CRÍTICO: No se pudo marcar ia_procesada=TRUE para [%s] fecha %s: %s — "
            "riesgo de doble cobro IA si la tabla no se limpia",
            url_fuente, fecha, err,
        )


def get_articulos_pendientes(url_fuente: str, fecha: date) -> list[dict]:
    """
    Recupera artículos pendientes de una fuente/fecha que aún no fueron procesados por IA.
    """

    def _select() -> list[dict]:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT titulo, url, url_fuente, resumen, fecha, fuente, pais
                    FROM articulos_pendientes
                    WHERE url_fuente = %s AND fecha_ef = %s AND ia_procesada = FALSE
                    """,
                    (url_fuente, fecha),
                )
                rows = cur.fetchall()

        articulos = []
        for r in rows:
            art = dict(r)
            art["score"] = 0
            art["titulo_en"] = ""
            art["resumen_en"] = ""
            articulos.append(art)
        return articulos

    resultado, ok, err = retrier.con_reintentos(
        fn=_select,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=url_fuente,
        url=url_fuente,
        registrar_en_collector=False,
    )
    if ok:
        articulos = resultado or []
        logger.info("Artículos pendientes recuperados para IA-retry [%s]: %d", url_fuente, len(articulos))
        return articulos

    logger.error(
        "No se pudieron recuperar artículos pendientes para [%s]: %s — fuente omitida en modo solo-IA",
        url_fuente, err,
    )
    return []


def reconciliar_pendientes_con_enviadas(url_fuente: str, fecha: date) -> dict:
    """
    Marca pendientes ya enviadas como procesadas y cierra la fuente si no quedan pendientes.
    """

    def _reconcile() -> dict:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE articulos_pendientes ap
                    SET ia_procesada = TRUE
                    WHERE ap.url_fuente = %s
                      AND ap.fecha_ef = %s
                      AND ap.ia_procesada = FALSE
                      AND EXISTS (
                          SELECT 1
                          FROM noticias_enviadas ne
                          WHERE ne.url = ap.url
                      )
                    """,
                    (url_fuente, fecha),
                )
                conciliados = cur.rowcount or 0

                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN ia_procesada THEN 1 ELSE 0 END) AS procesadas,
                        SUM(CASE WHEN NOT ia_procesada THEN 1 ELSE 0 END) AS pendientes
                    FROM articulos_pendientes
                    WHERE url_fuente = %s AND fecha_ef = %s
                    """,
                    (url_fuente, fecha),
                )
                estado = dict(cur.fetchone() or {})
                total = estado.get("total") or 0
                procesadas = estado.get("procesadas") or 0
                pendientes = estado.get("pendientes") or 0

                if total > 0 and pendientes == 0:
                    cur.execute(
                        """
                        UPDATE ejecucion_fuentes
                        SET ia_ok = TRUE,
                            noticias_enviadas = %s,
                            error_detalle = NULL,
                            actualizado_en = NOW()
                        WHERE url_fuente = %s AND fecha_ejecucion = %s
                        """,
                        (procesadas, url_fuente, fecha),
                    )

                return {
                    "conciliados": conciliados,
                    "total": total,
                    "procesadas": procesadas,
                    "pendientes": pendientes,
                    "ia_ok_cerrada": bool(total > 0 and pendientes == 0),
                }

    resultado, ok, err = retrier.con_reintentos(
        fn=_reconcile,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="reconciliar_pendientes_con_enviadas",
        url=url_fuente,
        registrar_en_collector=False,
    )

    if ok:
        resultado = resultado or {
            "conciliados": 0,
            "total": 0,
            "procesadas": 0,
            "pendientes": 0,
            "ia_ok_cerrada": False,
        }
        logger.info(
            "Reconciliación solo-IA [%s] fecha %s -> conciliados=%d | total=%d | procesadas=%d | pendientes=%d | ia_ok_cerrada=%s",
            url_fuente,
            fecha,
            resultado["conciliados"],
            resultado["total"],
            resultado["procesadas"],
            resultado["pendientes"],
            resultado["ia_ok_cerrada"],
        )
        return resultado

    logger.error(
        "No se pudo reconciliar articulos_pendientes con noticias_enviadas para [%s] fecha %s: %s",
        url_fuente,
        fecha,
        err,
    )
    return {
        "conciliados": 0,
        "total": 0,
        "procesadas": 0,
        "pendientes": 0,
        "ia_ok_cerrada": False,
    }


def limpiar_articulos_pendientes(fecha: date) -> None:
    """
    Elimina pendientes solamente de fuentes con IA ya completada.
    """

    def _delete():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM articulos_pendientes
                    WHERE fecha_ef = %s
                      AND url_fuente IN (
                          SELECT url_fuente FROM ejecucion_fuentes
                          WHERE fecha_ejecucion = %s AND ia_ok = TRUE
                      )
                    """,
                    (fecha, fecha),
                )
                return cur.rowcount

    result, ok, err = retrier.con_reintentos(
        fn=_delete,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="limpiar_articulos_pendientes",
        url=str(fecha),
        registrar_en_collector=False,
    )
    if ok:
        logger.info(
            "Artículos pendientes limpiados para fuentes con ia_ok=TRUE [fecha=%s]: %s eliminados",
            fecha, result or 0,
        )
    else:
        logger.warning(
            "No se pudo limpiar articulos_pendientes para %s: %s "
            "(artículos ya marcados ia_procesada=TRUE — sin riesgo de doble cobro)",
            fecha, err,
        )


def get_fuentes_solo_ia(todas_fuentes: list[dict], fecha: date) -> tuple[list[dict], list[dict]]:
    """
    Separa fuentes pendientes entre scraping+IA y solo-IA.
    """
    urls = [f["url"] for f in todas_fuentes]

    if not urls:
        return [], []

    def _query() -> set[str]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT url_fuente
                    FROM ejecucion_fuentes
                    WHERE fecha_ejecucion = %s
                      AND scraping_ok = TRUE
                      AND ia_ok = FALSE
                      AND url_fuente = ANY(%s)
                    """,
                    (fecha, urls),
                )
                return {row[0] for row in cur.fetchall()}

    resultado, ok, err = retrier.con_reintentos(
        fn=_query,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="get_fuentes_solo_ia",
        url=str(fecha),
        registrar_en_collector=False,
    )

    if not ok:
        logger.error(
            "No se pudo consultar fuentes solo-IA para %s: %s — "
            "fallback: todas las fuentes harán scraping completo",
            fecha, err,
        )
        return todas_fuentes, []

    urls_solo_ia = resultado or set()
    fuentes_todo = [f for f in todas_fuentes if f["url"] not in urls_solo_ia]
    fuentes_solo_ia = [f for f in todas_fuentes if f["url"] in urls_solo_ia]

    if fuentes_solo_ia:
        logger.info(
            "Fuentes solo-IA (scraping ya OK): %d | Fuentes scraping+IA: %d",
            len(fuentes_solo_ia), len(fuentes_todo),
        )
    return fuentes_todo, fuentes_solo_ia
