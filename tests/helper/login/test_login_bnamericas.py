"""
Diagnóstico de login + render SPA + detección de noticias para BNamericas.

Ejecutar desde boletin/:
    python test_login_bnamericas.py

Genera:
- screenshots en tests/output/login/debug_bnamericas_*.png
- log en tests/output/login/test_login_bnamericas.log
- resultado auditable en tests/output/login/test_login_bnamericas_resultado.json
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


load_dotenv(ROOT_DIR / '.env')
log = logging.getLogger("test_bnamericas")

SCREENSHOTS_DIR = ROOT_DIR / "tests" / "output" / "login"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

RESULTADO_JSON_PATH = SCREENSHOTS_DIR / "test_login_bnamericas_resultado.json"
DEFAULT_LOGIN_URL = "https://app.bnamericas.com/login"
DEFAULT_POST_LOGIN_URL = "https://app.bnamericas.com/home"

LOGIN_SUCCESS_SELECTORS = [
    "input[placeholder*='Buscar' i]",
    "input[placeholder*='Search' i]",
    "a[href='/home']",
    "a[href='/news']",
    "div[id='root']",
    "button:has-text('Tus preguntas')",
    "text=Hola",
]

BNAMERICAS_NEWS_SELECTORS = [
    "h3.article-title a",
    "a[href*='/article']",
    "a[href*='/project']",
    "a[href*='/company']",
    "a[href*='/update']",
    "[class*='feed'] a",
    "[class*='content'] a",
    "[class*='update'] a",
    "[class*='card'] a",
    "[class*='list'] a",
    "[class*='news'] a",
    "[class*='story'] a",
    "main a",
]


BNAMERICAS_EXCLUDE_TITLE_EXACT = {
    "noticias",
    "news",
    "proyectos",
    "projects",
    "compa??as",
    "companias",
    "companies",
    "actualizaciones",
    "updates",
}
BNAMERICAS_EXCLUDE_HREF_TOKENS = (
    "/article/section/all",
    "source=sidebar",
    "listtype=section",
    "contenttype=article",
)


def setup_logging() -> Path:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in root.handlers
    ):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    log_file = SCREENSHOTS_DIR / "test_login_bnamericas.log"
    existing_file_handler = next(
        (
            h for h in root.handlers
            if isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == log_file
        ),
        None,
    )
    if existing_file_handler is None:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8", mode="w")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return log_file


def _guardar_resultado_json(resultado: dict) -> None:
    RESULTADO_JSON_PATH.write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Resultado JSON guardado: %s", RESULTADO_JSON_PATH)


def _get_credenciales() -> tuple[str, str, str, str]:
    try:
        from boletin import db
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT usuario, clave, login_url, post_login_url
                    FROM fuentes
                    WHERE lower(nombre) LIKE '%bnamericas%'
                      AND usuario IS NOT NULL
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        if row:
            usuario, clave, login_db, post_db = row
            log.info(
                "Credenciales leídas desde DB — usuario: %s | login_url: %s | post_login_url: %s",
                usuario,
                login_db,
                post_db,
            )
            return usuario, clave, login_db or DEFAULT_LOGIN_URL, post_db or DEFAULT_POST_LOGIN_URL
    except Exception as exc:
        log.warning("No se pudo leer credenciales desde DB: %s", exc)

    usuario = input("Usuario BNamericas: ").strip()
    clave = input("Clave: ").strip()
    return usuario, clave, DEFAULT_LOGIN_URL, DEFAULT_POST_LOGIN_URL


def _screenshot(page, nombre: str) -> None:
    path = SCREENSHOTS_DIR / f"debug_bnamericas_{nombre}.png"
    page.screenshot(path=str(path), full_page=False)
    log.info("Screenshot guardado: %s", path)


def _page_looks_like_spa_shell(html: str) -> bool:
    sample = (html or "")[:5000].lower()
    return (
        "you need to enable javascript to run this app" in sample
        or '<div id="root"></div>' in sample
        or 'id="root"></div>' in sample
    )


def _wait_for_content(page, resultado: dict) -> None:
    for ronda in range(1, 6):
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

        html = page.content()
        looks_like_shell = _page_looks_like_spa_shell(html)
        resultado["html_shell_detectado"] = bool(resultado.get("html_shell_detectado") or looks_like_shell)
        if looks_like_shell:
            log.info("[BNamericas] La página aún parece shell SPA en ronda %d", ronda)

        for selector in LOGIN_SUCCESS_SELECTORS + BNAMERICAS_NEWS_SELECTORS:
            try:
                count = page.locator(selector).count()
            except Exception:
                continue
            if count > 0:
                log.info("[BNamericas] Selector '%s' detectó %d nodos en ronda %d", selector, count, ronda)
                return

        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass
        page.wait_for_timeout(2_000)


def _es_link_articulo_valido(titulo: str, href: str) -> tuple[bool, str]:
    titulo_low = (titulo or "").strip().lower()
    href_low = (href or "").strip().lower()

    if not titulo or len(titulo.strip()) < 12:
        return False, "titulo_corto"
    if titulo_low in BNAMERICAS_EXCLUDE_TITLE_EXACT:
        return False, "titulo_navegacion"
    if any(token in href_low for token in BNAMERICAS_EXCLUDE_HREF_TOKENS):
        return False, "href_navegacion"
    if "/article/content/" in href_low:
        return True, "article_content"
    if "/article/section/all/content/" in href_low:
        return True, "article_section_content"
    if "/article/" in href_low and "source=home_bna" in href_low:
        return True, "article_home"
    if any(token in href_low for token in ("/project/", "/company/", "/update/")):
        return True, "entity_content"
    return False, "href_no_prioritario"


def _extraer_candidatos_selector(page, selector: str, max_items: int = 20) -> list[dict]:
    candidatos: list[dict] = []
    try:
        count = page.locator(selector).count()
    except Exception:
        return candidatos
    if count <= 0:
        return candidatos

    log.info("Selector '%s' encontró %d links", selector, count)
    for i in range(min(max_items, count)):
        loc = page.locator(selector).nth(i)
        titulo = (loc.text_content() or "").strip()
        href = loc.get_attribute("href") or ""
        if not titulo and not href:
            continue
        es_valido, motivo = _es_link_articulo_valido(titulo, href)
        if not es_valido:
            log.info("  [descartado %d] %s -> %s | motivo=%s", i + 1, titulo[:120], href, motivo)
            continue
        candidatos.append(
            {
                "titulo": titulo[:160],
                "url": href,
                "selector": selector,
            }
        )
        log.info("  [%d] %s -> %s", i + 1, titulo[:120], href)

    return candidatos


def _deduplicar_candidatos(base_url: str, candidatos: list[dict]) -> list[dict]:
    unicos: list[dict] = []
    seen: set[str] = set()
    for item in candidatos:
        href = (item.get("url") or "").strip()
        url_abs = urljoin(base_url.rstrip("/") + "/", href)
        key = url_abs.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unicos.append(
            {
                "titulo": item.get("titulo", ""),
                "url": url_abs,
                "selector": item.get("selector"),
            }
        )
    return unicos


def _detectar_noticias(page) -> tuple[str | None, int, list[dict]]:
    for selector in BNAMERICAS_NEWS_SELECTORS:
        noticias_preview = _extraer_candidatos_selector(page, selector)
        noticias_preview = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, noticias_preview)
        if noticias_preview:
            return selector, len(noticias_preview), noticias_preview

    return None, 0, []


def _detectar_links_visibles_home(page) -> tuple[str | None, int, list[dict]]:
    """
    Fallback para BNamericas: inspecciona anchors visibles reales en /home
    y se queda con los que tienen título útil y href no trivial.
    """
    selectors = [
        "main a",
        "[role='main'] a",
        "#root a",
        "a",
    ]
    href_descartados = (
        "#",
        "javascript:",
        "mailto:",
        "tel:",
    )
    palabras_descartar = (
        "hola",
        "editar",
        "tus preguntas",
        "buscar",
        "cerrar sesión",
        "logout",
    )

    for selector in selectors:
        try:
            count = page.locator(selector).count()
        except Exception:
            continue
        if count <= 0:
            continue

        candidatos: list[dict] = []
        for i in range(min(count, 120)):
            loc = page.locator(selector).nth(i)
            try:
                titulo = (loc.text_content() or "").strip()
                href = (loc.get_attribute("href") or "").strip()
            except Exception:
                continue

            titulo_low = titulo.lower()
            href_low = href.lower()
            if any(x in href_low for x in href_descartados):
                continue
            if any(x == titulo_low or x in titulo_low for x in palabras_descartar):
                continue
            if href_low in ("", "/", "/home"):
                continue
            es_valido, motivo = _es_link_articulo_valido(titulo, href)
            if not es_valido:
                log.info("  [fallback descartado] %s -> %s | motivo=%s", titulo[:120], href, motivo)
                continue

            candidatos.append(
                {
                    "titulo": titulo[:160],
                    "url": href,
                    "selector": selector,
                }
            )

        if candidatos:
            candidatos = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, candidatos)
            log.info("Fallback home '%s' encontró %d links útiles", selector, len(candidatos))
            for idx, item in enumerate(candidatos[:10], start=1):
                log.info("  [home %d] %s -> %s", idx, item['titulo'][:120], item['url'])
            return selector, len(candidatos), candidatos[:10]

    return None, 0, []


def _detectar_noticias_con_scroll(page) -> tuple[str | None, int, list[dict]]:
    acumulados: list[dict] = []
    selector_ganador: str | None = None

    for ronda in range(1, 5):
        log.info("BNamericas extracción ronda %d/4", ronda)
        selector, total, preview = _detectar_noticias(page)
        if preview:
            if selector_ganador is None:
                selector_ganador = selector
            acumulados.extend(preview)
            acumulados = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, acumulados)
            log.info("BNamericas ronda %d acumuló %d artículos únicos", ronda, len(acumulados))

        try:
            page.mouse.wheel(0, 1800)
        except Exception:
            pass
        page.wait_for_timeout(2_500)

    if acumulados:
        return selector_ganador, len(acumulados), acumulados[:10]

    return _detectar_links_visibles_home(page)


def _extraer_links_por_heading(page, heading_text: str, max_items: int = 12) -> list[dict]:
    """
    Intenta encontrar un bloque visual a partir de un heading visible y luego
    extrae anchors útiles dentro de su contenedor más cercano.
    """
    selectors_heading = [
        f"text={heading_text}",
        f"h1:has-text('{heading_text}')",
        f"h2:has-text('{heading_text}')",
        f"h3:has-text('{heading_text}')",
        f"h4:has-text('{heading_text}')",
        f"div:has-text('{heading_text}')",
        f"span:has-text('{heading_text}')",
    ]

    for selector in selectors_heading:
        try:
            heading = page.locator(selector).first
            if heading.count() <= 0:
                continue
        except Exception:
            continue

        log.info("Bloque '%s' detectado con selector heading: %s", heading_text, selector)
        container_selectors = [
            "xpath=ancestor::section[1]",
            "xpath=ancestor::div[contains(@class,'card')][1]",
            "xpath=ancestor::div[contains(@class,'panel')][1]",
            "xpath=ancestor::div[1]",
        ]

        for container_selector in container_selectors:
            try:
                container = heading.locator(container_selector).first
                link_count = container.locator("a").count()
            except Exception:
                continue
            if link_count <= 0:
                continue

            encontrados: list[dict] = []
            for i in range(min(link_count, max_items * 3)):
                loc = container.locator("a").nth(i)
                titulo = (loc.text_content() or "").strip()
                href = (loc.get_attribute("href") or "").strip()
                if not titulo or not href:
                    continue
                if href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
                    continue
                encontrados.append(
                    {
                        "titulo": titulo[:180],
                        "url": urljoin(page.url.rstrip("/") + "/", href),
                        "heading": heading_text,
                    }
                )

            if encontrados:
                dedup = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, encontrados)
                log.info(
                    "Bloque '%s' con contenedor %s encontró %d links",
                    heading_text,
                    container_selector,
                    len(dedup),
                )
                return dedup[:max_items]

    return []


def _extraer_items_feed_noticias(page, max_items: int = 12) -> list[dict]:
    """
    Extracción especializada para el bloque visual 'Feed de noticias y cambios'.
    Busca el heading, acota al panel contiguo y luego intenta leer entradas
    repetidas del feed, no solo anchors sueltos.
    """
    heading_candidates = [
        "text=Feed de noticias y cambios",
        "h1:has-text('Feed de noticias y cambios')",
        "h2:has-text('Feed de noticias y cambios')",
        "h3:has-text('Feed de noticias y cambios')",
        "div:has-text('Feed de noticias y cambios')",
    ]

    for heading_selector in heading_candidates:
        try:
            heading = page.locator(heading_selector).first
            if heading.count() <= 0:
                continue
        except Exception:
            continue

        log.info("Feed principal detectado con heading: %s", heading_selector)
        panel_candidates = [
            "xpath=ancestor::div[1]/following-sibling::div[1]",
            "xpath=ancestor::div[contains(@class,'card')][1]",
            "xpath=ancestor::section[1]",
            "xpath=ancestor::div[2]",
        ]

        for panel_selector in panel_candidates:
            try:
                panel = heading.locator(panel_selector).first
                panel_text = (panel.text_content() or "").strip()
            except Exception:
                continue
            if not panel_text or len(panel_text) < 30:
                continue

            link_count = 0
            try:
                link_count = panel.locator("a").count()
            except Exception:
                pass
            if link_count <= 0:
                continue

            encontrados: list[dict] = []
            for i in range(min(link_count, max_items * 4)):
                loc = panel.locator("a").nth(i)
                titulo = (loc.text_content() or "").strip()
                href = (loc.get_attribute("href") or "").strip()
                if not titulo or not href:
                    continue
                if href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
                    continue
                if len(titulo) < 6:
                    continue

                es_valido, motivo = _es_link_articulo_valido(titulo, href)
                if not es_valido:
                    # Para el feed principal aceptamos también proyectos/entidades visibles
                    href_low = href.lower()
                    if not any(token in href_low for token in ("/project/content/", "/company/content/", "/article/content/", "/article/section/all/content/")):
                        log.info("  [feed descartado] %s -> %s | motivo=%s", titulo[:120], href, motivo)
                        continue

                encontrados.append(
                    {
                        "titulo": titulo[:180],
                        "url": urljoin(page.url.rstrip("/") + "/", href),
                        "heading": "Feed de noticias y cambios",
                    }
                )

            encontrados = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, encontrados)
            if encontrados:
                log.info("Feed principal encontró %d links útiles", len(encontrados))
                return encontrados[:max_items]

    return []


def _extraer_items_reportes(page, max_items: int = 8) -> list[dict]:
    """
    Extracción especializada para 'Reportes' evitando capturar menú/sidebar.
    """
    heading_candidates = [
        "text=Reportes",
        "h1:has-text('Reportes')",
        "h2:has-text('Reportes')",
        "h3:has-text('Reportes')",
        "div:has-text('Reportes')",
    ]

    for heading_selector in heading_candidates:
        try:
            heading = page.locator(heading_selector).first
            if heading.count() <= 0:
                continue
        except Exception:
            continue

        log.info("Bloque Reportes detectado con heading: %s", heading_selector)
        panel_candidates = [
            "xpath=ancestor::div[contains(@class,'card')][1]",
            "xpath=ancestor::section[1]",
            "xpath=ancestor::div[1]",
        ]

        for panel_selector in panel_candidates:
            try:
                panel = heading.locator(panel_selector).first
                link_count = panel.locator("a").count()
            except Exception:
                continue
            if link_count <= 0:
                continue

            encontrados: list[dict] = []
            for i in range(min(link_count, max_items * 3)):
                loc = panel.locator("a").nth(i)
                titulo = (loc.text_content() or "").strip()
                href = (loc.get_attribute("href") or "").strip()
                href_low = href.lower()
                titulo_low = titulo.lower()

                if not titulo or not href:
                    continue
                if href.startswith("#") or href_low.startswith(("javascript:", "mailto:", "tel:")):
                    continue
                if any(token in href_low for token in ("source=sidebar", "/dashboard", "/project/home", "/section/all?source=sidebar", "/newsfinder/section/all")):
                    continue
                if titulo_low in {"inicio", "dashboard", "nuevo dashboard", "noticias", "factiva", "proyectos", "todos", "mapa", "forecast"}:
                    continue
                if len(titulo) < 12:
                    continue

                encontrados.append(
                    {
                        "titulo": titulo[:180],
                        "url": urljoin(page.url.rstrip("/") + "/", href),
                        "heading": "Reportes",
                    }
                )

            encontrados = _deduplicar_candidatos(page.url or DEFAULT_POST_LOGIN_URL, encontrados)
            if encontrados:
                log.info("Reportes encontró %d links útiles", len(encontrados))
                return encontrados[:max_items]

    return []


def run_diagnostico():
    setup_logging()
    usuario, clave, login_url, post_login_url = _get_credenciales()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "login_url": login_url,
        "post_login_url": post_login_url,
        "usuario_input": usuario,
        "login_exitoso": False,
        "url_final": "",
        "titulo_final": "",
        "html_shell_detectado": False,
        "reintento_news_ejecutado": False,
        "selector_ganador": None,
        "noticias_encontradas": 0,
        "noticias_preview": [],
        "feed_principal_encontrado": 0,
        "feed_principal_preview": [],
        "reportes_encontrados": 0,
        "reportes_preview": [],
        "mas_visto_encontrado": 0,
        "mas_visto_preview": [],
        "estado": "iniciado",
        "error": None,
    }

    log.info("=== INICIO DIAGNOSTICO BNAMERICAS ===")
    log.info("LOGIN URL      : %s", login_url)
    log.info("POST LOGIN URL : %s", post_login_url)
    log.info("Usuario        : %s", usuario)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()

        try:
            log.info("--- Paso 1: Navegar a login ---")
            page.goto(login_url, timeout=30_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            log.info("URL actual: %s | Titulo: %s", page.url, page.title())
            _screenshot(page, "1_login_page")

            user_selectors = [
                "input[name='email']",
                "input[type='email']",
                "input[name='username']",
                "input[name='user']",
                "input[id='email']",
                "input[id='username']",
                "input[placeholder*='mail' i]",
                "input[placeholder*='usuario' i]",
            ]
            pass_selectors = [
                "input[type='password']",
                "input[name='password']",
                "input[id='password']",
            ]
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Ingresar')",
                "button:has-text('Login')",
                "button:has-text('Log in')",
                "button:has-text('Sign in')",
                "button:has-text('Acceder')",
            ]

            user_ok = False
            for sel in user_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(usuario)
                    log.info("Campo usuario rellenado con selector: %s", sel)
                    user_ok = True
                    break
            if not user_ok:
                raise RuntimeError("No se encontró campo de usuario para BNamericas")

            pass_ok = False
            for sel in pass_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(clave)
                    log.info("Campo contraseña rellenado con selector: %s", sel)
                    pass_ok = True
                    break
            if not pass_ok:
                raise RuntimeError("No se encontró campo de contraseña para BNamericas")

            _screenshot(page, "2_form_filled")

            submitted = False
            for sel in submit_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click()
                    log.info("Formulario enviado con selector: %s", sel)
                    submitted = True
                    break
            if not submitted:
                log.warning("No se encontró botón submit; presionando Enter")
                page.keyboard.press("Enter")

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(4_000)
            _screenshot(page, "3_post_submit")

            current_url = page.url
            current_title = page.title()
            log.info("URL tras submit: %s | Titulo: %s", current_url, current_title)

            if current_url.rstrip("/") != login_url.rstrip("/"):
                resultado["login_exitoso"] = True

            if post_login_url.rstrip("/") != current_url.rstrip("/"):
                log.info("Navegando a post_login_url: %s", post_login_url)
                page.goto(post_login_url, timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass

            _wait_for_content(page, resultado)
            _screenshot(page, "4_home_post_login")

            selector_ganador, total, preview = _detectar_noticias_con_scroll(page)
            if total == 0:
                log.info("No se detectaron noticias reales tras scroll y relectura del DOM")

            resultado["url_final"] = page.url
            resultado["titulo_final"] = page.title()
            resultado["selector_ganador"] = selector_ganador
            resultado["noticias_encontradas"] = total
            resultado["noticias_preview"] = preview

            feed_principal = _extraer_items_feed_noticias(page)
            reportes = _extraer_items_reportes(page)
            mas_visto = _extraer_links_por_heading(page, "Lo más visto")

            resultado["feed_principal_encontrado"] = len(feed_principal)
            resultado["feed_principal_preview"] = feed_principal[:10]
            resultado["reportes_encontrados"] = len(reportes)
            resultado["reportes_preview"] = reportes[:10]
            resultado["mas_visto_encontrado"] = len(mas_visto)
            resultado["mas_visto_preview"] = mas_visto[:10]
            resultado["estado"] = "ok"

            if total > 0:
                log.info(
                    "BNAMERICAS_OK | login_exitoso=%s | noticias=%d | selector=%s | url_final=%s",
                    resultado["login_exitoso"],
                    total,
                    selector_ganador,
                    page.url,
                )
            else:
                log.warning(
                    "BNAMERICAS_SIN_NOTICIAS | login_exitoso=%s | url_final=%s",
                    resultado["login_exitoso"],
                    page.url,
                )

            log.info(
                "BNAMERICAS_BLOQUES | feed=%d | reportes=%d | mas_visto=%d",
                resultado["feed_principal_encontrado"],
                resultado["reportes_encontrados"],
                resultado["mas_visto_encontrado"],
            )

        except Exception as exc:
            resultado["estado"] = "error"
            resultado["error"] = str(exc)
            log.exception("Error inesperado en BNamericas: %s", exc)
            try:
                _screenshot(page, "error")
            except Exception:
                pass
        finally:
            try:
                if not resultado.get("url_final"):
                    resultado["url_final"] = page.url
                if not resultado.get("titulo_final"):
                    resultado["titulo_final"] = page.title()
            except Exception:
                pass
            resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _guardar_resultado_json(resultado)
            input("\nPresiona Enter para cerrar el browser...")
            browser.close()

    log.info("=== FIN DIAGNOSTICO BNAMERICAS ===")
    log.info("Screenshots en: %s", SCREENSHOTS_DIR)
    return resultado


if __name__ == "__main__":
    run_diagnostico()
