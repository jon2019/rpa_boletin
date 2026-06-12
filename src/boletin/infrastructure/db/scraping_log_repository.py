"""Registro de noticias encontradas por ejecución de scraping."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from boletin.infrastructure.db.connection import get_connection

logger = logging.getLogger(__name__)


def _fecha_hoy() -> date:
    return datetime.now(tz=timezone.utc).date()


def registrar_scraping_log(
    url_fuente: str,
    fecha: date | None,
    ok: bool,
    noticias: list[dict],
    error: str | None = None,
) -> int | None:
    """
    Crea un registro nuevo en scraping_log para esta iteración del pipeline.

    Devuelve el `id` del registro creado para que el llamador pueda adjuntarlo
    a las noticias y usarlo luego para actualizar la ponderación sobre el mismo
    registro exacto (no sobre el "más reciente").

    numero_ejecucion es MAX+1 para (url_fuente, fecha) — empieza en 1 cada día.
    """
    fecha_log = fecha or _fecha_hoy()

    if ok:
        resultado = {
            "fecha":      fecha_log.isoformat(),
            "url_fuente": url_fuente,
            "total":      len(noticias),
            "noticias": [
                {
                    "numero_noticia": i,
                    "url":            n.get("url", ""),
                    "titulo":         n.get("titulo", ""),
                }
                for i, n in enumerate(noticias, start=1)
            ],
        }
    else:
        resultado = {
            "fecha":      fecha_log.isoformat(),
            "url_fuente": url_fuente,
            "total":      0,
            "error":      (error or "Error desconocido")[:1000],
        }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH next_num AS (
                        SELECT COALESCE(MAX(numero_ejecucion), 0) + 1 AS num
                        FROM scraping_log
                        WHERE url_fuente = %s AND fecha = %s
                    )
                    INSERT INTO scraping_log
                        (url_fuente, fecha, numero_ejecucion, ok, total_noticias, resultado)
                    SELECT %s, %s, num, %s, %s, %s::jsonb
                    FROM next_num
                    RETURNING id, numero_ejecucion
                    """,
                    (
                        url_fuente, fecha_log,
                        url_fuente, fecha_log,
                        ok,
                        len(noticias) if ok else 0,
                        json.dumps(resultado, ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
                record_id, numero = row[0], row[1]

        logger.debug(
            "scraping_log id=%d [%s] fecha=%s ejecucion=%d ok=%s total=%d",
            record_id, url_fuente, fecha_log, numero, ok, len(noticias) if ok else 0,
        )
        return record_id

    except Exception as exc:
        logger.warning(
            "No se pudo registrar scraping_log para [%s] fecha=%s: %s",
            url_fuente, fecha_log, exc,
        )
        return None


def actualizar_ponderacion_scraping_log(
    noticias: list[dict],
    fecha: date | None,
) -> None:
    """
    Actualiza el registro EXACTO de scraping_log (por id) que fue creado en
    esta misma iteración del pipeline, añadiendo la ponderacion a cada noticia
    y reordenando de mayor a menor.

    El vínculo entre la noticia y su registro es `_scraping_log_id`, que el
    orchestrator adjunta a cada noticia cuando llama a registrar_scraping_log.
    Esto garantiza que cada iteración crea UN registro y actualiza ESE MISMO,
    sin ambigüedad entre ejecuciones del mismo día.
    """
    # Agrupar scores y criterios por scraping_log_id → {url_noticia: score/criterios}
    score_por_id:    dict[int, dict[str, int]]  = {}
    criterios_por_id: dict[int, dict[str, dict]] = {}
    for n in noticias:
        log_id = n.get("_scraping_log_id")
        url_noticia = n.get("url", "")
        score = n.get("score")
        if not log_id or score is None:
            continue
        score_por_id.setdefault(log_id, {})[url_noticia] = score
        criterios_por_id.setdefault(log_id, {})[url_noticia] = n.get("_criterios")

    if not score_por_id:
        return

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for log_id, score_map in score_por_id.items():
                    criterios_map = criterios_por_id.get(log_id, {})
                    cur.execute(
                        "SELECT resultado FROM scraping_log WHERE id = %s AND ok = TRUE",
                        (log_id,),
                    )
                    row = cur.fetchone()
                    if not row or not row[0] or "noticias" not in row[0]:
                        continue

                    resultado = row[0]

                    noticias_actualizadas = [
                        {
                            "numero_noticia":               item.get("numero_noticia"),
                            "url":                          item.get("url", ""),
                            "titulo":                       item.get("titulo", ""),
                            "ponderacion_ia_semantica_final": score_map.get(item.get("url", "")),
                            "criterios":                    criterios_map.get(item.get("url", "")),
                        }
                        for item in resultado["noticias"]
                    ]

                    # Mayor ponderacion primero; las sin score (ya enviadas) al final
                    noticias_actualizadas.sort(
                        key=lambda x: x["ponderacion_ia_semantica_final"] if x["ponderacion_ia_semantica_final"] is not None else -1,
                        reverse=True,
                    )

                    cur.execute(
                        """
                        UPDATE scraping_log
                        SET resultado = %s::jsonb
                        WHERE id = %s
                        """,
                        (
                            json.dumps(
                                {**resultado, "noticias": noticias_actualizadas},
                                ensure_ascii=False,
                            ),
                            log_id,
                        ),
                    )

        logger.debug(
            "Ponderacion actualizada en scraping_log para %d registros",
            len(score_por_id),
        )
    except Exception as exc:
        logger.warning("No se pudo actualizar ponderacion en scraping_log: %s", exc)


def get_scraping_log(
    url_fuente: str,
    fecha: date,
) -> list[dict]:
    """
    Devuelve todos los registros de scraping_log para una fuente y fecha,
    ordenados por numero_ejecucion ascendente.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT numero_ejecucion, ok, total_noticias, resultado, creado_en
                    FROM scraping_log
                    WHERE url_fuente = %s AND fecha = %s
                    ORDER BY numero_ejecucion ASC
                    """,
                    (url_fuente, fecha),
                )
                rows = cur.fetchall()
        return [
            {
                "numero_ejecucion": r[0],
                "ok":               r[1],
                "total_noticias":   r[2],
                "resultado":        r[3],
                "creado_en":        r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("No se pudo consultar scraping_log para [%s]: %s", url_fuente, exc)
        return []
