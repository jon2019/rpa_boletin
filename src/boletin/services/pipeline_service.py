"""Orquestación del pipeline principal del boletín."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from boletin.infrastructure.db import facade as db
from boletin.infrastructure.notifications import emailer
from boletin.infrastructure.resilience import retrier
from boletin.scraping import orchestrator as scraper
from boletin.services import scoring_service as scorer
from boletin.services import translation_service as translator
from boletin.services.execution_calendar import calcular_fecha_efectiva
from boletin.services.pipeline_reporting import (
    log_tabla_noticias_por_url,
    log_tabla_noticias_ponderadas,
    render_tabla_fuentes_ejecutables,
    render_tabla_fuentes_omitidas,
    render_tabla_fuentes_problemas,
    render_tabla_fuentes_solo_ia_activas,
    render_tabla_fuentes_solo_ia_cerradas,
)
from boletin.services.source_classifier import clasificar_fuentes_operativas


def _cargar_snapshot_preview(
    todas_las_fuentes: list[dict],
    fecha_efectiva,
) -> list[dict]:
    """Recupera artículos pendientes persistidos para validar preview sin red."""
    snapshot = []
    for fuente in todas_las_fuentes:
        snapshot.extend(db.get_articulos_pendientes(fuente["url"], fecha_efectiva))
    return snapshot


def ejecutar_pipeline(preview: bool, logger: logging.Logger) -> None:
    """
    Ejecuta el pipeline completo asumiendo que el lock externo ya fue adquirido.
    """
    retrier.reset_collector()

    fuentes_con_problemas_operativos = db.get_fuentes_con_problemas_operativos()
    if fuentes_con_problemas_operativos:
        tabla_problemas = render_tabla_fuentes_problemas(fuentes_con_problemas_operativos)
        logger.warning(
            "Fuentes activas bloqueadas por configuración operativa (%d):\n%s",
            len(fuentes_con_problemas_operativos),
            tabla_problemas,
        )

    inicio = datetime.now(tz=timezone.utc)
    fecha_efectiva = calcular_fecha_efectiva(
        inicio,
        logger,
        permitir_reprocesada=preview,
    )

    if fecha_efectiva is None:
        logger.info("No se requiere ejecución del pipeline (fecha ya procesada)")
        return

    logger.info("=" * 60)
    logger.info("INICIO DEL PIPELINE - %s (fecha efectiva: %s)", inicio.isoformat(), fecha_efectiva)
    logger.info("=" * 60)

    if fuentes_con_problemas_operativos and not preview:
        emailer.enviar_alerta_fuentes_operativas(
            fuentes_con_problemas_operativos,
            fecha_efectiva=fecha_efectiva,
        )

    try:
        logger.info("PASO 1/5 - Scraping de fuentes pendientes...")
        todas_las_fuentes = db.get_fuentes_activas()
        pendientes = (
            db.get_fuentes_activas()
            if preview
            else db.obtener_fuentes_pendientes(fecha_efectiva)
        )
        preview_snapshot = _cargar_snapshot_preview(todas_las_fuentes, fecha_efectiva) if preview else []

        hoy_weekday = inicio.weekday()
        es_dia_autorizado = hoy_weekday in (1, 3)
        if not es_dia_autorizado:
            nombre_dia = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][hoy_weekday]
            completadas = len(todas_las_fuentes) - len(pendientes)
            logger.info(
                "[COMPENSACION] Hoy es %s. Fecha efectiva: %s | Total en BD: %d | "
                "Ya completadas: %d | Pendientes a reintentar: %d",
                nombre_dia.upper(),
                fecha_efectiva,
                len(todas_las_fuentes),
                completadas,
                len(pendientes),
            )
        else:
            logger.info(
                "[ORDINARIA] Fecha efectiva: %s | Fuentes a consultar: %d",
                fecha_efectiva,
                len(pendientes),
            )

        if preview_snapshot:
            logger.info(
                "MODO PREVIEW - usando snapshot persistido de articulos_pendientes (%d artículos).",
                len(preview_snapshot),
            )
            fuentes_todo, fuentes_solo_ia = [], []
        elif preview:
            logger.info(
                "MODO PREVIEW - se procesarán todas las fuentes activas sin filtrar por estado previo."
            )
            fuentes_todo, fuentes_solo_ia = pendientes, []
        else:
            fuentes_todo, fuentes_solo_ia = db.get_fuentes_solo_ia(pendientes, fecha_efectiva)

        if fuentes_todo:
            login_sources_plan, rss_sources_plan, scrape_sources_plan, omitidas_operativas = clasificar_fuentes_operativas(fuentes_todo)
            ejecutables = login_sources_plan + rss_sources_plan + scrape_sources_plan

            if ejecutables:
                tabla = render_tabla_fuentes_ejecutables(ejecutables)
                logger.info("Fuentes realmente ejecutables en este ciclo (%d):\n%s", len(ejecutables), tabla)

            if omitidas_operativas:
                tabla_omitidas = render_tabla_fuentes_omitidas(omitidas_operativas)
                logger.warning(
                    "Fuentes omitidas del ciclo operativo por configuración (%d):\n%s",
                    len(omitidas_operativas),
                    tabla_omitidas,
                )

        articulos_solo_ia = []
        if fuentes_solo_ia:
            fuentes_solo_ia_activas = []
            fuentes_solo_ia_cerradas = []

            for fuente in fuentes_solo_ia:
                reconc = db.reconciliar_pendientes_con_enviadas(fuente["url"], fecha_efectiva)
                if reconc.get("ia_ok_cerrada"):
                    fuentes_solo_ia_cerradas.append((fuente, reconc))
                else:
                    fuentes_solo_ia_activas.append((fuente, reconc))

            if fuentes_solo_ia_cerradas:
                tabla_cerradas = render_tabla_fuentes_solo_ia_cerradas(fuentes_solo_ia_cerradas)
                logger.info(
                    "Fuentes solo-IA cerradas automáticamente por ya-enviadas (%d):\n%s",
                    len(fuentes_solo_ia_cerradas),
                    tabla_cerradas,
                )

            if fuentes_solo_ia_activas:
                tabla_ia = render_tabla_fuentes_solo_ia_activas(fuentes_solo_ia_activas)
                logger.info(
                    "Fuentes solo-IA (scraping ya OK, reintentando IA) (%d):\n%s",
                    len(fuentes_solo_ia_activas),
                    tabla_ia,
                )

            for fuente, _ in fuentes_solo_ia_activas:
                arts = db.get_articulos_pendientes(fuente["url"], fecha_efectiva)
                articulos_solo_ia.extend(arts)
            logger.info("Artículos recuperados desde DB para retry IA: %d", len(articulos_solo_ia))

        if not preview_snapshot and not fuentes_todo and not fuentes_solo_ia:
            logger.info("Sin fuentes pendientes para esta fecha efectiva.")

        if preview_snapshot:
            todas = preview_snapshot
            failed_sources = []
        else:
            login_sources, rss_sources, scrape_sources, _ = clasificar_fuentes_operativas(fuentes_todo)
            todas, failed_sources = scraper.scrape_all(
                login_sources,
                rss_sources,
                scrape_sources,
                fuentes_todo,
                fecha_efectiva=fecha_efectiva,
            )

        if articulos_solo_ia:
            todas = todas + articulos_solo_ia
            logger.info("Total artículos combinados (scraping + retry IA): %d", len(todas))
        if not todas:
            logger.warning("Sin noticias nuevas que procesar. Finalizando.")
            return

        log_tabla_noticias_por_url(todas, logger)

        logger.info("PASO 2/5 - Filtrando historial...")
        nuevas = todas if preview else db.filtrar_enviadas(todas)
        if preview:
            logger.info(
                "MODO PREVIEW - se omite el filtro de historial para validar scoring, traducción y render."
            )
        if not nuevas:
            logger.warning("Todas las noticias ya fueron enviadas anteriormente.")
            return

        logger.info("PASO 3/5 - Scoring con Claude IA...")
        top = scorer.puntuar_y_seleccionar(nuevas, fecha_efectiva)
        if not top:
            logger.warning(
                "No hay noticias seleccionadas para el boletín — "
                "se enviará notificación de 0 noticias para evitar consultas de los destinatarios."
            )

        # Actualizar scraping_log con las ponderaciones calculadas.
        # En este punto `nuevas` ya tiene score en cada dict (mutado en-place
        # por puntuar_y_seleccionar): score Claude para top-60, pre-score local para el resto.
        db.actualizar_ponderacion_scraping_log(nuevas, fecha_efectiva)

        log_tabla_noticias_ponderadas(top, logger)

        logger.info("PASO 4/5 - Traducción al inglés...")
        top = translator.traducir(top)

        if preview:
            logger.info("PASO 5/5 - Generando preview (sin envío)...")
            preview_path = "tests/output/previews/preview.html"
            emailer.preview_html(top, output_path=preview_path)
            logger.info("Preview guardado: %s", preview_path)
        else:
            logger.info("PASO 5/5 - Enviando boletín...")
            ok = emailer.enviar(top, failed_sources, fecha_efectiva=fecha_efectiva)

            if ok:
                db.marcar_enviadas(top)
                db.registrar_envio(top, ok=True, fecha=fecha_efectiva)
                db.limpiar_articulos_pendientes(fecha_efectiva)
                logger.info("Boletín enviado exitosamente (%d noticias)", len(top))
            else:
                db.registrar_envio(top, ok=False, fecha=fecha_efectiva)
                logger.error("Error al enviar el boletín")

    except Exception as e:
        logger.exception("Error crítico en el pipeline: %s", e)

    finally:
        duracion = (datetime.now(tz=timezone.utc) - inicio).total_seconds()
        logger.info("Pipeline finalizado en %.1f segundos", duracion)

        try:
            resumen = db.resumen_ejecucion_hoy(fecha_efectiva)
            logger.info(
                "Resumen: %d fuentes | %d scraping OK | %d IA OK | "
                "%d completas | %d noticias obtenidas",
                resumen["total"] or 0,
                resumen["scraping_ok"] or 0,
                resumen["ia_ok"] or 0,
                resumen["completas"] or 0,
                resumen["noticias_obtenidas"] or 0,
            )

            if resumen.get("fuentes_ia_fallidas"):
                logger.warning("Fuentes que fallaron en IA (%d):", len(resumen["fuentes_ia_fallidas"]))
                for fuente in resumen["fuentes_ia_fallidas"]:
                    logger.warning("  - %s: %s", fuente["nombre"], fuente["error"][:100])

        except Exception:
            pass

        collector = retrier.get_collector()
        if collector.hay_errores():
            logger.warning(
                "Se acumularon %d errores durante el pipeline - enviando resumen...",
                collector.total(),
            )
            collector.enviar_resumen()
        else:
            logger.info("Sin errores que reportar en este ciclo.")

        logger.info("=" * 60)
