"""Helpers de navegador para scraping visible, headless y post-login."""

from __future__ import annotations

import io
import logging
import os
import random
import subprocess
import time

import numpy as np
from bs4 import BeautifulSoup
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc

from .constants import (
    BROWSER_CHALLENGE_BODY_TOKENS,
    BROWSER_CHALLENGE_TITLES,
    CAPTCHA_TEXT_PATTERNS,
    HEADERS,
)
from .extraction import (
    build_browser_wait_selectors,
    build_login_wait_selectors,
    extract_noticias_from_soup,
)
from .source_rules import (
    flaresolverr_enabled_for_source,
    get_scrape_target_url,
    is_mining_com_source,
    is_miningdigital_source,
    is_miningweekly_source,
    is_rumbominero_source,
)

logger = logging.getLogger(__name__)

try:
    import easyocr as _easyocr

    _EASYOCR_AVAILABLE = True
except ImportError:
    _EASYOCR_AVAILABLE = False

_easyocr_reader = None


def page_looks_like_spa_shell(html: str) -> bool:
    sample = (html or "")[:5000].lower()
    return (
        "you need to enable javascript to run this app" in sample
        or '<div id="root"></div>' in sample
        or 'id="root"></div>' in sample
    )


def detect_login_captcha(html: str) -> bool:
    sample = (html or "")[:40000].lower()
    return any(token in sample for token in CAPTCHA_TEXT_PATTERNS)


def get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        _easyocr_reader = _easyocr.Reader(["en"], verbose=False)
    return _easyocr_reader


def solve_captcha_paddle(page, source_name: str) -> str | None:
    """
    Busca la imagen del CAPTCHA en la página, la captura con Playwright
    y usa EasyOCR para extraer el texto.
    """
    if not _EASYOCR_AVAILABLE:
        logger.warning("[%s] EasyOCR no disponible — instalá easyocr", source_name)
        return None

    captcha_img_selectors = [
        "img[src*='captcha' i]",
        "img[id*='captcha' i]",
        "img[class*='captcha' i]",
        "#captchaImg",
        "img[alt*='captcha' i]",
        ".captcha img",
    ]

    captcha_loc = None
    for sel in captcha_img_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            captcha_loc = loc
            logger.info("[%s] CAPTCHA imagen encontrada con selector: %s", source_name, sel)
            break

    if captcha_loc is None:
        logger.warning("[%s] No se encontró imagen de CAPTCHA en la página", source_name)
        return None

    try:
        img_bytes = captcha_loc.screenshot()
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img_array = np.array(img)

        reader = get_easyocr_reader()
        result = reader.readtext(img_array)

        if not result:
            logger.warning("[%s] EasyOCR no extrajo texto del CAPTCHA", source_name)
            return None

        texto = "".join(r[1] for r in result).strip().replace(" ", "")
        logger.info("[%s] CAPTCHA resuelto por OCR: '%s'", source_name, texto)
        return texto
    except Exception as exc:
        logger.warning("[%s] Error resolviendo CAPTCHA con EasyOCR: %s", source_name, exc)
        return None


def wait_for_login_content(page, source: dict) -> None:
    """
    Espera contenido real post-login para sitios SPA o dashboards que hidratan el DOM tarde.
    """
    selectors = build_login_wait_selectors(source)
    source_name = (source.get("name") or "").lower()

    for round_idx in range(1, 5):
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

        html = page.content()
        if page_looks_like_spa_shell(html):
            logger.info(
                "[%s] Login post-render aún luce como SPA shell en ronda %d; esperando hidratación...",
                source["name"],
                round_idx,
            )

        for selector in selectors:
            try:
                count = page.locator(selector).count()
            except Exception:
                continue
            if count > 0:
                logger.info(
                    "[%s] Post-login wait -> selector '%s' encontró %d nodos en ronda %d",
                    source["name"],
                    selector,
                    count,
                    round_idx,
                )
                return

        if "bnamericas" in source_name:
            for selector in (
                "a[href*='/news']",
                "a[href*='/projects']",
                "a[href*='/companies']",
                "h3.article-title a",
            ):
                try:
                    count = page.locator(selector).count()
                except Exception:
                    continue
                if count > 0:
                    logger.info(
                        "[%s] BNamericas post-login -> selector '%s' encontró %d nodos en ronda %d",
                        source["name"],
                        selector,
                        count,
                        round_idx,
                    )
                    return

        try:
            page.mouse.wheel(0, 1400)
        except Exception:
            pass
        page.wait_for_timeout(2_000)


def patch_undetected_chromedriver_shutdown() -> None:
    """
    Hace idempotente el cierre de UC para evitar WinError 6 en Windows
    cuando __del__ intenta cerrar una sesión ya finalizada.
    """
    if getattr(uc.Chrome, "_boletin_safe_shutdown", False):
        return

    original_quit = uc.Chrome.quit

    def safe_quit(self, *args, **kwargs):
        if getattr(self, "_boletin_quit_called", False):
            return None
        self._boletin_quit_called = True
        try:
            return original_quit(self, *args, **kwargs)
        except OSError as e:
            if getattr(e, "winerror", None) == 6:
                logger.debug("Ignorando WinError 6 al cerrar undetected-chromedriver")
                return None
            raise
        finally:
            try:
                if getattr(self, "service", None) is not None:
                    self.service.process = None
            except Exception:
                pass

    def safe_del(self):
        try:
            safe_quit(self)
        except Exception:
            pass

    uc.Chrome.quit = safe_quit
    uc.Chrome.__del__ = safe_del
    uc.Chrome._boletin_safe_shutdown = True


def get_local_chrome_major_version() -> int | None:
    """
    Detecta la major version de Google Chrome instalada localmente.
    """
    posibles_rutas = []
    for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if base:
            posibles_rutas.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    posibles_rutas.extend(
        [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    )

    vistas = set()
    for ruta in posibles_rutas:
        if ruta in vistas:
            continue
        vistas.add(ruta)
        if not os.path.exists(ruta):
            continue
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Item '{ruta}').VersionInfo.ProductVersion",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            version = (result.stdout or "").strip()
            if version:
                major = version.split(".", 1)[0]
                if major.isdigit():
                    return int(major)
        except Exception:
            pass
    return None


def browser_page_is_challenge(driver) -> bool:
    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""
    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        body_text = ""

    return any(token in title for token in BROWSER_CHALLENGE_TITLES) or any(
        token in body_text for token in BROWSER_CHALLENGE_BODY_TOKENS
    )


def _try_solve_cloudflare_turnstile(driver, source_name: str) -> bool:
    """
    Intenta hacer click en el checkbox de Cloudflare Turnstile dentro del iframe.
    Retorna True si encontró y clickeó el checkbox.
    """
    iframe_selectors = [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='cloudflare.com']",
    ]
    for sel in iframe_selectors:
        try:
            frames = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            continue
        if not frames:
            continue
        try:
            driver.switch_to.frame(frames[0])
            time.sleep(random.uniform(0.5, 1.2))
            for cb_sel in ("input[type='checkbox']", ".mark", "[type='checkbox']"):
                try:
                    cb = driver.find_element(By.CSS_SELECTOR, cb_sel)
                    ActionChains(driver).move_to_element(cb).pause(random.uniform(0.3, 0.8)).click().perform()
                    logger.info("[%s] Turnstile: click en checkbox '%s'", source_name, cb_sel)
                    return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("[%s] Error al intentar Turnstile iframe '%s': %s", source_name, sel, e)
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    logger.debug("[%s] Turnstile: iframe no encontrado aún", source_name)
    return False


def wait_for_browser_content(driver, source: dict, timeout_seconds: int = 20) -> tuple[bool, str]:
    selectors = build_browser_wait_selectors(source)
    started_at = time.time()
    last_state = "desconocido"
    _turnstile_attempts = 0

    while time.time() - started_at < timeout_seconds:
        try:
            current_title = driver.title or ""
        except Exception:
            current_title = ""
        current_url = driver.current_url or get_scrape_target_url(source)

        challenge_active = browser_page_is_challenge(driver)
        if challenge_active:
            last_state = f"challenge_activo | title={current_title} | url={current_url}"
            logger.info(
                "[%s] Cloudflare challenge activo ('%s') → esperando... (%ds)",
                source["name"],
                current_title,
                int(time.time() - started_at),
            )
            if _turnstile_attempts < 3:
                clicked = _try_solve_cloudflare_turnstile(driver, source["name"])
                _turnstile_attempts += 1
                if clicked:
                    time.sleep(4)
                    continue
        else:
            for selector in selectors:
                try:
                    nodes = driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    nodes = []
                if nodes:
                    logger.info(
                        "[%s] Contenido real detectado con selector '%s' (%d nodos)",
                        source["name"],
                        selector,
                        len(nodes),
                    )
                    return True, selector
            last_state = f"sin_challenge_pero_sin_selectores | title={current_title} | url={current_url}"
            logger.info(
                "[%s] Sin challenge aparente, pero todavía sin selectores de noticias | title=%s",
                source["name"],
                current_title,
            )

        time.sleep(2)

    return False, last_state


def progressive_scroll_driver(driver, source_name: str, max_scrolls: int = 8) -> None:
    """
    Scrollea hasta el fondo de la página progresivamente para activar lazy load
    e infinite scroll. Detiene si no aparecen links nuevos en 2 scrolls consecutivos.
    """
    prev_count = 0
    no_new = 0

    for i in range(1, max_scrolls + 1):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            break
        time.sleep(1.5)
        try:
            current_count = len(driver.find_elements(By.CSS_SELECTOR, "a[href]"))
        except Exception:
            break

        logger.info(
            "[%s] Scroll progresivo %d/%d: %d links (antes: %d)",
            source_name, i, max_scrolls, current_count, prev_count,
        )

        if current_count > prev_count:
            no_new = 0
        else:
            no_new += 1
            if no_new >= 2:
                logger.info("[%s] Sin contenido nuevo en 2 scrolls — deteniendo", source_name)
                break

        prev_count = current_count


def extract_noticias_from_driver(driver, source: dict, origin_label: str) -> list[dict]:
    target_url = get_scrape_target_url(source)
    current_url = driver.current_url or target_url
    if current_url.rstrip("/") != target_url.rstrip("/"):
        logger.warning("Redirección detectada: %s -> %s", target_url, current_url)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    noticias = extract_noticias_from_soup(source, soup, current_url, origin_label)
    if not noticias:
        logger.warning(
            "[%s] %s no encontró noticias. Dump HTML: %s",
            source["name"],
            origin_label,
            driver.page_source[:500],
        )
    return noticias


def scrape_visible_chrome_sync(source: dict) -> list[dict]:
    """
    Fallback visible específico para fuentes que en modo headless quedan bloqueadas.
    """
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"--user-agent={HEADERS['User-Agent']}")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(get_scrape_target_url(source))
        resolved, state = wait_for_browser_content(driver, source, timeout_seconds=60)
        logger.info(
            "[%s] Selenium visible | URL final: %s | título: %s | estado=%s",
            source["name"],
            driver.current_url,
            driver.title,
            state,
        )
        if not resolved:
            logger.warning(
                "[%s] Selenium visible no resolvió contenido real tras 60s. Estado=%s",
                source["name"],
                state,
            )
        progressive_scroll_driver(driver, source["name"])
        return extract_noticias_from_driver(driver, source, "Selenium visible")
    except Exception as exc:
        logger.warning("Error scraping Selenium visible %s: %s", source["url"], exc)
        return []
    finally:
        if driver is not None:
            driver.quit()


def scrape_with_playwright_stealth(source: dict) -> list[dict]:
    """
    Scraping con Playwright + playwright-stealth para sitios protegidos por Cloudflare.
    Parchea fingerprinting JS y usa Chromium real (mejor TLS que ChromeDriver).
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        logger.warning("[%s] playwright-stealth no disponible: %s", source.get("name"), exc)
        return []

    target_url = get_scrape_target_url(source)
    source_name = source["name"]
    _challenge_tokens = ("un momento", "just a moment", "checking your browser", "cloudflare")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--start-maximized"],
        )
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-ES",
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.new_page()
        Stealth().use_sync(page)

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            logger.warning("[%s] Playwright stealth: error navegando: %s", source_name, exc)
            browser.close()
            return []

        started_at = time.time()
        _turnstile_clicked = False

        try:
            while time.time() - started_at < 30:
                title = (page.title() or "").lower()
                is_challenge = any(t in title for t in _challenge_tokens)

                if is_challenge:
                    logger.info(
                        "[%s] Playwright stealth: challenge activo '%s' (%ds)",
                        source_name, title, int(time.time() - started_at),
                    )
                    if not _turnstile_clicked:
                        try:
                            cf_frame = page.frame_locator(
                                "iframe[src*='challenges.cloudflare.com'], iframe[src*='cloudflare.com']"
                            ).first
                            cb = cf_frame.locator("input[type='checkbox'], .mark").first
                            cb.wait_for(timeout=3_000)
                            page.mouse.move(random.randint(100, 800), random.randint(100, 600))
                            time.sleep(random.uniform(0.3, 0.7))
                            cb.click()
                            _turnstile_clicked = True
                            logger.info("[%s] Playwright stealth: Turnstile click ejecutado", source_name)
                            time.sleep(4)
                            continue
                        except Exception:
                            pass
                else:
                    for sel in build_browser_wait_selectors(source):
                        try:
                            if page.locator(sel).count() > 0:
                                logger.info(
                                    "[%s] Playwright stealth: contenido detectado con '%s'",
                                    source_name, sel,
                                )
                                soup = BeautifulSoup(page.content(), "html.parser")
                                return extract_noticias_from_soup(source, soup, page.url, "Playwright stealth")
                        except Exception:
                            continue

                time.sleep(2)

            logger.warning("[%s] Playwright stealth: timeout sin contenido real", source_name)
            return []
        finally:
            browser.close()


def scrape_fallback_sync(source: dict, flaresolverr_scraper=None) -> list[dict]:
    """
    Intenta scraping browser para evadir protecciones anti-bot.
    """
    if flaresolverr_scraper and flaresolverr_enabled_for_source(source):
        logger.info("[%s] Usando FlareSolverr como fallback preferente", source["name"])
        noticias_fs = flaresolverr_scraper(source, "FlareSolverr browser fallback")
        if noticias_fs:
            return noticias_fs
        logger.warning(
            "[%s] FlareSolverr no obtuvo noticias. Reintentando con fallback browser...",
            source["name"],
        )

    if flaresolverr_enabled_for_source(source):
        logger.info("[%s] Intentando Playwright + stealth...", source["name"])
        noticias_pw = scrape_with_playwright_stealth(source)
        if noticias_pw:
            logger.info("[%s] Playwright stealth exitoso: %d noticias", source["name"], len(noticias_pw))
            return noticias_pw
        logger.warning("[%s] Playwright stealth no obtuvo noticias. Reintentando con Selenium visible...", source["name"])

    if is_miningweekly_source(source) or is_rumbominero_source(source) or is_mining_com_source(source) or is_miningdigital_source(source):
        logger.info("[%s] Usando fallback Selenium visible específico", source["name"])
        noticias_visible = scrape_visible_chrome_sync(source)
        if noticias_visible:
            return noticias_visible
        logger.warning(
            "[%s] Selenium visible no obtuvo noticias. Reintentando con UC headless...",
            source["name"],
        )

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    chrome_major = get_local_chrome_major_version()
    driver = None
    try:
        if chrome_major:
            logger.info(
                "Inicializando undetected-chromedriver para [%s] con Chrome major %s",
                source["name"],
                chrome_major,
            )
            try:
                driver = uc.Chrome(
                    headless=True,
                    options=options,
                    version_main=chrome_major,
                )
            except Exception as e:
                logger.warning(
                    "UC falló con version_main=%s para [%s]: %s. Reintentando autodetección.",
                    chrome_major,
                    source["name"],
                    e,
                )
        if driver is None:
            driver = uc.Chrome(headless=True, options=options)
        driver.get(get_scrape_target_url(source))
        resolved, state = wait_for_browser_content(driver, source, timeout_seconds=20)
        if not resolved:
            logger.warning(
                "[%s] UC headless no resolvió contenido real tras 20s. Estado=%s",
                source["name"],
                state,
            )
        progressive_scroll_driver(driver, source["name"])
        return extract_noticias_from_driver(driver, source, "UC/Playwright")
    except Exception as e:
        logger.warning("Error scraping UC %s: %s", source["url"], e)
        if "403" in str(e) or "429" in str(e):
            logger.warning("Anti-bot activo en %s → considerá feed RSS alternativo", source["url"])
        return []
    finally:
        if driver is not None:
            driver.quit()
