"""Historial de noticias enviadas y registro de envíos."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

import psycopg2.extras

from boletin.infrastructure.resilience import retrier
from boletin.infrastructure.db.connection import get_connection
from boletin.infrastructure.db.execution_repository import hoy_utc

logger = logging.getLogger(__name__)


def hash_url(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def filtrar_enviadas(noticias: list[dict]) -> list[dict]:
    if not noticias:
        return []

    hashes = {hash_url(n["url"]): n for n in noticias}

    def _query():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT url_hash FROM noticias_enviadas WHERE url_hash = ANY(%s)",
                    (list(hashes.keys()),),
                )
                return {row[0] for row in cur.fetchall()}

    resultado, ok, _ = retrier.con_reintentos(
        fn=_query,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="filtrar_enviadas",
        url="noticias_enviadas",
    )
    ya = resultado if ok else set()
    nuevas = [n for h, n in hashes.items() if h not in ya]
    logger.info(
        "Filtro historial: %d total -> %d nuevas (%d ya enviadas)",
        len(noticias), len(nuevas), len(ya)
    )
    return nuevas


def marcar_enviadas(noticias: list[dict]) -> None:
    if not noticias:
        return

    ahora = datetime.now(tz=timezone.utc)
    rows = [
        (
            hash_url(n["url"]),
            n["titulo"][:500],
            n["fuente"][:200],
            n["pais"][:50],
            n["url"][:2000],
            ahora,
        )
        for n in noticias
    ]

    def _insert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO noticias_enviadas
                        (url_hash, titulo, fuente, pais, url, enviado_en)
                    VALUES %s ON CONFLICT (url_hash) DO NOTHING
                    """,
                    rows,
                )

    _, ok, _ = retrier.con_reintentos(
        fn=_insert,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="marcar_enviadas",
        url="noticias_enviadas",
    )
    if ok:
        logger.info("Marcadas %d noticias como enviadas", len(rows))
    else:
        logger.error("No se pudieron marcar %d noticias en DB", len(rows))


def registrar_envio(noticias: list[dict], ok: bool = True, fecha: date | None = None) -> None:
    fecha_envio = fecha or hoy_utc()
    conteo: dict[str, int] = {}
    for n in noticias:
        pais = n.get("pais_boletin", n.get("pais", "")) or ""
        if pais:
            conteo[pais] = conteo.get(pais, 0) + 1

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO envios_log (fecha, total_noticias, por_pais, ok)
                VALUES (%s,%s,%s,%s)
                """,
                (fecha_envio, len(noticias), json.dumps(conteo), ok),
            )

    resumen_paises = " | ".join(f"{p}:{c}" for p, c in sorted(conteo.items()))
    logger.info("Envío registrado — Total:%d [%s] ok:%s", len(noticias), resumen_paises, ok)
