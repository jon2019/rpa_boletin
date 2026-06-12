"""Clasificación de fuentes operativas según su estrategia de ejecución."""

from __future__ import annotations


def clasificar_fuentes_operativas(
    fuentes: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Separa fuentes ejecutables por tipo y fuentes omitidas por configuración.
    """
    login_sources = [f for f in fuentes if f.get("usuario") and f.get("clave")]
    no_login = [f for f in fuentes if not (f.get("usuario") and f.get("clave"))]

    rss_sources = [f for f in no_login if f.get("rss")]

    # Todas las fuentes sin login y sin RSS van a scraping.
    # Si tienen scrape_selector se usa ese; si no, se aplican los selectores genéricos.
    # Ya no se omite ninguna fuente por falta de scrape_selector explícito.
    scrape_sources = [f for f in no_login if not f.get("rss")]

    return login_sources, rss_sources, scrape_sources, []
