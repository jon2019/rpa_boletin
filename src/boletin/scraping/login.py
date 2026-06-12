"""Scraping autenticado y helpers del flujo de login."""

from __future__ import annotations

import logging
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .browser import (
    detect_login_captcha,
    page_looks_like_spa_shell,
    solve_captcha_paddle,
    wait_for_login_content,
)
from .constants import HEADERS
from .extraction import (
    dedupe_noticias,
    extract_bnamericas_home_blocks,
    extract_noticias_from_soup,
    extract_portalminero_wp_page,
)
from .feed_detection import build_news_item
from .source_rules import is_bnamericas_app_source, is_portalminero_source

logger = logging.getLogger(__name__)


_PORTALMINERO_WP_URLS = [
    "https://www.portalminero.com/wp/noticias-portalminero/",
    "https://www.portalminero.com/wp/notas-de-prensa/",
]


def _scrape_portalminero_wp_pages(page, source: dict) -> list[dict]:
    all_noticias: list[dict] = []
    for target_url in _PORTALMINERO_WP_URLS:
        logger.info("[%s] Navegando a sección WP: %s", source["name"], target_url)
        page.goto(target_url, timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        section_label = target_url.rstrip("/").rsplit("/", 1)[-1]
        noticias = extract_portalminero_wp_page(
            source, soup, target_url, f"Login [WP:{section_label}]", max_age_days=2
        )
        logger.info("[%s] Sección '%s' -> %d noticias", source["name"], section_label, len(noticias))
        all_noticias.extend(noticias)
    return dedupe_noticias(all_noticias)


_BNAMERICAS_GEO_COUNTRY_MAP = {
    "chile":     "Chile",
    "argentina": "Argentina",
    "peru":      "Peru",
    "perú":      "Peru",
}


def _detect_bnamericas_article_country(link_el, default: str = "Internacional") -> str:
    """
    Detecta el país de un artículo BNamericas buscando los tags geográficos del card.

    Cada card renderiza chips como:
      <a href="/article/geographicarea/chile">Chile</a>
      <a href="/article/geographicarea/argentina">Argentina</a>

    Sube hasta 6 niveles desde el link del artículo para encontrar el contenedor
    y devuelve el primer país mapeado que encuentre. Si hay varios (e.g. Chile + Argentina),
    devuelve el primero que aparezca en el DOM.
    """
    container = link_el.parent
    for _ in range(6):
        if container is None:
            break
        for geo_link in container.select("a[href*='/article/geographicarea/']"):
            href_low = (geo_link.get("href") or "").lower()
            for key, country in _BNAMERICAS_GEO_COUNTRY_MAP.items():
                if f"/article/geographicarea/{key}" in href_low:
                    return country
        container = container.parent if hasattr(container, "parent") else None
    return default


def _parse_bnamericas_age_days(text: str) -> float | None:
    """
    Parsea la antigüedad relativa de un card de BNamericas.
    Formatos: 'hace X minutos', 'hace X horas', 'hace un día', 'hace X días'.
    Devuelve días como float, o None si no pudo parsear.
    """
    t = (text or "").lower()
    if re.search(r'hace\s+\d+\s+minuto', t):
        return 0.0
    m = re.search(r'hace\s+(\d+)\s+hora', t)
    if m:
        return int(m.group(1)) / 24.0
    if re.search(r'hace\s+un\s+d[íi]a', t):
        return 1.0
    m = re.search(r'hace\s+(\d+)\s+d[íi]a', t)
    if m:
        return float(m.group(1))
    return None


def _scrape_bnamericas_app_filter(page, source: dict) -> list[dict]:
    """Navega al filtro de app.bnamericas.com y extrae artículos con scroll infinito.

    El path del filtro es configurable vía BNAMERICAS_APP_FILTER_PATH en el .env.
    Detiene el scroll cuando detecta artículos con más de MAX_AGE_DAYS de antigüedad.
    """
    from boletin.config.environment import get_bnamericas_app_settings

    settings = get_bnamericas_app_settings()
    filter_url = f"https://app.bnamericas.com/{settings.filter_path.lstrip('/')}"
    logger.info("[%s] BNamericas App — navegando a filtro: %s", source["name"], filter_url)

    page.goto(filter_url, timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass

    # Esperar hidratación SPA
    for sel in ("a[href*='/article/content/']", "h3 a", "[class*='card'] a"):
        try:
            page.wait_for_selector(sel, timeout=10_000)
            logger.info("[%s] BNamericas filter — contenido detectado con '%s'", source["name"], sel)
            break
        except Exception:
            pass

    # Los artículos del filtro tienen href con ?source=FILTER (no /article/content/)
    # Ejemplo real: /article/section/all/content/xhjj6tbbb-ceos?source=FILTER
    _FILTER_SELECTOR = "a[href*='source=FILTER']"

    MAX_AGE_DAYS = 3
    MAX_SCROLLS = 8
    seen_hrefs: set[str] = set()
    noticias: list[dict] = []

    for scroll_round in range(1, MAX_SCROLLS + 1):
        soup = BeautifulSoup(page.content(), "html.parser")
        article_links = soup.select(_FILTER_SELECTOR)
        new_this_round = 0
        stop_scroll = False

        for link in article_links:
            href = (link.get("href") or "").strip()
            if not href:
                continue
            if not href.startswith("http"):
                href = f"https://app.bnamericas.com{href}"
            href_key = href.split("?")[0].lower()
            if href_key in seen_hrefs:
                continue

            title = " ".join(link.get_text(" ", strip=True).split())
            if len(title) < 8:
                continue

            # Buscar timestamp "hace X días/horas/minutos" subiendo hasta 5 niveles
            age_days: float | None = None
            container = link.parent
            for _ in range(5):
                if container is None:
                    break
                age_days = _parse_bnamericas_age_days(container.get_text(" ", strip=True))
                if age_days is not None:
                    break
                container = container.parent if hasattr(container, "parent") else None

            if age_days is not None and age_days > MAX_AGE_DAYS:
                logger.info(
                    "[%s] BNamericas filter ronda %d — descartado (%.1fd > %dd): '%s'",
                    source["name"], scroll_round, age_days, MAX_AGE_DAYS, title[:80],
                )
                stop_scroll = True
                continue

            country = _detect_bnamericas_article_country(link, default=source.get("country") or "Internacional")
            seen_hrefs.add(href_key)
            new_this_round += 1
            noticias.append(
                build_news_item(title, href, "", None, source["name"], country, url_fuente=source["url"])
            )
            logger.info(
                "[%s] BNamericas filter ronda %d — noticia #%d | titulo='%s' | país=%s | edad=%s | url=%s",
                source["name"], scroll_round, len(noticias), title[:100],
                country,
                f"{age_days:.1f}d" if age_days is not None else "desconocida",
                href,
            )

        logger.info(
            "[%s] BNamericas filter ronda %d — %d nuevas, total=%d, stop=%s",
            source["name"], scroll_round, new_this_round, len(noticias), stop_scroll,
        )

        if stop_scroll:
            break

        # Scroll para cargar más artículos (infinite scroll)
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(2_500)

        # Si no cargó contenido nuevo después del scroll, salir
        new_soup = BeautifulSoup(page.content(), "html.parser")
        new_count = len(new_soup.select(_FILTER_SELECTOR))
        if new_count <= len(article_links):
            logger.info("[%s] BNamericas filter — sin nuevo contenido tras scroll en ronda %d, saliendo", source["name"], scroll_round)
            break

    return dedupe_noticias(noticias)


def scrape_with_login(source: dict) -> tuple[list[dict], str | None]:
    """
    Scraping autenticado usando Playwright.
    Requiere source['usuario'] y source['clave'].
    """
    usuario = source.get("usuario") or ""
    clave = source.get("clave") or ""
    if not usuario or not clave:
        motivo = "Credenciales incompletas: usuario o clave vacíos"
        logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
        return [], motivo

    url_base = source["url"].rstrip("/")
    login_url = (source.get("login_url") or "").strip() or None
    post_login_url = (source.get("post_login_url") or "").strip() or url_base

    logger.info("[%s] Iniciando login scraping como '%s'", source["name"], usuario)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-CL",
            extra_http_headers={"Accept-Language": "es-CL,es;q=0.9,en;q=0.8"},
        )
        page = ctx.new_page()
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """
        )

        try:
            if login_url:
                logger.info("[%s] Navegando directamente a login_url: %s", source["name"], login_url)
                page.goto(login_url, timeout=30_000)
            else:
                logger.info("[%s] Navegando a url_base para buscar enlace de login: %s", source["name"], url_base)
                page.goto(url_base, timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass

                login_link_selectors = [
                    "a[href*='/login']",
                    "a[href*='/signin']",
                    "a[href*='/ingresar']",
                    "a[href*='/acceso']",
                    "a[href*='/session']",
                    "a:has-text('Login')",
                    "a:has-text('Ingresar')",
                    "a:has-text('Iniciar sesión')",
                    "a:has-text('Sign in')",
                    "a:has-text('Log in')",
                ]
                clicked = False
                for sel in login_link_selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        href_val = loc.get_attribute("href") or ""
                        logger.info("[%s] Enlace de login encontrado (%s): %s", source["name"], sel, href_val)
                        loc.click()
                        clicked = True
                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            pass
                        break
                if not clicked:
                    logger.warning(
                        "[%s] No se encontró enlace de login — intentando rellenar form en página actual",
                        source["name"],
                    )

            logger.info("[%s] URL tras navegación a login: %s", source["name"], page.url)

            html_login = page.content()
            login_has_captcha = detect_login_captcha(html_login)
            if login_has_captcha:
                logger.info("[%s] CAPTCHA detectado en login — intentando resolver con PaddleOCR", source["name"])

            form_appeared = False
            for selector in (
                "input[type='email']",
                "input[type='password']",
                "input[name='email']",
                "input[name='username']",
                "input[name='os_username']",
            ):
                try:
                    page.wait_for_selector(selector, timeout=10_000)
                    form_appeared = True
                    logger.info("[%s] Formulario detectado con selector: %s", source["name"], selector)
                    break
                except Exception:
                    pass
            if not form_appeared:
                motivo = f"Formulario de login no detectado tras espera explícita | url={page.url}"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                return [], motivo

            user_selectors = [
                "input[type='email']",
                "input[name='email']",
                "input[name='username']",
                "input[name='user']",
                "input[name='os_username']",
                "input[id='email']",
                "input[id='username']",
                "input[id='user']",
                "input[id='os_username']",
                "input[placeholder*='correo' i]",
                "input[placeholder*='email' i]",
                "input[placeholder*='usuario' i]",
                "input[placeholder*='user' i]",
            ]
            campo_usuario_ok = False
            for sel in user_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(usuario)
                    campo_usuario_ok = True
                    logger.info("[%s] Campo usuario rellenado (%s)", source["name"], sel)
                    break
            if not campo_usuario_ok:
                motivo = f"No se encontró campo de usuario en {page.url}"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                return [], motivo

            loc_pass = page.locator("input[type='password']").first
            if loc_pass.count() > 0:
                loc_pass.fill(clave)
                logger.info("[%s] Campo contraseña rellenado", source["name"])
            else:
                motivo = f"No se encontró campo de contraseña en {page.url}"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                return [], motivo

            captcha_field_selectors = [
                "input[name='os_captcha']",
                "input[name='captcha']",
                "input[id='captcha']",
                "input[id*='captcha' i]",
                "input[name*='captcha' i]",
                "input[placeholder*='captcha' i]",
            ]
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Ingresar')",
                "button:has-text('Login')",
                "button:has-text('Log in')",
                "button:has-text('Iniciar')",
                "button:has-text('Sign in')",
                "button:has-text('Acceder')",
            ]
            max_captcha_intentos = 5
            url_post_submit = page.url

            for intento in range(1, max_captcha_intentos + 1):
                if intento > 1:
                    logger.info(
                        "[%s] CAPTCHA reintento %d/%d — recargando login",
                        source["name"],
                        intento,
                        max_captcha_intentos,
                    )
                    url_login_reintento = login_url or page.url
                    page.goto(url_login_reintento, timeout=30_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except Exception:
                        pass
                    for sel_u in user_selectors:
                        loc_u = page.locator(sel_u).first
                        if loc_u.count() > 0:
                            loc_u.fill(usuario)
                            break
                    loc_p2 = page.locator("input[type='password']").first
                    if loc_p2.count() > 0:
                        loc_p2.fill(clave)

                if login_has_captcha:
                    texto_captcha = solve_captcha_paddle(page, source["name"])
                    if texto_captcha is None:
                        if intento < max_captcha_intentos:
                            logger.warning(
                                "[%s] OCR no resolvió CAPTCHA en intento %d — reintentando",
                                source["name"],
                                intento,
                            )
                            continue
                        motivo = f"CAPTCHA no pudo resolverse tras {max_captcha_intentos} intentos | url={page.url}"
                        logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                        return [], motivo

                    captcha_filled = False
                    for sel in captcha_field_selectors:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            loc.fill(texto_captcha)
                            captcha_filled = True
                            logger.info(
                                "[%s] Campo CAPTCHA rellenado (%s) con '%s' [intento %d]",
                                source["name"],
                                sel,
                                texto_captcha,
                                intento,
                            )
                            break
                    if not captcha_filled:
                        motivo = f"No se encontró campo de entrada del CAPTCHA | url={page.url}"
                        logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                        return [], motivo

                submitted = False
                for sel in submit_selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.click()
                        submitted = True
                        logger.info("[%s] Formulario enviado (%s) [intento %d]", source["name"], sel, intento)
                        break
                if not submitted:
                    logger.warning("[%s] No se encontró botón submit — intentando Enter", source["name"])
                    if campo_usuario_ok:
                        page.locator(user_selectors[0]).press("Enter")

                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                time.sleep(2)

                url_post_submit = page.url
                logger.info("[%s] URL tras submit [intento %d]: %s", source["name"], intento, url_post_submit)

                html_post_submit = page.content()
                still_on_login = any(s in url_post_submit.lower() for s in ("login.action", "dologin.action"))
                if detect_login_captcha(html_post_submit) or still_on_login:
                    login_has_captcha = True
                    if intento < max_captcha_intentos:
                        logger.warning(
                            "[%s] Login no completado en intento %d/%d (captcha=%s, en_login=%s) — reintentando",
                            source["name"],
                            intento,
                            max_captcha_intentos,
                            detect_login_captcha(html_post_submit),
                            still_on_login,
                        )
                        continue
                    motivo = f"Login no completado tras {max_captcha_intentos} intentos | url={url_post_submit}"
                    logger.warning(
                        "[%s] LOGIN_FAIL | motivo=%s | html=%s",
                        source["name"],
                        motivo,
                        html_post_submit[:800],
                    )
                    return [], motivo
                break

            if login_url and url_post_submit.rstrip("/") == login_url.rstrip("/"):
                motivo = (
                    "Login probablemente falló: la URL no cambió tras submit | "
                    f"url_post_login={url_post_submit}"
                )
                logger.warning("[%s] LOGIN_FAIL | motivo=%s | html=%s", source["name"], motivo, page.content()[:500])
                return [], motivo

            logger.info(
                "[%s] LOGIN_OK | autenticacion_exitosa=true | usuario='%s' | url_post_login=%s",
                source["name"],
                usuario,
                url_post_submit,
            )

            if is_portalminero_source(source):
                noticias = _scrape_portalminero_wp_pages(page, source)
                if noticias:
                    logger.info(
                        "[%s] LOGIN_SCRAPING_OK | sesion_autenticada=true | noticias=%d | fuente=WP_pages",
                        source["name"],
                        len(noticias),
                    )
                    return noticias, None
                motivo = "Login exitoso pero sin noticias en páginas WP de Portal Minero"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
                return [], motivo

            if is_bnamericas_app_source(source):
                noticias = _scrape_bnamericas_app_filter(page, source)
                if noticias:
                    logger.info(
                        "[%s] LOGIN_SCRAPING_OK | sesion_autenticada=true | noticias=%d | fuente=BNamericas App filter",
                        source["name"],
                        len(noticias),
                    )
                    return noticias, None
                motivo = "BNamericas App: login exitoso pero sin noticias en filtro"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s | html=%s", source["name"], motivo, page.content()[:800])
                return [], motivo

            if post_login_url.rstrip("/") != url_post_submit.rstrip("/"):
                logger.info("[%s] Navegando a post_login_url: %s", source["name"], post_login_url)
                page.goto(post_login_url, timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass

            wait_for_login_content(page, source)

            source_name = (source.get("name") or "").lower()
            current_html = page.content()
            if "bnamericas" in source_name and page_looks_like_spa_shell(current_html):
                logger.info(
                    "[%s] BNamericas sigue en shell SPA tras /home; se mantiene el home y se fuerza scroll para hidratar contenido",
                    source["name"],
                )
                try:
                    page.mouse.wheel(0, 1800)
                except Exception:
                    pass
                page.wait_for_timeout(2_500)

            final_html = page.content()
            if detect_login_captcha(final_html):
                motivo = f"CAPTCHA requerido en vista autenticada | url_final={page.url}"
                logger.warning("[%s] LOGIN_FAIL | motivo=%s | html=%s", source["name"], motivo, final_html[:800])
                return [], motivo

            soup = BeautifulSoup(final_html, "html.parser")
            noticias = extract_noticias_from_soup(source, soup, page.url or url_base, "Login")

            if "bnamericas" in source_name:
                noticias_home = extract_bnamericas_home_blocks(source, soup, page.url or url_base, "Login BNamericas home")
                if noticias_home:
                    noticias = dedupe_noticias(noticias + noticias_home)

            if noticias:
                logger.info(
                    "[%s] LOGIN_SCRAPING_OK | sesion_autenticada=true | noticias=%d | url_final=%s",
                    source["name"],
                    len(noticias),
                    page.url,
                )
                return noticias, None

            motivo = f"Login exitoso pero sin noticias detectadas | url_final={page.url}"
            logger.warning("[%s] LOGIN_FAIL | motivo=%s | html=%s", source["name"], motivo, page.content()[:800])
            return [], motivo
        except Exception as e:
            motivo = f"Excepción durante login scraping: {type(e).__name__}: {e}"
            logger.warning("[%s] LOGIN_FAIL | motivo=%s", source["name"], motivo)
            return [], motivo
        finally:
            browser.close()
