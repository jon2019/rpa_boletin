# ── Scraping asíncrono para fuentes sin RSS ────────────────────────────────
import asyncio
import logging
import traceback
from datetime import datetime, timezone

import feedparser
import httpx
from bs4 import BeautifulSoup

from boletin.infrastructure.db import facade as db
from boletin.infrastructure.resilience import retrier
from boletin.infrastructure.resilience.retrier import TipoError
from boletin.scraping.constants import (
    FLARESOLVERR_MAX_TIMEOUT_MS,
    FLARESOLVERR_URL,
    FLARESOLVERR_WAIT_SECONDS,
    HEADERS,
    TIMEOUT,
)
from boletin.scraping.browser import (
    patch_undetected_chromedriver_shutdown as _patch_undetected_chromedriver_shutdown,
    scrape_fallback_sync as _scrape_fallback_sync,
)
from boletin.scraping.extraction import (
    extract_acades_with_api as _extract_acades_with_api,
    extract_bnamericas_home_blocks as _extract_bnamericas_home_blocks,
    extract_mch_with_tabs as _extract_mch_with_tabs,
    extract_noticias_from_soup as _extract_noticias_from_soup,
    extract_sea_noticias as _extract_sea_noticias,
    dedupe_noticias as _dedupe_noticias,
    log_noticia_encontrada as _log_noticia_encontrada,
)
from boletin.scraping.flaresolverr import (
    scrape_with_flaresolverr_sync as _scrape_with_flaresolverr_sync,
)
from boletin.scraping.feed_detection import build_news_item as _noticia
from boletin.scraping.login import scrape_with_login as _scrape_with_login
from boletin.scraping.source_rules import (
    browser_required_for_source as _browser_required_for_source,
    flaresolverr_enabled_for_source as _flaresolverr_enabled_for_source,
    get_scrape_target_url as _get_scrape_target_url,
    is_acades_source as _is_acades_source,
    is_english_source as _is_english_source,
    is_mch_source as _is_mch_source,
    is_sea_source as _is_sea_source,
    page_looks_like_flaresolverr_challenge as _page_looks_like_flaresolverr_challenge,
)
from boletin.config.environment import get_scraping_settings as _get_scraping_settings
from boletin.scraping.cf_cookie_manager import (
    get_cf_clearance as _get_cf_clearance,
    invalidate as _invalidate_cf_clearance,
)

logger = logging.getLogger(__name__)



_patch_undetected_chromedriver_shutdown()


# ── RSS con reintentos ────────────────────────────────────────────────────────

def _fetch_rss(source: dict) -> tuple[list[dict], bool, str]:
    """
    Parsea el feed RSS con hasta MAX_INTENTOS reintentos.
    Fallback: HTTPX scraping → Selenium/Playwright scraping.
    Retorna (noticias, ok, error_msg).
    """
    def _intentar():
        try:
            feed = feedparser.parse(source["rss"], request_headers=HEADERS)
        except Exception as e:
            logger.warning(f"Error al solicitar feed {source['rss']}: {e}")
            raise
        if hasattr(feed, 'status') and feed.status not in (200, 301, 302):
            logger.warning(f"Feed {source['rss']} devolvió status {getattr(feed, 'status', 'desconocido')}")
        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed malformado: {getattr(feed, 'bozo_exception', 'desconocido')}")
        noticias = []
        max_chars = _get_scraping_settings().rss_resumen_max_chars
        for entry in feed.entries[:30]:
            titulo = entry.get("title", "")
            url = entry.get("link", "")
            resumen = ""
            if "content" in entry and entry["content"]:
                resumen = entry["content"][0].get("value", "")
            elif "summary" in entry:
                resumen = entry["summary"]
            elif "description" in entry:
                resumen = entry["description"]
            if resumen and "<" in resumen:
                resumen = BeautifulSoup(resumen, "html.parser").get_text(" ", strip=True)
            categorias = []
            if "tags" in entry:
                categorias = [t.get("term", "") for t in entry["tags"] if t.get("term")]
            autor = entry.get("author", "") or entry.get("dc_creator", "")
            fecha = entry.get("published_parsed") or entry.get("updated_parsed")
            resumen_final = resumen
            if categorias:
                resumen_final += f"\nCategorías: {', '.join(categorias)}"
            if autor:
                resumen_final += f"\nAutor: {autor}"
            if titulo and url:
                noticias.append(
                    _noticia(titulo, url, resumen_final[:max_chars], fecha,
                             source["name"], source["country"],
                             url_fuente=source["url"])
                )
                _log_noticia_encontrada(source, f"RSS {source['rss']}", len(noticias), titulo, url)
            else:
                logger.warning(f"RSS entry missing title or url: {entry}")
        if not noticias:
            logger.warning(f"Feed {source['rss']} no devolvió noticias.")
        return noticias

    resultado, ok, _ = retrier.con_reintentos(
        fn=_intentar,
        tipo_error=TipoError.URL_NO_DISPONIBLE,
        fuente=source["name"],
        url=source["rss"],
        registrar_en_collector=False,
    )
    if ok:
        return resultado, True, None

    if _flaresolverr_enabled_for_source(source):
        logger.info("RSS falló para [%s], intentando FlareSolverr...", source["name"])
        try:
            noticias_fs = _scrape_with_flaresolverr_sync(source, "FlareSolverr RSS fallback")
            if noticias_fs:
                logger.info("FlareSolverr exitoso para [%s]: %d noticias", source["name"], len(noticias_fs))
                return noticias_fs, True, None
            logger.warning("FlareSolverr no encontró noticias para [%s]", source["name"])
        except Exception as e:
            logger.warning("FlareSolverr falló para [%s]: %s", source["name"], str(e))

    # Fallback 1: HTTPX (usa scrape_selector si está configurado, o selectores genéricos si no)
    logger.info("RSS falló para [%s], intentando fallback HTTPX...", source["name"])
    try:
        r = httpx.get(_get_scrape_target_url(source), headers=HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        _extractor = _extract_mch_with_tabs if _is_mch_source(source) else (_extract_sea_noticias if _is_sea_source(source) else (_extract_acades_with_api if _is_acades_source(source) else _extract_noticias_from_soup))
        noticias = _extractor(
            source,
            soup,
            str(r.url),
            "HTTPX fallback",
        )
        if noticias:
            logger.info("HTTPX scraping exitoso para [%s]: %d noticias", source["name"], len(noticias))
            return noticias, True, None
        else:
            logger.warning("HTTPX scraping no encontró noticias para [%s]", source["name"])
    except Exception as e:
        logger.warning("HTTPX scraping falló para [%s]: %s", source["name"], str(e))

    # Fallback 2: Selenium/Playwright
    logger.info("HTTPX falló para [%s], intentando Selenium/Playwright...", source["name"])
    try:
        fallback_resultado = _scrape_fallback_sync(
            source,
            flaresolverr_scraper=_scrape_with_flaresolverr_sync,
        )
        if fallback_resultado:
            logger.info("Fallback Selenium/Playwright exitoso para [%s]: %d noticias", source["name"], len(fallback_resultado))
            return fallback_resultado, True, None
        else:
            logger.warning("Fallback Selenium/Playwright no encontró noticias para [%s]", source["name"])
    except Exception as e:
        logger.warning("Fallback Selenium/Playwright falló para [%s]: %s", source["name"], str(e))

    return [], False, "Todos los métodos de scraping fallaron"


# ── Scraping asíncrono para fuentes sin RSS ────────────────────────────────

def _scrape_with_curl_cffi(source: dict, origin_label: str = "curl_cffi") -> list[dict]:
    """
    Scraping con curl_cffi: impersona el TLS/HTTP2 fingerprint de Chrome real.
    Si hay cookie cf_clearance en el .env, la inyecta para saltear el challenge.
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.warning("[%s] curl_cffi no instalado", source["name"])
        return []

    target_url = _get_scrape_target_url(source)

    cf = _get_cf_clearance(target_url)
    cookies = {}
    headers = dict(HEADERS)
    proxies = {}
    if cf:
        cf_clearance, user_agent, proxy = cf
        cookies["cf_clearance"] = cf_clearance
        headers["User-Agent"] = user_agent
        if proxy:
            proxies = {"http": proxy, "https": proxy}
        logger.info("[%s] curl_cffi: cf_clearance disponible%s", source["name"], " (con proxy)" if proxy else "")
    else:
        logger.info("[%s] curl_cffi: sin cf_clearance, intentando sin cookie", source["name"])

    try:
        r = curl_requests.get(
            target_url,
            impersonate="chrome120",
            headers=headers,
            cookies=cookies,
            proxies=proxies or None,
            timeout=20,
            allow_redirects=True,
        )
        r.raise_for_status()
    except Exception as exc:
        logger.warning("[%s] curl_cffi falló: %s", source["name"], exc)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if _page_looks_like_flaresolverr_challenge(r.text, title):
        logger.warning("[%s] curl_cffi: challenge activo — invalidando cf_clearance", source["name"])
        _invalidate_cf_clearance(target_url)
        return []

    _extractor = _extract_mch_with_tabs if _is_mch_source(source) else (_extract_sea_noticias if _is_sea_source(source) else _extract_noticias_from_soup)
    noticias = _extractor(source, soup, str(r.url), origin_label)
    if noticias:
        logger.info("[%s] curl_cffi obtuvo %d noticias", source["name"], len(noticias))
    else:
        logger.warning("[%s] curl_cffi obtuvo HTML real pero sin noticias detectadas", source["name"])
    return noticias


async def _fetch_scrape_async(source: dict, client: httpx.AsyncClient) -> tuple[list[dict], bool, str]:
    """
    Realiza scraping asíncrono de una fuente usando httpx y BeautifulSoup.
    Devuelve (noticias, ok, error_msg).
    """
    if _flaresolverr_enabled_for_source(source):
        try:
            noticias_cffi = await asyncio.to_thread(_scrape_with_curl_cffi, source, "curl_cffi")
            if noticias_cffi:
                return noticias_cffi, True, None
            logger.warning("[%s] curl_cffi no obtuvo noticias. Intentando FlareSolverr...", source["name"])
        except Exception as e:
            logger.warning("[%s] curl_cffi error: %s. Intentando FlareSolverr...", source["name"], e)

        try:
            noticias_fs = await asyncio.to_thread(
                _scrape_with_flaresolverr_sync,
                source,
                "FlareSolverr async",
            )
            if noticias_fs:
                return noticias_fs, True, None
            logger.warning("[%s] FlareSolverr async no encontró noticias. Reintentando con HTTPX directo...", source["name"])
        except Exception as e:
            logger.warning("[%s] FlareSolverr async falló: %s. Reintentando con HTTPX directo...", source["name"], e)

    if _browser_required_for_source(source):
        logger.info("[%s] Sitio requiere JS rendering — saltando HTTPX, usando browser fallback", source["name"])
        return [], False, "browser required (JS rendering)"

    try:
        r = await client.get(_get_scrape_target_url(source), headers=HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        _extractor = _extract_mch_with_tabs if _is_mch_source(source) else (_extract_sea_noticias if _is_sea_source(source) else (_extract_acades_with_api if _is_acades_source(source) else _extract_noticias_from_soup))
        noticias = _extractor(
            source,
            soup,
            str(r.url),
            "Scrape async",
        )
        if noticias:
            return noticias, True, None
        else:
            return [], False, "Sin noticias encontradas"
    except Exception as e:
        err = f"Scraping asíncrono falló para [{source['name']}]: {e}\n{traceback.format_exc()}"
        return [], False, err


async def _run_scrape_async(sources: list[dict]) -> list[tuple]:
    """
    Ejecuta scraping asíncrono en paralelo para múltiples fuentes.
    """
    async with httpx.AsyncClient() as client:
        tasks = [_fetch_scrape_async(s, client) for s in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    salida = []
    for r in results:
        if isinstance(r, tuple):
            salida.append(r)
        else:
            salida.append(([], False, str(r)))
    return salida


# ── Pipeline principal ────────────────────────────────────────────────────────

def scrape_all(login_sources, rss_sources, scrape_sources, pendientes, fecha_efectiva=None):
    """
    Ejecuta el pipeline de scraping completo:
    - Fuentes con login (usuario+clave): Playwright autenticado
    - Fuentes con RSS: reintentos + fallbacks HTTPX / Selenium
    - Fuentes sin RSS: scraping asíncrono paralelo
    - Deduplicación y logging
    """
    todas_noticias = []
    failed_sources = []

    # Regla de negocio: si tiene usuario+clave SIEMPRE va por login.
    # Si tiene url_rss configurada va por RSS (permite maximizar cobertura en fuentes bloqueadas por anti-bot).
    # El resto va por scraping HTML asíncrono.
    todas_fuentes = login_sources + rss_sources + scrape_sources
    fuentes_con_login = [f for f in todas_fuentes if f.get("usuario") and f.get("clave")]
    fuentes_con_rss = [
        f for f in todas_fuentes
        if not (f.get("usuario") and f.get("clave")) and (f.get("rss") or "").strip()
    ]
    fuentes_sin_rss = [
        f for f in todas_fuentes
        if not (f.get("usuario") and f.get("clave")) and not (f.get("rss") or "").strip()
    ]

    # ── Login scraping — autenticado con Playwright ───────────────────────────
    for source in fuentes_con_login:
        logger.info("Login scraping para [%s] (%s)", source["name"], source["url"])
        try:
            noticias, login_error = _scrape_with_login(source)
        except Exception as exc:
            logger.warning("[%s] Login scraping lanzó excepción: %s", source["name"], exc)
            noticias, login_error = [], str(exc)
        ok    = bool(noticias)
        error = None if ok else (login_error or "Login scraping no obtuvo noticias")
        db.registrar_scraping(
            url_fuente=source["url"],
            nombre=source["name"],
            ok=ok,
            noticias_obtenidas=len(noticias),
            error=error,
            fecha=fecha_efectiva,
        )
        log_id = db.registrar_scraping_log(
            url_fuente=source["url"],
            fecha=fecha_efectiva,
            ok=ok,
            noticias=noticias,
            error=error,
        )
        if ok:
            _en = _is_english_source(source)
            for n in noticias:
                if log_id:
                    n["_scraping_log_id"] = log_id
                if _en:
                    n["idioma_original"] = "en"
            todas_noticias.extend(noticias)
            if fecha_efectiva:
                db.guardar_articulos_pendientes(source["url"], noticias, fecha_efectiva)
        else:
            err_msg = error or "Sin noticias obtenidas"
            failed_sources.append({
                "pais":    source["country"],
                "nombre":  source["name"],
                "url":     source["url"],
                "metodo":  "Login",
                "error":   err_msg,
                "usuario": source.get("usuario", ""),
            })
            retrier.get_collector().registrar(
                fuente=source["name"],
                url=source["url"],
                tipo=retrier.TipoError.URL_NO_DISPONIBLE,
                mensaje=err_msg,
                intentos=1,
                tecnologias="Login",
            )

    # ── RSS — síncrono con reintentos incorporados ────────────────────────────
    for source in fuentes_con_rss:
        noticias, ok, error = _fetch_rss(source)
        db.registrar_scraping(
            url_fuente=source["url"],
            nombre=source["name"],
            ok=ok,
            noticias_obtenidas=len(noticias),
            error=error,
            fecha=fecha_efectiva,
        )
        log_id = db.registrar_scraping_log(
            url_fuente=source["url"],
            fecha=fecha_efectiva,
            ok=ok,
            noticias=noticias,
            error=error,
        )
        if ok:
            _en = _is_english_source(source)
            for n in noticias:
                if log_id:
                    n["_scraping_log_id"] = log_id
                if _en:
                    n["idioma_original"] = "en"
            todas_noticias.extend(noticias)
            if fecha_efectiva:
                db.guardar_articulos_pendientes(source["url"], noticias, fecha_efectiva)
        else:
            err_msg = error or "Sin noticias obtenidas"
            failed_sources.append({
                "pais":    source["country"],
                "nombre":  source["name"],
                "url":     source["url"],
                "metodo":  "RSS + Fallback",
                "error":   err_msg,
                "usuario": source.get("usuario", ""),
            })
            retrier.get_collector().registrar(
                fuente=source["name"],
                url=source["url"],
                tipo=retrier.TipoError.URL_NO_DISPONIBLE,
                mensaje=err_msg,
                intentos=1,
                tecnologias="RSS + Fallback",
            )

    # ── Scraping async — paralelo, reintentos por fuente ─────────────────────
    if fuentes_sin_rss:
        resultados = asyncio.run(_run_scrape_async(fuentes_sin_rss))
        for source, (noticias, ok, error) in zip(fuentes_sin_rss, resultados):
            # Fallback UC Chrome si HTTPX falla (403, Cloudflare, anti-bot)
            if not ok:
                es_cloudflare = error and any(
                    kw in str(error).lower()
                    for kw in ("403", "forbidden", "cloudflare", "just a moment", "429")
                )
                if es_cloudflare or not noticias:
                    logger.info(
                        "[%s] HTTPX falló (%s) — intentando Playwright/UC Chrome...",
                        source["name"], (error or "sin noticias")[:80],
                    )
                    try:
                        # Si FlareSolverr ya corrió en la fase async, no repetirlo
                        flaresolverr_para_fallback = (
                            None if _flaresolverr_enabled_for_source(source)
                            else _scrape_with_flaresolverr_sync
                        )
                        noticias_uc = _scrape_fallback_sync(
                            source,
                            flaresolverr_scraper=flaresolverr_para_fallback,
                        )
                        if noticias_uc:
                            noticias = noticias_uc
                            ok = True
                            error = None
                            logger.info(
                                "[%s] Playwright/UC Chrome exitoso: %d noticias",
                                source["name"], len(noticias),
                            )
                        else:
                            logger.warning("[%s] Playwright/UC Chrome también falló", source["name"])
                    except Exception as _e:
                        logger.warning("[%s] Playwright/UC Chrome error: %s", source["name"], _e)

            db.registrar_scraping(
                url_fuente=source["url"],
                nombre=source["name"],
                ok=ok,
                noticias_obtenidas=len(noticias),
                error=error,
                fecha=fecha_efectiva,
            )
            log_id = db.registrar_scraping_log(
                url_fuente=source["url"],
                fecha=fecha_efectiva,
                ok=ok,
                noticias=noticias,
                error=error,
            )
            if ok:
                _en = _is_english_source(source)
                for n in noticias:
                    if log_id:
                        n["_scraping_log_id"] = log_id
                    if _en:
                        n["idioma_original"] = "en"
                todas_noticias.extend(noticias)
                if fecha_efectiva:
                    db.guardar_articulos_pendientes(source["url"], noticias, fecha_efectiva)
            else:
                err_msg = error or "Sin noticias obtenidas"
                failed_sources.append({
                    "pais":    source["country"],
                    "nombre":  source["name"],
                    "url":     source["url"],
                    "metodo":  "Scraping",
                    "error":   err_msg,
                    "usuario": source.get("usuario", ""),
                })
                retrier.get_collector().registrar(
                    fuente=source["name"],
                    url=source["url"],
                    tipo=retrier.TipoError.URL_NO_DISPONIBLE,
                    mensaje=err_msg,
                    intentos=1,
                    tecnologias="Scraping",
                )

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
    return unicas, failed_sources
