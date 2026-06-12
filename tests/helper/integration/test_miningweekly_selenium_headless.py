"""
Diagn?stico headless con Selenium para https://www.miningweekly.com

Objetivo:
- probar una variante oculta (sin ventana visible) del test de Mining Weekly
- verificar si Chrome headless moderno logra cargar el home internacional
- capturar el HTML renderizado real y probar selectores de noticias
- comparar el resultado con la extracci?n gen?rica del scraper productivo

Ejecuci?n desde la ra?z del proyecto:
    python boletin/test_miningweekly_selenium_headless.py

Salidas:
- tests/output/integration/test_miningweekly_selenium_headless.log
- tests/output/integration/test_miningweekly_selenium_headless_resultado.json
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
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from boletin.scraper import _extract_noticias_from_soup


load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("test_miningweekly_selenium_headless")
BASE_URL = "https://www.miningweekly.com"
TARGET_URL = "https://www.miningweekly.com/page/international-home"
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_miningweekly_selenium_headless_resultado.json"

SOURCE_FIXTURE = {
    "name": "Mining Weekly",
    "url": BASE_URL,
    "country": "Internacional",
    "scrape_selector": "a.card-title[href*='/article/'], a.headline-link[href*='/article/'], a.headline-link-projects[href*='/article/'], a.image-link[href*='/article/'], a.image-link-projects[href*='/article/']",
}

SELECTORES_CANDIDATOS = [
    "a.card-title[href*='/article/']",
    "a.headline-link[href*='/article/']",
    "a.headline-link-projects[href*='/article/']",
    "a.image-link[href*='/article/']",
    "a.image-link-projects[href*='/article/']",
    ".entry a[href*='/article/']",
    ".card-body a[href*='/article/']",
    "article a[href*='/article/']",
]

CLOUDFLARE_TITLE_TOKENS = {
    "un momento",
    "just a moment",
    "please wait",
    "checking your browser",
}
CLOUDFLARE_BODY_TOKENS = (
    "checking your browser",
    "verify you are human",
    "ray id",
    "cloudflare",
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

    log_file = RESULT_DIR / "test_miningweekly_selenium_headless.log"
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



def _extraer_titulo_desde_anchor(anchor) -> str:
    titulo = _normalizar_texto(anchor.get_text(" ", strip=True))
    if titulo:
        return titulo

    titulo = _normalizar_texto((anchor.get("aria-label") or anchor.get("title") or ""))
    if titulo:
        return titulo

    img = anchor.select_one("img[alt]")
    if img:
        titulo = _normalizar_texto(img.get("alt") or "")
        if titulo:
            return titulo

    return ""



def _extraer_categoria_desde_container(container) -> str:
    if not container:
        return ""

    categoria_node = container.select_one(
        ".btn-news, .cm-sponsored-post, .category, .categoria"
    )
    if categoria_node:
        return _normalizar_texto(categoria_node.get_text(" ", strip=True))

    return ""



def _extraer_noticias_por_cards(soup: BeautifulSoup, base_url: str) -> list[dict]:
    noticias: list[dict] = []
    seen: set[str] = set()

    for selector in SELECTORES_CANDIDATOS:
        for anchor in soup.select(selector):
            href = urljoin(base_url, (anchor.get("href") or "").strip())
            if not href or "/article/" not in href or href in seen:
                continue

            titulo = _extraer_titulo_desde_anchor(anchor)
            if not titulo:
                continue

            container = anchor.find_parent(
                class_=lambda value: value and any(
                    token in " ".join(value).lower()
                    for token in ("card", "entry")
                )
            )
            categoria = _extraer_categoria_desde_container(container)

            noticia = {
                "titulo": titulo[:220],
                "url": href,
                "categoria": categoria,
                "selector": selector,
            }
            noticias.append(noticia)
            seen.add(href)
            log.info(
                "Noticia detectada | selector=%s | categoria=%s | titulo=%s | url=%s",
                selector,
                categoria,
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
            resultados.append(
                {
                    "selector": selector,
                    "total": 0,
                    "error": str(exc),
                    "preview": [],
                }
            )
            continue

        preview = []
        for node in nodes[:5]:
            texto = _extraer_titulo_desde_anchor(node)[:160]
            href = urljoin(base_url, (node.get("href") or "").strip())
            preview.append({"texto": texto, "href": href})

        resultados.append(
            {
                "selector": selector,
                "total": len(nodes),
                "preview": preview,
            }
        )
        log.info("Selector '%s' encontr? %d nodos", selector, len(nodes))

    return resultados



def _comparar_con_scraper_generico(soup: BeautifulSoup, base_url: str) -> list[dict]:
    try:
        noticias = _extract_noticias_from_soup(
            SOURCE_FIXTURE,
            soup,
            base_url,
            "Test Mining Weekly Selenium Headless",
        )
    except Exception as exc:
        log.exception("Fall? la comparaci?n con _extract_noticias_from_soup: %s", exc)
        return [{"titulo": "ERROR", "url": str(exc)}]

    preview = []
    for noticia in noticias[:15]:
        preview.append(
            {
                "titulo": noticia.get("titulo", ""),
                "url": noticia.get("url", ""),
            }
        )
    return preview



def _page_is_challenge(driver: webdriver.Chrome) -> bool:
    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""
    try:
        body_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        body_text = ""

    return any(token in title for token in CLOUDFLARE_TITLE_TOKENS) or any(
        token in body_text for token in CLOUDFLARE_BODY_TOKENS
    )



def _esperar_contenido_real(driver: webdriver.Chrome, timeout_segundos: int = 45) -> tuple[bool, str]:
    inicio = time.time()
    ultimo_estado = "desconocido"

    while time.time() - inicio < timeout_segundos:
        try:
            current_title = driver.title or ""
        except Exception:
            current_title = ""

        current_url = driver.current_url or TARGET_URL
        challenge_activo = _page_is_challenge(driver)
        if challenge_activo:
            ultimo_estado = f"challenge_activo | title={current_title} | url={current_url}"
            log.info("Challenge activo ? esperando... | title=%s | url=%s", current_title, current_url)
        else:
            for selector in SELECTORES_CANDIDATOS:
                try:
                    nodes = driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    nodes = []
                if nodes:
                    log.info("Contenido real detectado con selector '%s' (%d nodos)", selector, len(nodes))
                    return True, selector

            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
                )
            except TimeoutException:
                pass

            ultimo_estado = f"sin_challenge_pero_sin_selectores | title={current_title} | url={current_url}"
            log.info("Sin challenge aparente, pero todav?a sin selectores de noticias | title=%s", current_title)

        time.sleep(2)

    return False, ultimo_estado



def _descargar_html_con_selenium_headless(timeout_segundos: int = 45) -> tuple[str, str, dict]:
    """
    Levanta Chrome headless moderno para probar si el sitio responde igual que en visible.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = None
    metadata = {
        "challenge_resuelto": False,
        "detector_selector": None,
        "estado_browser": None,
        "title_final": None,
        "cookies_count": 0,
        "modo": "headless",
    }
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(TARGET_URL)
        log.info("Chrome headless abierto en %s", TARGET_URL)

        challenge_resuelto, detalle = _esperar_contenido_real(driver, timeout_segundos=timeout_segundos)
        metadata["challenge_resuelto"] = challenge_resuelto
        if challenge_resuelto:
            metadata["detector_selector"] = detalle
            metadata["estado_browser"] = "contenido_real_detectado"
        else:
            metadata["estado_browser"] = detalle

        html = driver.page_source or ""
        final_url = driver.current_url or TARGET_URL
        metadata["title_final"] = driver.title
        metadata["cookies_count"] = len(driver.get_cookies())

        log.info(
            "Selenium headless | URL final: %s | t?tulo: %s | html_len=%d | cookies=%d | estado=%s",
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
        "url_final": None,
        "content_type": "text/html; browser-rendered-headless",
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
        html, final_url, browser_meta = _descargar_html_con_selenium_headless(timeout_segundos=45)
        resultado["url_final"] = final_url
        resultado["browser"] = browser_meta

        if not html:
            raise RuntimeError(browser_meta.get("estado_browser") or "Selenium headless no devolvi? HTML")

        soup = BeautifulSoup(html, "html.parser")
        resultado["selectores"] = _probar_selectores(soup, final_url)
        noticias = _extraer_noticias_por_cards(soup, final_url)
        scraper_preview = _comparar_con_scraper_generico(soup, final_url)

        resultado["noticias_encontradas"] = len(noticias)
        resultado["noticias_preview"] = noticias[:15]
        resultado["scraper_generico_encontradas"] = len(scraper_preview)
        resultado["scraper_generico_preview"] = scraper_preview
        resultado["estado"] = "ok" if browser_meta.get("challenge_resuelto") else "cloudflare_blocked"

        log.info(
            "Resumen test Mining Weekly Selenium headless | noticias_cards=%d | scraper_generico=%d | estado=%s",
            len(noticias),
            len(scraper_preview),
            resultado["estado"],
        )
    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error ejecutando test Selenium headless de Mining Weekly: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _guardar_resultado(resultado)

    log.info("=== FIN TEST MINING WEEKLY SELENIUM HEADLESS ===")
    return resultado


if __name__ == "__main__":
    run_test()
