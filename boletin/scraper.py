"""
scraper.py
----------
Obtiene noticias de las fuentes pendientes para hoy.
Hasta 5 reintentos por fuente con backoff exponencial.
Los errores se acumulan en retrier.ErrorCollector y se reportan
al finalizar todas las iteraciones en un único email de resumen.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from bs4 import BeautifulSoup

import db
import retrier
from retrier import TipoError

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15
# MAX_INTENTOS y BACKOFF_BASE se leen en tiempo de ejecución desde retrier
# para respetar siempre el valor actual de REINTENTOS_MAX en .env


# ── Normalización ─────────────────────────────────────────────────────────────

def _parse_date(raw_date) -> str:
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


def _noticia(titulo, url, resumen, fecha_raw, fuente, pais, url_fuente="") -> dict:
    """
    url_fuente: URL de la fuente en la tabla `fuentes` de la DB.
    Distinto de url (URL de la noticia individual).
    Se usa en scorer.py para registrar_ia por fuente correctamente.
    """
    return {
        "titulo":     titulo.strip() if titulo else "",
        "url":        url.strip() if url else "",
        "url_fuente": url_fuente,
        "resumen":    resumen.strip() if resumen else "",
        "fecha":      _parse_date(fecha_raw),
        "fuente":     fuente,
        "pais":       pais,
        "score":      0,
        "titulo_en":  "",
        "resumen_en": "",
    }


# ── RSS con reintentos ────────────────────────────────────────────────────────

def _fetch_rss(source: dict) -> tuple[list[dict], bool, str]:
    """
    Parsea el feed RSS con hasta MAX_INTENTOS reintentos.
    Retorna (noticias, ok, error_msg).
    """
    def _intentar():
        feed = feedparser.parse(source["rss"], request_headers=HEADERS)
        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed malformado: {getattr(feed, 'bozo_exception', 'desconocido')}")
        noticias = []
        for entry in feed.entries[:30]:
            titulo  = entry.get("title", "")
            url     = entry.get("link", "")
            resumen = entry.get("summary", "")
            fecha   = entry.get("published_parsed") or entry.get("updated_parsed")
            if resumen and "<" in resumen:
                resumen = BeautifulSoup(resumen, "html.parser").get_text(" ", strip=True)
            if titulo and url:
                noticias.append(
                    _noticia(titulo, url, resumen[:500], fecha,
                             source["name"], source["country"],
                             url_fuente=source["url"])
                )
        return noticias

    resultado, ok = retrier.con_reintentos(
        fn=_intentar,
        tipo_error=TipoError.URL_NO_DISPONIBLE,
        fuente=source["name"],
        url=source["rss"],
    )
    if ok:
        return resultado, True, None
    return [], False, f"Falló después de {retrier.MAX_INTENTOS} intentos"


# ── Scraping con reintentos ───────────────────────────────────────────────────

async def _fetch_scrape_async(source: dict,
                               client: httpx.AsyncClient) -> tuple[list[dict], bool, str]:
    """
    Scrapea una fuente sin RSS con hasta MAX_INTENTOS reintentos.
    Retorna (noticias, ok, error_msg).
    """
    ultimo_error = ""
    for intento in range(1, retrier.MAX_INTENTOS + 1):
        try:
            r = await client.get(
                source["url"], headers=HEADERS,
                timeout=TIMEOUT, follow_redirects=True
            )
            r.raise_for_status()

            soup     = BeautifulSoup(r.text, "html.parser")
            selector = source.get("scrape_selector", "article h2 a")
            elementos = soup.select(selector) or soup.select("article a, h2 a, h3 a")

            noticias = []
            seen: set[str] = set()
            for el in elementos[:30]:
                titulo = el.get_text(strip=True)
                href   = el.get("href", "")
                if href.startswith("/"):
                    href = source["url"].rstrip("/") + href
                elif not href.startswith("http"):
                    continue
                if not titulo or href in seen:
                    continue
                seen.add(href)
                noticias.append(
                    _noticia(titulo, href, "", None,
                             source["name"], source["country"],
                             url_fuente=source["url"])
                )
            return noticias, True, None

        except Exception as e:
            ultimo_error = str(e)
            if intento < MAX_INTENTOS:
                espera = retrier.BACKOFF_BASE ** intento
                logger.warning(
                    "Intento %d/%d fallido [%s]: %s. Reintentando en %ds...",
                    intento, retrier.MAX_INTENTOS, source["name"], ultimo_error[:100], espera,
                )
                await asyncio.sleep(espera)
            else:
                logger.error(
                    "Todos los intentos agotados [%s]: %s",
                    source["name"], ultimo_error[:200],
                )

    retrier.get_collector().registrar(
        fuente=source["name"],
        url=source["url"],
        tipo=TipoError.URL_NO_DISPONIBLE,
        mensaje=ultimo_error,
        intentos=retrier.MAX_INTENTOS,
    )
    return [], False, ultimo_error


# ── Orquestación ──────────────────────────────────────────────────────────────

def fetch_all() -> list[dict]:
    """
    Obtiene noticias de todas las fuentes pendientes para hoy.
    Los errores por fuente se acumulan en retrier.ErrorCollector.
    Al finalizar, el pipeline llama a collector.enviar_resumen().
    """
    todas_las_fuentes = db.get_fuentes_activas()
    pendientes        = db.fuentes_pendientes_hoy(todas_las_fuentes)

    if not pendientes:
        logger.info("Todas las fuentes ya procesadas exitosamente hoy.")
        return []

    rss_sources    = [s for s in pendientes if s["method"] == "rss"]
    scrape_sources = [s for s in pendientes if s["method"] == "scrape"]
    todas_noticias: list[dict] = []

    # RSS — síncrono con reintentos incorporados
    for source in rss_sources:
        noticias, ok, error = _fetch_rss(source)
        db.registrar_scraping(
            url_fuente=source["url"],
            nombre=source["name"],
            ok=ok,
            noticias_obtenidas=len(noticias),
            error=error,
        )
        if ok:
            todas_noticias.extend(noticias)

    # Scraping async — paralelo, reintentos por fuente
    if scrape_sources:
        resultados = asyncio.run(_run_scrape_async(scrape_sources))
        for source, (noticias, ok, error) in zip(scrape_sources, resultados):
            db.registrar_scraping(
                url_fuente=source["url"],
                nombre=source["name"],
                ok=ok,
                noticias_obtenidas=len(noticias),
                error=error,
            )
            if ok:
                todas_noticias.extend(noticias)

    # Deduplicar por URL
    seen_urls: set[str] = set()
    unicas = []
    for n in todas_noticias:
        if n["url"] and n["url"] not in seen_urls:
            seen_urls.add(n["url"])
            unicas.append(n)

    logger.info(
        "Scraping: %d pendientes -> %d noticias unicas | %d errores acumulados",
        len(pendientes), len(unicas), retrier.get_collector().total(),
    )
    return unicas


async def _run_scrape_async(sources: list[dict]) -> list[tuple]:
    async with httpx.AsyncClient() as client:
        tasks   = [_fetch_scrape_async(s, client) for s in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    salida = []
    for r in results:
        if isinstance(r, tuple):
            salida.append(r)
        else:
            salida.append(([], False, str(r)))
    return salida
