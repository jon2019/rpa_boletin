"""
Diagn?stico de scraping para https://www.rumbominero.com

Objetivo:
- validar selectores reales contra el HTML actual del home
- detectar noticias visibles del layout Newspaper / Rumbo Minero
- comparar el resultado del test con la extracci?n gen?rica del scraper productivo
- dejar evidencia en JSON para comparar luego con el scraper productivo

Ejecuci?n desde la ra?z del proyecto:
    python boletin/test_rumbominero.py

Salidas:
- tests/output/integration/test_rumbominero.log
- tests/output/integration/test_rumbominero_resultado.json
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

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from boletin.scraping.extraction import extract_noticias_from_soup as _extract_noticias_from_soup


load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("test_rumbominero")
BASE_URL = "https://www.rumbominero.com"
TARGET_URL = BASE_URL
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_rumbominero_resultado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}

SOURCE_FIXTURE = {
    "name": "Rumbo Minero",
    "url": BASE_URL,
    "country": "Per?",
    "scrape_selector": ", ".join([
        ".td_module_wrap .entry-title a[href*='rumbominero.com/']",
        ".tdb_module_header .entry-title a[href*='rumbominero.com/']",
        ".td_module_trending_now .entry-title a[href*='rumbominero.com/']",
        ".td-module-thumb a.td-image-wrap[href*='rumbominero.com/']",
        "h2.entry-title a[href*='rumbominero.com/']",
        "h3.entry-title a[href*='rumbominero.com/']",
        "p.entry-title a[href*='rumbominero.com/']",
    ]),
}

SELECTORES_CANDIDATOS = [
    ".td_module_wrap .entry-title a[href*='rumbominero.com/']",
    ".tdb_module_header .entry-title a[href*='rumbominero.com/']",
    ".td_module_trending_now .entry-title a[href*='rumbominero.com/']",
    ".td-module-thumb a.td-image-wrap[href*='rumbominero.com/']",
    "h2.entry-title a[href*='rumbominero.com/']",
    "h3.entry-title a[href*='rumbominero.com/']",
    "p.entry-title a[href*='rumbominero.com/']",
]

EXCLUDE_URL_TOKENS = (
    "/category/",
    "/tag/",
    "/author/",
    "/ediciones/",
    "/eventos/",
    "/contacto/",
    "/nosotros/",
    "/wp-content/",
    "/wp-json/",
    "/xmlrpc.php",
    "/feed/",
    "/comments/",
    "/?s=",
)
ALLOWED_PATH_TOKENS = (
    "/peru/noticias/",
    "/actualidad/",
    "/mexico/",
    "/usa/",
    "/canada/",
    "/chile/",
    "/argentina/",
    "/bolivia/",
    "/brasil/",
    "/colombia/",
    "/otros-paises/",
    "/rumbo-minero-tv/",
    "/entrevistas-tv/",
    "/portada2/",
    "/portada/",
    "/revista/",
)
CHALLENGE_TEXT_TOKENS = (
    "verificaci?n de seguridad en curso",
    "verificando",
    "cloudflare",
    "usted no es un bot",
    "security verification",
    "verifique que es un ser humano",
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

    log_file = RESULT_DIR / "test_rumbominero.log"
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



def _guardar_resultado(resultado: dict) -> None:
    RESULT_JSON.write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Resultado JSON guardado: %s", RESULT_JSON)



def _normalizar_texto(texto: str) -> str:
    return " ".join((texto or "").split())



def _es_url_noticia_rumbominero(href: str) -> bool:
    if not href:
        return False
    url = href.strip().lower()
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    if host != "rumbominero.com":
        return False
    path = parsed.path.lower()
    if path in {"", "/"}:
        return False
    if any(token in url for token in EXCLUDE_URL_TOKENS):
        return False
    return any(token in path for token in ALLOWED_PATH_TOKENS)



def _extraer_titulo_desde_anchor(anchor) -> str:
    titulo = _normalizar_texto(anchor.get_text(" ", strip=True))
    if titulo:
        return titulo
    titulo = _normalizar_texto((anchor.get("title") or anchor.get("aria-label") or ""))
    if titulo:
        return titulo
    return ""



def _extraer_categoria_desde_container(container) -> str:
    if not container:
        return ""
    categoria_node = container.select_one(".td-post-category")
    if categoria_node:
        return _normalizar_texto(categoria_node.get_text(" ", strip=True))
    return ""



def _extraer_noticias_desde_modulos(soup: BeautifulSoup, base_url: str) -> list[dict]:
    noticias: list[dict] = []
    seen: set[str] = set()

    for selector in SELECTORES_CANDIDATOS:
        for anchor in soup.select(selector):
            href = urljoin(base_url, (anchor.get("href") or "").strip())
            if href in seen or not _es_url_noticia_rumbominero(href):
                continue

            titulo = _extraer_titulo_desde_anchor(anchor)
            if not titulo:
                continue

            container = anchor.find_parent(class_=lambda value: value and any(
                token in " ".join(value).lower()
                for token in ("td_module_wrap", "td-module-container", "td_module_flex", "td_module_trending_now")
            ))

            noticia = {
                "titulo": titulo[:220],
                "url": href,
                "categoria": _extraer_categoria_desde_container(container),
                "selector": selector,
            }
            noticias.append(noticia)
            seen.add(href)
            log.info(
                "Noticia detectada | selector=%s | categoria=%s | titulo=%s | url=%s",
                selector,
                noticia["categoria"],
                titulo[:140],
                href,
            )

    return noticias



def _probar_selectores(soup: BeautifulSoup, base_url: str) -> list[dict]:
    resultados: list[dict] = []
    for selector in SELECTORES_CANDIDATOS:
        try:
            nodes = soup.select(selector)
        except Exception as exc:
            resultados.append({
                "selector": selector,
                "total": 0,
                "error": str(exc),
                "preview": [],
            })
            continue

        preview = []
        for node in nodes[:5]:
            texto = _extraer_titulo_desde_anchor(node)[:160]
            href = urljoin(base_url, (node.get("href") or "").strip())
            preview.append({"texto": texto, "href": href})

        resultados.append({
            "selector": selector,
            "total": len(nodes),
            "preview": preview,
        })
        log.info("Selector '%s' encontr? %d nodos", selector, len(nodes))

    return resultados



def _comparar_con_scraper_generico(soup: BeautifulSoup, base_url: str) -> list[dict]:
    try:
        noticias = _extract_noticias_from_soup(
            SOURCE_FIXTURE,
            soup,
            base_url,
            "Test Rumbo Minero",
        )
    except Exception as exc:
        log.exception("Fall? la comparaci?n con _extract_noticias_from_soup: %s", exc)
        return [{"titulo": "ERROR", "url": str(exc)}]

    return [
        {
            "titulo": noticia.get("titulo", ""),
            "url": noticia.get("url", ""),
        }
        for noticia in noticias[:20]
    ]



def _html_parece_challenge(html: str) -> bool:
    sample = (html or "")[:40000].lower()
    return any(token in sample for token in CHALLENGE_TEXT_TOKENS)



def _esperar_contenido_real(driver: webdriver.Chrome, timeout_segundos: int = 60) -> tuple[bool, str]:
    inicio = time.time()
    ultimo_estado = "desconocido"

    while time.time() - inicio < timeout_segundos:
        title = driver.title or ""
        current_url = driver.current_url or TARGET_URL
        body_text = ""
        try:
            body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except Exception:
            pass

        challenge_activo = any(token in title.lower() for token in CHALLENGE_TEXT_TOKENS) or any(
            token in body_text for token in CHALLENGE_TEXT_TOKENS
        )
        if challenge_activo:
            ultimo_estado = f"challenge_activo | title={title} | url={current_url}"
            log.info("Challenge activo ? esperando... | title=%s | url=%s", title, current_url)
        else:
            for selector in SELECTORES_CANDIDATOS:
                try:
                    nodes = driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    nodes = []
                if nodes:
                    log.info("Contenido real detectado con selector '%s' (%d nodos)", selector, len(nodes))
                    return True, selector
            ultimo_estado = f"sin_challenge_pero_sin_selectores | title={title} | url={current_url}"
            log.info("Sin challenge aparente, pero todav?a sin selectores de noticias | title=%s", title)

        time.sleep(2)

    return False, ultimo_estado



def _buscar_objetivo_verificacion(driver: webdriver.Chrome):
    candidatos = [
        (By.CSS_SELECTOR, "input[type='checkbox']"),
        (By.CSS_SELECTOR, "[role='checkbox']"),
        (By.XPATH, "//*[contains(normalize-space(.), 'Verifique que es un ser humano')]"),
        (By.XPATH, "//*[contains(normalize-space(.), 'Verify you are human')]"),
    ]

    for by, selector in candidatos:
        try:
            nodes = driver.find_elements(by, selector)
        except Exception:
            nodes = []

        for node in nodes:
            try:
                if node.is_displayed():
                    return node, f"{by}={selector}"
            except Exception:
                continue

    return None, None



def _intentar_click_verificacion_humana(driver: webdriver.Chrome) -> tuple[bool, str]:
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    objetivo, detalle = _buscar_objetivo_verificacion(driver)
    if objetivo is not None:
        try:
            ActionChains(driver).move_to_element(objetivo).pause(0.5).click(objetivo).perform()
            return True, f"click_main_document: {detalle}"
        except Exception as exc:
            log.warning("No pude clickear el objetivo en documento principal: %s", exc)

    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        iframes = []

    for idx, iframe in enumerate(iframes):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(iframe)
            objetivo, detalle = _buscar_objetivo_verificacion(driver)
            if objetivo is None:
                continue

            ActionChains(driver).move_to_element(objetivo).pause(0.5).click(objetivo).perform()
            driver.switch_to.default_content()
            return True, f"click_iframe_{idx}: {detalle}"
        except Exception as exc:
            log.warning("No pude clickear objetivo en iframe %d: %s", idx, exc)
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

    return False, "objetivo_no_encontrado"



def _resolver_challenge_con_apoyo_manual(
    driver: webdriver.Chrome,
    timeout_segundos: int = 120,
) -> tuple[bool, str]:
    log.warning(
        "Challenge detectado. Voy a intentar clickear la verificaci?n humana y, si no alcanza, "
        "dejar? tiempo para resoluci?n manual."
    )

    click_ok, click_detalle = _intentar_click_verificacion_humana(driver)
    if click_ok:
        log.info("Intento de click autom?tico ejecutado: %s", click_detalle)
    else:
        log.info("No encontr? un objetivo clickeable autom?tico: %s", click_detalle)

    inicio = time.time()
    ultimo_estado = click_detalle
    ultimo_aviso_seg = -1

    while time.time() - inicio < timeout_segundos:
        challenge_activo, detalle = _challenge_sigue_activo(driver)
        if not challenge_activo:
            ok, resultado = _esperar_contenido_real(driver, timeout_segundos=20)
            if ok:
                return True, f"challenge_resuelto: {resultado}"
            ultimo_estado = f"challenge_baj? pero sin noticias: {resultado}"
        else:
            ultimo_estado = detalle

        segundos = int(time.time() - inicio)
        if segundos // 10 != ultimo_aviso_seg:
            ultimo_aviso_seg = segundos // 10
            restante = max(0, timeout_segundos - segundos)
            log.warning(
                "Si el navegador muestra el checkbox de Cloudflare, hac? click manualmente. "
                "Tiempo restante: %ss | estado=%s",
                restante,
                ultimo_estado,
            )

        time.sleep(2)

    return False, ultimo_estado



def _challenge_sigue_activo(driver: webdriver.Chrome) -> tuple[bool, str]:
    title = driver.title or ""
    current_url = driver.current_url or TARGET_URL
    body_text = ""
    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        pass

    challenge_activo = any(token in title.lower() for token in CHALLENGE_TEXT_TOKENS) or any(
        token in body_text for token in CHALLENGE_TEXT_TOKENS
    )
    detalle = f"challenge_activo | title={title} | url={current_url}"
    return challenge_activo, detalle



def _descargar_html_con_browser_visible(timeout_segundos: int = 60) -> tuple[str, str, dict]:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(f"--user-agent={HEADERS['User-Agent']}")

    driver = None
    metadata = {
        "challenge_resuelto": False,
        "detector_selector": None,
        "estado_browser": None,
        "title_final": None,
        "cookies_count": 0,
        "click_verificacion": None,
    }
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(TARGET_URL)
        log.info("Chrome visible abierto en %s", TARGET_URL)

        challenge_resuelto, detalle = _esperar_contenido_real(driver, timeout_segundos=timeout_segundos)
        metadata["click_verificacion"] = "no_necesario"
        if not challenge_resuelto:
            challenge_activo, _ = _challenge_sigue_activo(driver)
            if challenge_activo:
                challenge_resuelto, detalle = _resolver_challenge_con_apoyo_manual(
                    driver,
                    timeout_segundos=max(120, timeout_segundos),
                )
                metadata["click_verificacion"] = "automatico_o_manual_intentado"

        metadata["challenge_resuelto"] = challenge_resuelto
        metadata["estado_browser"] = "contenido_real_detectado" if challenge_resuelto else detalle
        if challenge_resuelto:
            metadata["detector_selector"] = detalle

        html = driver.page_source or ""
        final_url = driver.current_url or TARGET_URL
        metadata["title_final"] = driver.title
        metadata["cookies_count"] = len(driver.get_cookies())

        log.info(
            "Browser visible | URL final: %s | t?tulo: %s | html_len=%d | cookies=%d | estado=%s",
            final_url,
            driver.title,
            len(html),
            metadata["cookies_count"],
            metadata["estado_browser"],
        )
        return html, final_url, metadata
    except WebDriverException as exc:
        metadata["estado_browser"] = f"webdriver_error: {exc}"
        return "", TARGET_URL, metadata
    finally:
        if driver is not None:
            driver.quit()



def run_test():
    setup_logging()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": TARGET_URL,
        "status_code": None,
        "url_final": None,
        "content_type": None,
        "selectores": [],
        "selector_recomendado": SOURCE_FIXTURE["scrape_selector"],
        "noticias_encontradas": 0,
        "noticias_preview": [],
        "scraper_generico_encontradas": 0,
        "scraper_generico_preview": [],
        "estado": "iniciado",
        "error": None,
        "browser": {},
    }

    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(TARGET_URL)

        html = resp.text
        final_url = str(resp.url)
        resultado["status_code"] = resp.status_code
        resultado["url_final"] = final_url
        resultado["content_type"] = resp.headers.get("content-type", "")
        log.info(
            "HTTP %s | URL final: %s | Content-Type: %s",
            resp.status_code,
            resp.url,
            resp.headers.get("content-type", ""),
        )

        usar_browser = resp.status_code == 403 or _html_parece_challenge(html)
        if usar_browser:
            log.warning("HTTP 403/challenge detectado en Rumbo Minero. Intentando browser visible para validar noticias reales...")
            browser_html, browser_url, browser_meta = _descargar_html_con_browser_visible(timeout_segundos=60)
            resultado["browser"] = browser_meta
            if browser_html:
                html = browser_html
                final_url = browser_url
                resultado["url_final"] = browser_url
                resultado["content_type"] = "text/html; browser-rendered-visible"
            else:
                raise RuntimeError(browser_meta.get("estado_browser") or "Browser visible no devolvi? HTML")

        soup = BeautifulSoup(html, "html.parser")
        resultado["selectores"] = _probar_selectores(soup, final_url)
        noticias = _extraer_noticias_desde_modulos(soup, final_url)
        scraper_preview = _comparar_con_scraper_generico(soup, final_url)

        resultado["noticias_encontradas"] = len(noticias)
        resultado["noticias_preview"] = noticias[:20]
        resultado["scraper_generico_encontradas"] = len(scraper_preview)
        resultado["scraper_generico_preview"] = scraper_preview
        resultado["estado"] = "ok"

        log.info(
            "Resumen test Rumbo Minero | noticias_modulos=%d | scraper_generico=%d",
            len(noticias),
            len(scraper_preview),
        )

    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error ejecutando test de Rumbo Minero: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _guardar_resultado(resultado)

    log.info("=== FIN TEST RUMBO MINERO ===")
    return resultado


if __name__ == "__main__":
    run_test()
