"""Extracción de enlaces/noticias y construcción de selectores."""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .constants import (
    BNAMERICAS_EXCLUDE_HREF_TOKENS,
    BNAMERICAS_EXCLUDE_TITLE_EXACT,
    BNAMERICAS_EXTRA_SELECTORS,
    BNAMERICAS_HOME_CONTENT_PATH_TOKENS,
    BNAMERICAS_HOME_SECTION_HEADINGS,
    ENERGIASRENOVABLES_EXTRA_SELECTORS,
    EXCLUDE_TITLE_PATTERNS,
    EXCLUDE_URL_PATTERNS,
    GENERIC_LINK_SELECTORS,
    LOGIN_SPA_WAIT_SELECTORS,
    MINERIAYDESARROLLO_EXTRA_SELECTORS,
    MINING_COM_EXTRA_SELECTORS,
    MININGDIGITAL_EXTRA_SELECTORS,
    MININGWEEKLY_EXTRA_SELECTORS,
)
from boletin.config.environment import get_scraping_settings
from .feed_detection import build_news_item, normalize_extracted_text
from .source_rules import (
    is_bnamericas_source,
    is_energiasrenovables_source,
    is_mch_source,
    is_mineriaydesarrollo_source,
    is_mining_com_source,
    is_miningdigital_source,
    is_miningweekly_source,
    is_portalminero_source,
    is_sea_source,
)

logger = logging.getLogger(__name__)

_SC = get_scraping_settings()
_MAX_ELEMENTOS_POR_SELECTOR = _SC.max_elementos_por_selector
_MAX_CANDIDATOS_TOTALES = _SC.max_candidatos_totales


def log_noticia_encontrada(source: dict, origen: str, total: int, titulo: str, url: str) -> None:
    logger.info(
        "[%s] %s -> noticia #%d encontrada | titulo='%s' | url=%s",
        source["name"],
        origen,
        total,
        (titulo or "")[:120],
        url,
    )


def split_selectors(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[\n,;]+", raw) if p.strip()]


def extract_title_from_element(element) -> str:
    title = normalize_extracted_text(element.get_text(" ", strip=True))
    if title:
        return title

    title = normalize_extracted_text(element.get("aria-label") or element.get("title") or "")
    if title:
        return title

    image = element.select_one("img[alt]")
    if image:
        title = normalize_extracted_text(image.get("alt") or "")
        if title:
            return title

    return ""


def is_bnamericas_navigation_candidate(title: str, href: str) -> tuple[bool, str]:
    title_low = (title or "").strip().lower()
    href_low = (href or "").strip().lower()
    if title_low in BNAMERICAS_EXCLUDE_TITLE_EXACT:
        return True, "bnamericas_nav_title"
    if any(token in href_low for token in BNAMERICAS_EXCLUDE_HREF_TOKENS):
        return True, "bnamericas_nav_href"
    return False, ""


def score_link_candidate(source: dict, title: str, href: str, element) -> tuple[int, str]:
    score = 0
    title_low = title.lower().strip()
    href_low = href.lower().strip()
    source_host = urlparse(source["url"]).netloc.replace("www.", "").lower()
    href_host = urlparse(href).netloc.replace("www.", "").lower()
    path = urlparse(href).path.lower()
    is_bnamericas = is_bnamericas_source(source)
    is_mineriaydesarrollo = is_mineriaydesarrollo_source(source)
    is_miningweekly = is_miningweekly_source(source)
    is_mining_com = is_mining_com_source(source)
    is_miningdigital = is_miningdigital_source(source)
    min_title_len = 8 if is_bnamericas else 18

    if not title or len(title) < min_title_len:
        return -100, f"titulo_corto(len={len(title)})"
    if any(pat in title_low for pat in EXCLUDE_TITLE_PATTERNS):
        return -50, "titulo_excluido"
    if any(pat in href_low for pat in EXCLUDE_URL_PATTERNS):
        return -80, "href_excluido"
    if is_bnamericas:
        is_nav, nav_reason = is_bnamericas_navigation_candidate(title, href)
        if is_nav:
            return -90, nav_reason

    score += min(len(title) // 12, 12)
    if is_bnamericas and len(title) >= 8:
        score += 4

    if href_host == source_host:
        score += 12
    elif href_host.endswith("." + source_host) or source_host.endswith("." + href_host):
        score += 8
    else:
        score -= 20

    if re.search(r"/20\d{2}/\d{2}/", path):
        score += 10
    if path.count("-") >= 3:
        score += 8
    if any(token in path for token in ("/noticia", "/news", "/articulo", "/article")):
        score += 6
    if is_bnamericas and any(token in path for token in ("/news", "/projects", "/companies", "/updates")):
        score += 10
    if is_bnamericas and "/article/content/" in path:
        score += 12
    if any(token in title_low for token in ("minería", "mineria", "energía", "energia", "mining", "energy", "hidrógeno", "hidrogeno")):
        score += 6
    if is_mineriaydesarrollo and "/noticias/" in path:
        score += 10
    if is_miningweekly and "/article/" in path:
        score += 12
    if is_miningdigital and any(token in path for token in ("/mining/", "/technology/", "/sustainability/", "/company/", "/projects/")):
        score += 10
    if is_mining_com and path.count("-") >= 3:
        score += 8
    if is_bnamericas and any(token in title_low for token in ("project", "projects", "news", "company", "companies")):
        score += 4

    parent_classes = " ".join(element.parent.get("class", [])) if getattr(element, "parent", None) else ""
    if any(token in parent_classes.lower() for token in ("article", "post", "entry", "news", "title")):
        score += 6
    if is_bnamericas and any(token in parent_classes.lower() for token in ("feed", "content", "update", "card", "list")):
        score += 6

    return score, "ok"


def extract_noticias_from_soup(source: dict, soup: BeautifulSoup, base_url: str, origin_label: str) -> list[dict]:
    selector_candidates = split_selectors(source.get("scrape_selector")) + GENERIC_LINK_SELECTORS
    if is_mineriaydesarrollo_source(source):
        selector_candidates += MINERIAYDESARROLLO_EXTRA_SELECTORS
    if is_miningweekly_source(source):
        selector_candidates += MININGWEEKLY_EXTRA_SELECTORS
    if is_mining_com_source(source):
        selector_candidates += MINING_COM_EXTRA_SELECTORS
    if is_miningdigital_source(source):
        selector_candidates += MININGDIGITAL_EXTRA_SELECTORS
    if is_bnamericas_source(source):
        selector_candidates += BNAMERICAS_EXTRA_SELECTORS
    if is_energiasrenovables_source(source):
        selector_candidates += ENERGIASRENOVABLES_EXTRA_SELECTORS

    seen_selector: set[str] = set()
    ordered_selectors: list[str] = []
    for selector in selector_candidates:
        if selector not in seen_selector:
            seen_selector.add(selector)
            ordered_selectors.append(selector)

    raw_candidates: list[tuple[int, str, str, str]] = []
    seen_href: set[str] = set()

    for selector in ordered_selectors:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        if elements:
            logger.info("[%s] %s -> selector '%s' encontró %d elementos", source["name"], origin_label, selector, len(elements))
        for el in elements[:_MAX_ELEMENTOS_POR_SELECTOR]:
            title = extract_title_from_element(el)
            href = (el.get("href") or "").strip()
            href = urljoin(base_url.rstrip("/") + "/", href)
            if not href or href in seen_href:
                continue
            score, reason = score_link_candidate(source, title, href, el)
            min_score = 6 if is_bnamericas_source(source) else 8
            if score < min_score:
                if is_bnamericas_source(source):
                    logger.info(
                        "[%s] %s -> candidato descartado | selector='%s' | titulo='%s' | href=%s | score=%d | motivo=%s",
                        source["name"], origin_label, selector, title[:120], href, score, reason,
                    )
                continue
            seen_href.add(href)
            raw_candidates.append((score, title, href, selector))

    raw_candidates.sort(key=lambda item: item[0], reverse=True)

    noticias: list[dict] = []
    for score, title, href, selector in raw_candidates[:_MAX_CANDIDATOS_TOTALES]:
        noticias.append(build_news_item(title, href, "", None, source["name"], source["country"], url_fuente=source["url"]))
        log_noticia_encontrada(source, f"{origin_label} [{selector}] score={score}", len(noticias), title, href)
    return noticias


def dedupe_noticias(noticias: list[dict]) -> list[dict]:
    unicas: list[dict] = []
    seen: set[str] = set()
    for noticia in noticias:
        url = (noticia.get("url") or "").strip()
        key = url.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unicas.append(noticia)
    return unicas


def extract_bnamericas_home_blocks(source: dict, soup: BeautifulSoup, base_url: str, origin_label: str) -> list[dict]:
    if not is_bnamericas_source(source):
        return []

    resultados: list[dict] = []
    for heading_text in BNAMERICAS_HOME_SECTION_HEADINGS:
        heading = soup.find(
            lambda tag: getattr(tag, "name", None) in {"h1", "h2", "h3", "h4", "div", "span"}
            and heading_text in " ".join(tag.get_text(" ", strip=True).lower().split())
        )
        if not heading:
            continue

        contenedor = heading.parent
        if not contenedor:
            continue

        anchors = contenedor.select("a[href]")
        logger.info("[%s] %s -> bloque BNamericas '%s' encontró %d anchors", source["name"], origin_label, heading_text, len(anchors))

        for anchor in anchors[:80]:
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = urljoin(base_url.rstrip("/") + "/", (anchor.get("href") or "").strip())
            href_low = href.lower()
            if not title or not href:
                continue
            if heading_text == "reportes":
                if any(token in href_low for token in ("source=sidebar", "/dashboard", "/project/home", "/newsfinder/section/all")):
                    continue
                if len(title) < 12:
                    continue
            elif heading_text == "lo mÃ¡s visto":
                if not any(token in href_low for token in ("/project/content/", "/company/content/")):
                    continue
            else:
                if not any(token in href_low for token in BNAMERICAS_HOME_CONTENT_PATH_TOKENS):
                    continue

            is_nav, nav_reason = is_bnamericas_navigation_candidate(title, href)
            if is_nav:
                logger.info("[%s] %s -> bloque '%s' descartó '%s' | href=%s | motivo=%s", source["name"], origin_label, heading_text, title[:120], href, nav_reason)
                continue

            resultados.append(build_news_item(title, href, "", None, source["name"], source["country"], url_fuente=source["url"]))

    resultados = dedupe_noticias(resultados)
    for idx, noticia in enumerate(resultados[:20], start=1):
        log_noticia_encontrada(source, f"{origin_label} [BNamericas home blocks]", idx, noticia.get("titulo") or "", noticia.get("url") or "")
    return resultados


def extract_mch_with_tabs(source: dict, soup: BeautifulSoup, base_url: str, origin_label: str) -> list[dict]:
    """
    Extrae noticias de MCH: home genérico + todas las pestañas Elementor Nested Tabs.
    Elementor renderiza el contenido de todos los tabs en el HTML inicial (solo los oculta
    con CSS), por lo que un único request HTTPX alcanza para obtenerlos todos.
    """
    noticias = extract_noticias_from_soup(source, soup, base_url, origin_label)
    seen_urls = {n["url"] for n in noticias if n.get("url")}

    # Los paneles de Elementor Nested Tabs usan id="e-n-tab-content-<ID>"
    tab_panels = soup.select("div[id^='e-n-tab-content-']")
    if not tab_panels:
        logger.info("[%s] %s -> no se encontraron paneles Elementor tabs", source["name"], origin_label)
        return noticias

    for panel in tab_panels:
        panel_id = panel.get("id", "")
        btn = soup.find("button", {"aria-controls": panel_id})
        tab_name = btn.get_text(strip=True) if btn else panel_id
        panel_count = 0

        for anchor in panel.select("a[href]"):
            title = extract_title_from_element(anchor)
            href = (anchor.get("href") or "").strip()
            href = urljoin(base_url.rstrip("/") + "/", href)
            if not href or href in seen_urls:
                continue
            score, _ = score_link_candidate(source, title, href, anchor)
            if score < 8:
                continue
            seen_urls.add(href)
            noticias.append(build_news_item(title, href, "", None, source["name"], source["country"], url_fuente=source["url"]))
            panel_count += 1
            log_noticia_encontrada(source, f"{origin_label} [tab={tab_name}]", len(noticias), title, href)

        logger.info("[%s] %s -> tab '%s': %d noticias", source["name"], origin_label, tab_name, panel_count)

    return noticias


def extract_acades_with_api(source: dict, soup: BeautifulSoup, base_url: str, origin_label: str) -> list[dict]:
    """
    Extrae noticias de ACADES combinando dos fuentes:
    1. Home page vía selector CSS (JetBlog renderizado server-side).
    2. WP REST API /wp-json/wp/v2/posts — noticias del mes actual (todos los post types).

    La sección /noticias/ usa JetEngine puro JS (no scrappeable sin browser), por
    eso se omite el scraping HTML de esa URL y se consulta la API directamente.
    """
    import httpx
    from datetime import datetime, timezone

    noticias = extract_noticias_from_soup(source, soup, base_url, origin_label)
    seen_urls: set[str] = {n["url"] for n in noticias}

    # Primer día del mes actual en UTC
    now = datetime.now(timezone.utc)
    after = f"{now.year}-{now.month:02d}-01T00:00:00"

    api_url = "https://www.acades.cl/wp-json/wp/v2/posts"
    params = {
        "per_page": min(_MAX_CANDIDATOS_TOTALES, 100),  # WP REST API max es 100
        "after": after,
        "_fields": "id,title,link,date,excerpt",
        "orderby": "date",
        "order": "desc",
    }

    try:
        r = httpx.get(api_url, params=params, timeout=20, follow_redirects=True)
        r.raise_for_status()
        posts = r.json()
        logger.info(
            "[%s] %s -> WP REST API /wp/v2/posts (after=%s): %d posts",
            source["name"], origin_label, after, len(posts),
        )
    except Exception as exc:
        logger.warning("[%s] WP REST API falló: %s", source["name"], exc)
        posts = []

    # Si el mes actual no tiene posts, ampliar al último mes completo
    if not posts:
        import calendar
        if now.month == 1:
            yr, mo = now.year - 1, 12
        else:
            yr, mo = now.year, now.month - 1
        after_fallback = f"{yr}-{mo:02d}-01T00:00:00"
        logger.info(
            "[%s] %s -> Mes actual vacío, buscando desde %s",
            source["name"], origin_label, after_fallback,
        )
        try:
            r2 = httpx.get(api_url, params={**params, "after": after_fallback, "per_page": min(_MAX_CANDIDATOS_TOTALES, 100)}, timeout=20, follow_redirects=True)
            r2.raise_for_status()
            posts = r2.json()
            logger.info(
                "[%s] %s -> Fallback API (after=%s): %d posts",
                source["name"], origin_label, after_fallback, len(posts),
            )
        except Exception as exc2:
            logger.warning("[%s] WP REST API fallback falló: %s", source["name"], exc2)

    for post in posts:
        url = (post.get("link") or "").strip()
        if not url or url in seen_urls:
            continue
        titulo_raw = (post.get("title") or {}).get("rendered") or ""
        titulo = BeautifulSoup(titulo_raw, "html.parser").get_text(" ", strip=True)
        if not titulo:
            continue
        excerpt_raw = (post.get("excerpt") or {}).get("rendered") or ""
        resumen = BeautifulSoup(excerpt_raw, "html.parser").get_text(" ", strip=True)[:500]
        seen_urls.add(url)
        noticias.append(
            build_news_item(titulo, url, resumen, None, source["name"], source["country"], url_fuente=source["url"])
        )
        log_noticia_encontrada(source, f"{origin_label} [WP API noticias/]", len(noticias), titulo, url)

    return noticias


def extract_sea_noticias(
    source: dict,
    soup: BeautifulSoup,
    base_url: str,
    origin_label: str,
    max_age_days: int = 2,
) -> list[dict]:
    """
    Extrae noticias de https://www.sea.gob.cl/noticias filtrando por antigüedad.

    Soporta dos estructuras:
    - <article> con <time datetime="ISO"> (Drupal estándar de gobierno Chile)
    - <time datetime="ISO"> fuera de <article> — se ubica el <a> más cercano en el ancestro

    Si no hay <time datetime> parseable, el artículo se incluye sin filtrar.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    noticias: list[dict] = []
    seen_href: set[str] = set()

    def _parse_dt(el) -> datetime | None:
        raw = (el.get("datetime") or "").strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _add_candidate(link_el, dt: "datetime | None") -> None:
        if dt is not None and dt < cutoff:
            return
        title = extract_title_from_element(link_el)
        href = urljoin(base_url.rstrip("/") + "/", (link_el.get("href") or "").strip())
        if not href or href in seen_href:
            return
        score, _ = score_link_candidate(source, title, href, link_el)
        if score < 0:
            return
        seen_href.add(href)
        noticias.append(
            build_news_item(title, href, "", None, source["name"], source["country"], url_fuente=source["url"])
        )
        log_noticia_encontrada(source, origin_label, len(noticias), title, href)

    articles = soup.select("article")
    if articles:
        logger.info("[%s] %s -> %d elementos <article> encontrados", source["name"], origin_label, len(articles))
        for article in articles:
            time_el = article.select_one("time[datetime]")
            dt = _parse_dt(time_el) if time_el else None
            link_el = article.select_one("h2 a[href], h3 a[href], h1 a[href], .field--type-string a[href]")
            if not link_el:
                link_el = article.select_one("a[href]")
            if link_el:
                _add_candidate(link_el, dt)
    else:
        # Fallback: busca <time datetime> en el DOM y sube al ancestro para encontrar el <a>
        time_elements = soup.select("time[datetime]")
        logger.info("[%s] %s -> sin <article>, encontró %d <time datetime>", source["name"], origin_label, len(time_elements))
        for time_el in time_elements:
            dt = _parse_dt(time_el)
            container = time_el.parent
            for _ in range(4):
                if container is None:
                    break
                link_el = container.select_one("a[href]")
                if link_el:
                    _add_candidate(link_el, dt)
                    break
                container = container.parent if hasattr(container, "parent") else None

    if not noticias:
        logger.info("[%s] %s -> sin <article>/<time>, usando extractor genérico sin filtro de fecha", source["name"], origin_label)
        noticias = extract_noticias_from_soup(source, soup, base_url, origin_label)
    else:
        logger.info(
            "[%s] %s -> %d noticias con antigüedad <= %d días",
            source["name"], origin_label, len(noticias), max_age_days,
        )
    return noticias


def extract_portalminero_wp_page(
    source: dict,
    soup: BeautifulSoup,
    base_url: str,
    origin_label: str,
    max_age_days: int = 2,
) -> list[dict]:
    """
    Extrae noticias de una página de listado WordPress de Portal Minero filtrando
    artículos con antigüedad mayor a max_age_days días.

    Estructura esperada: <article> con <time datetime="ISO"> y <h2 a> o <h3 a>.
    Si no hay <time datetime>, el artículo se incluye sin filtrar por fecha.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    noticias: list[dict] = []
    seen_href: set[str] = set()

    articles = soup.select("article")
    logger.info(
        "[%s] %s -> %d elementos <article> encontrados",
        source["name"], origin_label, len(articles),
    )

    for article in articles:
        time_el = article.select_one("time[datetime]")
        if time_el:
            try:
                raw_dt = (time_el.get("datetime") or "").strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw_dt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            except Exception:
                pass  # fecha no parseable → incluir

        link_el = article.select_one("h2 a[href], h3 a[href], h1 a[href], .entry-title a[href]")
        if not link_el:
            link_el = article.select_one("a[href]")
        if not link_el:
            continue

        title = extract_title_from_element(link_el)
        href = urljoin(base_url.rstrip("/") + "/", (link_el.get("href") or "").strip())
        if not href or href in seen_href:
            continue

        score, reason = score_link_candidate(source, title, href, link_el)
        if score < 0:
            continue

        seen_href.add(href)
        noticias.append(
            build_news_item(title, href, "", None, source["name"], source["country"], url_fuente=source["url"])
        )
        log_noticia_encontrada(source, origin_label, len(noticias), title, href)

    logger.info(
        "[%s] %s -> %d noticias con antigüedad <= %d días",
        source["name"], origin_label, len(noticias), max_age_days,
    )
    return noticias


def build_browser_wait_selectors(source: dict) -> list[str]:
    selector_candidates = split_selectors(source.get("scrape_selector")) + GENERIC_LINK_SELECTORS
    if is_mineriaydesarrollo_source(source):
        selector_candidates += MINERIAYDESARROLLO_EXTRA_SELECTORS
    if is_miningweekly_source(source):
        selector_candidates += MININGWEEKLY_EXTRA_SELECTORS
    if is_mining_com_source(source):
        selector_candidates += MINING_COM_EXTRA_SELECTORS
    if is_miningdigital_source(source):
        selector_candidates += MININGDIGITAL_EXTRA_SELECTORS
    if is_bnamericas_source(source):
        selector_candidates += BNAMERICAS_EXTRA_SELECTORS

    ordered: list[str] = []
    seen: set[str] = set()
    for selector in selector_candidates:
        selector = (selector or "").strip()
        if selector and selector not in seen:
            seen.add(selector)
            ordered.append(selector)
    return ordered


def build_login_wait_selectors(source: dict) -> list[str]:
    selectors = split_selectors(source.get("scrape_selector")) + LOGIN_SPA_WAIT_SELECTORS + GENERIC_LINK_SELECTORS
    if is_bnamericas_source(source):
        selectors += BNAMERICAS_EXTRA_SELECTORS
    ordered: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        selector = (selector or "").strip()
        if selector and selector not in seen:
            seen.add(selector)
            ordered.append(selector)
    return ordered
