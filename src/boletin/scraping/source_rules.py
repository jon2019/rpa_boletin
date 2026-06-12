"""Reglas y helpers para identificar comportamientos específicos por fuente."""

from __future__ import annotations

from .constants import FLARESOLVERR_CHALLENGE_TOKENS


def is_bnamericas_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return "bnamericas" in name or "bnamericas.com" in url


_ENGLISH_SOURCE_KEYWORDS = (
    "bnamericas.com",
    "miningweekly.com",
    "mining.com",
    "miningdigital.com",
)


def is_english_source(source: dict) -> bool:
    """Devuelve True si el contenido original de la fuente está en inglés."""
    combined = " ".join([
        (source.get("url") or "").lower(),
        (source.get("post_login_url") or "").lower(),
        (source.get("name") or "").lower(),
    ])
    return any(kw in combined for kw in _ENGLISH_SOURCE_KEYWORDS)


def is_bnamericas_app_source(source: dict) -> bool:
    """Identifica la fuente app.bnamericas.com (plataforma autenticada con filtro configurable).

    Chequea url Y post_login_url porque la fuente en DB puede tener url=www.bnamericas.com
    pero post_login_url=https://app.bnamericas.com.
    """
    url = (source.get("url") or "").lower()
    post_login_url = (source.get("post_login_url") or "").lower()
    return "app.bnamericas.com" in url or "app.bnamericas.com" in post_login_url


def is_mineriaydesarrollo_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return "mineriaydesarrollo" in url or "minerÃ­a y desarrollo" in name or "mineria y desarrollo" in name


def is_miningweekly_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return "mining weekly" in name or "miningweekly.com" in url


def is_mining_com_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return name == "mining.com" or "www.mining.com" in url


def is_miningdigital_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return "miningdigital" in name or "miningdigital.com" in url


def is_rumbominero_source(source: dict) -> bool:
    name = (source.get("name") or "").lower()
    url = (source.get("url") or "").lower()
    return "rumbo minero" in name or "rumbominero.com" in url


def is_mch_source(source: dict) -> bool:
    url = (source.get("url") or "").lower()
    return "mch.cl" in url


def is_acades_source(source: dict) -> bool:
    url = (source.get("url") or "").lower()
    return "acades.cl" in url


def is_portalminero_source(source: dict) -> bool:
    url = (source.get("url") or "").lower()
    return "portalminero.com" in url


def is_energiasrenovables_source(source: dict) -> bool:
    url = (source.get("url") or "").lower()
    return "energias-renovables.com" in url


def is_sea_source(source: dict) -> bool:
    url = (source.get("url") or "").lower()
    return "sea.gob.cl" in url



def get_scrape_target_url(source: dict) -> str:
    url = (source.get("url") or "").strip()
    if is_miningweekly_source(source):
        return "https://www.miningweekly.com/page/international-home"
    if is_sea_source(source):
        return "https://www.sea.gob.cl/noticias"
    return url


def page_looks_like_flaresolverr_challenge(html: str, title: str = "") -> bool:
    sample = f"{title}\n{(html or '')[:40000]}".lower()
    return any(token in sample for token in FLARESOLVERR_CHALLENGE_TOKENS)


def flaresolverr_enabled_for_source(source: dict) -> bool:
    return is_rumbominero_source(source)


def browser_required_for_source(source: dict) -> bool:
    """Indica si la fuente requiere browser/JS rendering y debe saltear HTTPX directo."""
    return is_mining_com_source(source) or is_miningdigital_source(source)
