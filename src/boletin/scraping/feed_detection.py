"""Normalización y detección de feeds para scraping."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_SUSPICIOUS_MOJIBAKE_TOKENS = ("Ã", "Â", "â€", "â€™", "â€œ", "â€“", "â€”")
_MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€\x9d": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "Â ": " ",
    "\xa0": " ",
}


def normalize_extracted_text(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    for bad, good in _MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, good)

    if any(token in cleaned for token in _SUSPICIOUS_MOJIBAKE_TOKENS):
        try:
            repaired = cleaned.encode("latin-1").decode("utf-8")
            if repaired:
                cleaned = repaired
        except Exception:
            pass

    for bad, good in _MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, good)

    return " ".join(cleaned.split())


def detectar_feed(url_base: str) -> str | None:
    """Intenta detectar automáticamente un feed RSS/Atom en un sitio."""
    rutas = ["/feed/", "/rss/", "/rss.xml", "/atom.xml", "/feed.xml"]
    try:
        for ruta in rutas:
            url_feed = url_base.rstrip("/") + ruta
            try:
                r = httpx.get(url_feed, timeout=10, follow_redirects=True)
                if r.status_code == 200 and "xml" in r.headers.get("Content-Type", ""):
                    logger.info("Feed detectado por ruta común: %s", url_feed)
                    return url_feed
            except Exception:
                continue

        r = httpx.get(url_base, timeout=10, follow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("link", rel=re.compile("alternate", re.I)):
                tipo = link.get("type", "")
                href = link.get("href", "")
                if "rss" in tipo or "atom" in tipo:
                    if href.startswith("http"):
                        logger.info("Feed detectado en <head>: %s", href)
                        return href
                    if href.startswith("/"):
                        url_feed = url_base.rstrip("/") + href
                        logger.info("Feed detectado en <head>: %s", url_feed)
                        return url_feed
        return None
    except Exception as exc:
        logger.warning("Error al detectar feed en %s: %s", url_base, exc)
        return None


def parse_date(raw_date) -> str:
    try:
        if hasattr(raw_date, "tm_year"):
            import calendar
            ts = calendar.timegm(raw_date)
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if isinstance(raw_date, str):
            return parsedate_to_datetime(raw_date).isoformat()
    except Exception:
        pass
    return datetime.now(tz=timezone.utc).isoformat()


def build_news_item(titulo, url, resumen, fecha_raw, fuente, pais, url_fuente="") -> dict:
    return {
        "titulo": normalize_extracted_text(titulo),
        "url": url.strip() if url else "",
        "url_fuente": url_fuente,
        "resumen": normalize_extracted_text(resumen),
        "fecha": parse_date(fecha_raw),
        "fuente": fuente,
        "pais": pais,
        "score": 0,
        "titulo_en": "",
        "resumen_en": "",
    }
