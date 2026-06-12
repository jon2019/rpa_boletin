"""Helpers de reporting tabular para el pipeline."""

from __future__ import annotations

from collections import defaultdict
import logging

from tabulate import tabulate


def log_tabla_noticias_por_url(noticias: list[dict], logger: logging.Logger) -> None:
    if not noticias:
        logger.info("TABLA FINAL — No hay noticias para resumir por URL fuente.")
        return

    counters: dict[tuple[str, str], int] = defaultdict(int)
    for noticia in noticias:
        url_fuente = noticia.get("url_fuente") or noticia.get("fuente") or "-"
        fuente = noticia.get("fuente") or "-"
        counters[(fuente, url_fuente)] += 1

    rows = [
        [fuente, url_fuente, total]
        for (fuente, url_fuente), total in sorted(
            counters.items(),
            key=lambda item: (-item[1], item[0][0].lower(), item[0][1].lower()),
        )
    ]
    tabla = tabulate(
        rows,
        headers=["Fuente", "URL fuente", "Noticias encontradas"],
        tablefmt="grid",
    )
    logger.info("TABLA FINAL — TOTAL DE NOTICIAS ENCONTRADAS POR URL\n%s", tabla)


def log_tabla_noticias_ponderadas(noticias: list[dict], logger: logging.Logger) -> None:
    if not noticias:
        logger.info("TABLA FINAL — No hay noticias ponderadas para resumir.")
        return

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for noticia in noticias:
        pais = noticia.get("pais") or "-"
        url_fuente = noticia.get("url_fuente") or noticia.get("fuente") or "-"
        fuente = noticia.get("fuente") or "-"
        grouped[(pais, fuente, url_fuente)].append(noticia)

    for (pais, fuente, url_fuente), items in sorted(
        grouped.items(),
        key=lambda item: (item[0][0].lower(), item[0][2].lower(), item[0][1].lower()),
    ):
        ordered = sorted(
            items,
            key=lambda n: (-(n.get("score") or 0), (n.get("titulo") or "").lower()),
        )
        rows = []
        for idx, noticia in enumerate(ordered, start=1):
            rows.append(
                [
                    idx,
                    pais,
                    fuente,
                    (noticia.get("titulo") or "")[:110],
                    noticia.get("score") or 0,
                    (noticia.get("url") or "")[:140],
                ]
            )
        tabla = tabulate(
            rows,
            headers=["#", "País", "Fuente", "Título", "Ponderación", "URL noticia"],
            tablefmt="grid",
        )
        logger.info(
            "TABLA FINAL — NOTICIAS PONDERADAS | País=%s | Fuente=%s | URL fuente=%s\n%s",
            pais,
            fuente,
            url_fuente,
            tabla,
        )


def render_tabla_fuentes_problemas(fuentes: list[dict]) -> str:
    return tabulate(
        [[f["name"], f["country"], f["url"], f["problema"]] for f in fuentes],
        headers=["Fuente", "País", "URL", "Problema"],
        tablefmt="simple",
    )


def render_tabla_fuentes_ejecutables(fuentes: list[dict]) -> str:
    return tabulate(
        [
            [
                f["name"],
                f["country"],
                "Login" if f.get("usuario") and f.get("clave") else ("RSS" if f.get("rss") else "Scrape"),
                (f.get("rss") or f["url"])[:70],
            ]
            for f in fuentes
        ],
        headers=["Fuente", "País", "Método", "URL"],
        tablefmt="simple",
    )


def render_tabla_fuentes_omitidas(fuentes: list[dict]) -> str:
    return tabulate(
        [[f["name"], f["country"], f["url"][:70], f["problema"]] for f in fuentes],
        headers=["Fuente", "País", "URL", "Problema"],
        tablefmt="simple",
    )


def render_tabla_fuentes_solo_ia_cerradas(fuentes_cerradas: list[tuple[dict, dict]]) -> str:
    return tabulate(
        [
            [fuente["name"], fuente["country"], reconc.get("procesadas", 0), (fuente.get("rss") or fuente["url"])[:70]]
            for fuente, reconc in fuentes_cerradas
        ],
        headers=["Fuente", "País", "Artículos conciliados", "URL"],
        tablefmt="simple",
    )


def render_tabla_fuentes_solo_ia_activas(fuentes_activas: list[tuple[dict, dict]]) -> str:
    return tabulate(
        [[fuente["name"], fuente["country"], (fuente.get("rss") or fuente["url"])[:70]] for fuente, _ in fuentes_activas],
        headers=["Fuente", "País", "URL"],
        tablefmt="simple",
    )
