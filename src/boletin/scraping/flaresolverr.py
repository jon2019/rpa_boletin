"""Integración con FlareSolverr para fuentes protegidas por Cloudflare."""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from .constants import (
    FLARESOLVERR_MAX_TIMEOUT_MS,
    FLARESOLVERR_URL,
    FLARESOLVERR_WAIT_SECONDS,
    HEADERS,
    TIMEOUT,
)
from .extraction import extract_noticias_from_soup
from .source_rules import (
    flaresolverr_enabled_for_source,
    get_scrape_target_url,
    page_looks_like_flaresolverr_challenge,
)

logger = logging.getLogger(__name__)


def scrape_with_flaresolverr_sync(source: dict, origin_label: str = "FlareSolverr") -> list[dict]:
    """
    Usa FlareSolverr como capa de acceso para fuentes con Cloudflare desafiante.
    """
    if not flaresolverr_enabled_for_source(source):
        return []

    target_url = get_scrape_target_url(source)
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": FLARESOLVERR_MAX_TIMEOUT_MS,
        "session": f"boletin-{(source.get('name') or 'source').lower().replace(' ', '-')}",
        "session_ttl_minutes": 15,
        "cookies": [],
        "returnOnlyCookies": False,
        "download": False,
        "waitInSeconds": FLARESOLVERR_WAIT_SECONDS,
    }
    logger.info(
        "[%s] Intentando acceso con FlareSolverr -> %s | payload=%s",
        source["name"],
        target_url,
        payload,
    )

    try:
        response = httpx.post(
            FLARESOLVERR_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=max(TIMEOUT, FLARESOLVERR_WAIT_SECONDS + 20),
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("[%s] FlareSolverr request falló: %s", source["name"], exc)
        return []

    data = response.json()
    if data.get("status") != "ok":
        logger.warning(
            "[%s] FlareSolverr devolvió status=%s message=%s",
            source["name"],
            data.get("status"),
            data.get("message"),
        )
        return []

    solution = data.get("solution") or {}
    response_text = solution.get("response") or ""
    final_url = solution.get("url") or target_url
    cookies = solution.get("cookies") or []
    user_agent = solution.get("userAgent") or HEADERS["User-Agent"]

    soup = BeautifulSoup(response_text, "html.parser")
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    challenge = page_looks_like_flaresolverr_challenge(response_text, title)

    logger.info(
        "[%s] FlareSolverr OK | solution_status=%s | title=%s | cookies=%d | challenge=%s | ua=%s",
        source["name"],
        data.get("status"),
        title[:120],
        len(cookies),
        challenge,
        user_agent,
    )

    if challenge:
        logger.warning("[%s] FlareSolverr aún devolvió challenge activo", source["name"])
        return []

    noticias = extract_noticias_from_soup(source, soup, final_url, origin_label)
    if noticias:
        logger.info(
            "[%s] FlareSolverr obtuvo %d noticias desde %s",
            source["name"],
            len(noticias),
            final_url,
        )
    else:
        logger.warning("[%s] FlareSolverr obtuvo HTML real pero no detectó noticias", source["name"])
    return noticias
