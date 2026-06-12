"""Consultas y actualizaciones de fuentes/configuración."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg2.extras

from boletin.infrastructure.db.connection import get_connection

logger = logging.getLogger(__name__)


def get_fuentes_activas() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, nombre AS name, url, url_rss AS rss,
                       pais AS country, metodo AS method,
                       scrape_selector, nota AS note,
                       usuario, clave, login_url, post_login_url
                FROM fuentes
                WHERE activa = TRUE
                ORDER BY pais, nombre
            """)
            rows = cur.fetchall()
    fuentes = [dict(r) for r in rows]
    logger.info("Fuentes activas cargadas desde DB: %d", len(fuentes))
    return fuentes


def get_paises_activos() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT nombre, nombre_en, codigo_iso, bandera, cuota
                FROM paises
                WHERE activo = TRUE
                ORDER BY orden ASC
            """)
            rows = cur.fetchall()
    paises = [dict(r) for r in rows]
    logger.info("Países activos: %s", ", ".join(f"{p['nombre']}({p['cuota']})" for p in paises))
    return paises


def get_score_config() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT codigo, puntos FROM score_reglas WHERE activa = TRUE")
            reglas = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT nombre FROM score_empresas WHERE activa = TRUE")
            empresas = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT nombre FROM score_empresas_conocidas WHERE activa = TRUE")
            empresas_conocidas = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT keyword FROM score_keywords WHERE activa = TRUE AND tipo = 'contrato'")
            keywords = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT keyword FROM score_keywords WHERE activa = TRUE AND tipo = 'concepto'")
            keywords_concepto = [row[0] for row in cur.fetchall()]
            cur.execute("SELECT keyword FROM score_keywords WHERE activa = TRUE AND tipo = 'entrevista'")
            keywords_entrevista = [row[0] for row in cur.fetchall()]
            cur.execute("""
                SELECT nombre, tipo, puntos
                FROM score_empresa_tipo
                WHERE activa = TRUE
                ORDER BY puntos DESC
            """)
            empresas_tipo = [(row[0], row[1], row[2]) for row in cur.fetchall()]
            cur.execute("SELECT termino FROM score_sector_contexto WHERE activa = TRUE")
            sector_contexto = [row[0] for row in cur.fetchall()]
    logger.info(
        "Score config: %d reglas | %d empresas | %d empresas_conocidas "
        "| %d keywords_contrato | %d keywords_concepto | %d keywords_entrevista "
        "| %d empresa_tipo | %d sector_contexto",
        len(reglas), len(empresas), len(empresas_conocidas),
        len(keywords), len(keywords_concepto), len(keywords_entrevista),
        len(empresas_tipo), len(sector_contexto),
    )
    return {
        "reglas":               reglas,
        "empresas":             empresas,
        "empresas_conocidas":   empresas_conocidas,
        "keywords":             keywords,
        "keywords_concepto":    keywords_concepto,
        "keywords_entrevista":  keywords_entrevista,
        "empresas_tipo":        empresas_tipo,
        "sector_contexto":      sector_contexto,
    }


def get_fuentes_sin_selector() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, nombre AS name, url, url_rss AS rss,
                       pais AS country, metodo AS method,
                       scrape_selector, nota AS note
                FROM fuentes
                WHERE activa = TRUE
                  AND (scrape_selector IS NULL OR TRIM(scrape_selector) = '')
                  AND metodo = 'scrape'
                ORDER BY pais, nombre
            """)
            rows = cur.fetchall()
    fuentes = [dict(r) for r in rows]
    logger.info("Fuentes activas SIN selector: %d", len(fuentes))
    return fuentes


def get_fuentes_omitidas_reglas_negocio() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,
                       nombre AS name,
                       url,
                       url_rss AS rss,
                       pais AS country,
                       metodo AS method,
                       scrape_selector,
                       usuario,
                       clave
                FROM fuentes
                WHERE activa = TRUE
                  AND (url_rss IS NULL OR TRIM(url_rss) = '')
                  AND (scrape_selector IS NULL OR TRIM(scrape_selector) = '')
                  AND (usuario IS NULL OR TRIM(usuario) = '')
                  AND (clave IS NULL OR TRIM(clave) = '')
                ORDER BY pais, nombre
            """)
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_fuentes_con_problemas_operativos() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id,
                       nombre AS name,
                       url,
                       url_rss AS rss,
                       pais AS country,
                       metodo AS method,
                       scrape_selector,
                       usuario,
                       clave
                FROM fuentes
                WHERE activa = TRUE
                ORDER BY pais, nombre
            """)
            rows = cur.fetchall()

    problemas: list[dict] = []
    for row in rows:
        fuente = dict(row)
        tiene_rss = bool((fuente.get("rss") or "").strip())
        tiene_selector = bool((fuente.get("scrape_selector") or "").strip())
        tiene_login = bool((fuente.get("usuario") or "").strip() and (fuente.get("clave") or "").strip())
        if tiene_rss or tiene_selector or tiene_login:
            continue
        problema = "Sin rss, sin scrape_selector y sin credenciales"
        if (fuente.get("method") or "").strip().lower() == "scrape":
            problema = "Sin scrape_selector para fuente scrape sin RSS ni credenciales"
        fuente["problema"] = problema
        problemas.append(fuente)
    return problemas


def actualizar_feed_fuente(fuente_id: int, url_rss: str | None, metodo: str | None) -> None:
    ahora = datetime.now(tz=timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fuentes
                SET url_rss = %s,
                    metodo = %s,
                    actualizado_en = %s
                WHERE id = %s
            """, (url_rss, metodo, ahora, fuente_id))


def actualizar_selector_fuente(fuente_id: int, scrape_selector: str | None, nota: str | None = None) -> None:
    ahora = datetime.now(tz=timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fuentes
                SET scrape_selector = %s,
                    nota = COALESCE(%s, nota),
                    actualizado_en = %s
                WHERE id = %s
            """, (scrape_selector, nota, ahora, fuente_id))
