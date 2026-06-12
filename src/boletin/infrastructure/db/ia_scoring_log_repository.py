"""Registro de noticias que llegaron a Claude, agrupadas por país, por ejecución."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from boletin.infrastructure.db.connection import get_connection

logger = logging.getLogger(__name__)


def _fecha_hoy() -> date:
    return datetime.now(tz=timezone.utc).date()


def registrar_ia_scoring_log(
    candidatas: list[dict],
    fecha: date | None = None,
    urls_boletin: set[str] | None = None,
) -> None:
    """
    Crea un nuevo registro en ia_scoring_log para esta ejecución del RPA.
    numero_insercion = MAX+1 por día (empieza en 1 cada día).

    IMPORTANTE: usa la fecha REAL de ejecución (hoy), NO fecha_efectiva.
    fecha_efectiva es la fecha de negocio del contenido (puede ser ayer en
    ejecuciones de compensación). ia_scoring_log registra cuándo corrió Claude.

    Dentro de cada país las noticias se ordenan:
      1. Las que entraron al boletín (en_boletin=true), por ia_score desc
      2. Las que quedaron fuera (en_boletin=false), por ia_score desc
    """
    fecha_log    = _fecha_hoy()  # siempre fecha real de ejecución
    urls_boletin = urls_boletin or set()

    # ── Agrupar candidatas por país ───────────────────────────────────────────
    por_pais: dict[str, list] = {}
    for n in candidatas:
        pais = n.get("pais") or "Sin clasificar"
        por_pais.setdefault(pais, []).append(n)

    # ── Construir el JSON de resultado ────────────────────────────────────────
    resultado: dict = {
        "fecha":          fecha_log.isoformat(),
        "total_noticias": len(candidatas),
    }

    # Países ordenados alfabéticamente (orden estable y predecible)
    for pais, noticias in sorted(por_pais.items()):

        # Primero boletín (en_boletin=True) por score desc,
        # luego excluidas (en_boletin=False) por score desc
        en_boletin  = sorted(
            [n for n in noticias if n.get("url", "") in urls_boletin],
            key=lambda x: x.get("score", 0) or 0,
            reverse=True,
        )
        excluidas = sorted(
            [n for n in noticias if n.get("url", "") not in urls_boletin],
            key=lambda x: x.get("score", 0) or 0,
            reverse=True,
        )
        noticias_ordenadas = en_boletin + excluidas

        resultado[pais] = {
            "total_noticias_pais":  len(noticias_ordenadas),
            "total_en_boletin":     len(en_boletin),
            "total_excluidas":      len(excluidas),
            "noticias": [
                {
                    "numero":     i,
                    "en_boletin": n.get("url", "") in urls_boletin,
                    "url":        n.get("url", ""),
                    "titulo":     n.get("titulo", ""),
                    "fuente":     n.get("fuente", ""),
                    "ia_score":   n.get("score", 0),
                    "criterios":  n.get("_criterios", {}),
                }
                for i, n in enumerate(noticias_ordenadas, start=1)
            ],
        }

    # ── Insertar con numero_insercion = MAX+1 para la fecha ───────────────────
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH next_num AS (
                        SELECT COALESCE(MAX(numero_insercion), 0) + 1 AS num
                        FROM ia_scoring_log
                        WHERE fecha = %s
                    )
                    INSERT INTO ia_scoring_log
                        (fecha, numero_insercion, total_noticias, resultado)
                    SELECT %s, num, %s, %s::jsonb
                    FROM next_num
                    RETURNING id, numero_insercion
                    """,
                    (
                        fecha_log,
                        fecha_log,
                        len(candidatas),
                        json.dumps(resultado, ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
                record_id, numero = row[0], row[1]

        logger.info(
            "ia_scoring_log id=%d fecha=%s insercion=%d total=%d paises=%s",
            record_id, fecha_log, numero, len(candidatas),
            ", ".join(f"{p}:{len(ns['noticias'])}" for p, ns in resultado.items()
                      if isinstance(ns, dict) and "noticias" in ns),
        )
    except Exception as exc:
        logger.warning(
            "No se pudo guardar ia_scoring_log fecha=%s: %s", fecha_log, exc
        )


def get_ia_scoring_log(fecha: date, pais: str | None = None) -> list[dict]:
    """
    Devuelve todos los registros de ia_scoring_log para una fecha,
    ordenados por numero_insercion ascendente.
    Si se especifica pais, devuelve solo el bloque de ese país dentro del resultado.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT numero_insercion, total_noticias, resultado, creado_en
                    FROM ia_scoring_log
                    WHERE fecha = %s
                    ORDER BY numero_insercion ASC
                    """,
                    (fecha,),
                )
                rows = cur.fetchall()

        registros = []
        for r in rows:
            resultado = r[2] or {}
            if pais:
                resultado = {pais: resultado.get(pais, {})}
            registros.append({
                "numero_insercion": r[0],
                "total_noticias":   r[1],
                "resultado":        resultado,
                "creado_en":        r[3].isoformat() if r[3] else None,
            })
        return registros

    except Exception as exc:
        logger.warning("No se pudo consultar ia_scoring_log fecha=%s: %s", fecha, exc)
        return []
