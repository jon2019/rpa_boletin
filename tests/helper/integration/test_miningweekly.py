"""
Diagn?stico de scraping para https://www.miningweekly.com

Objetivo:
- validar selectores reales contra el HTML actual del home internacional
- detectar noticias visibles del layout de cards
- comparar el resultado del test con la extracci?n gen?rica del scraper productivo
- dejar evidencia en JSON para comparar luego con el scraper productivo

Ejecuci?n desde la ra?z del proyecto:
    python boletin/test_miningweekly.py

Salidas:
- tests/output/integration/test_miningweekly.log
- tests/output/integration/test_miningweekly_resultado.json
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

import httpx
import time as _time
import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.webdriver.chrome.options import Options

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from boletin.scraper import _extract_noticias_from_soup, _get_local_chrome_major_version


load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("test_miningweekly")
BASE_URL = "https://www.miningweekly.com"
TARGET_URL = "https://www.miningweekly.com/page/international-home"
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_miningweekly_resultado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

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

    log_file = RESULT_DIR / "test_miningweekly.log"
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
            "Test Mining Weekly",
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


def _descargar_html_con_browser() -> tuple[str, str, str | None]:
    """
    Intenta obtener el DOM renderizado con undetected-chromedriver para esquivar el 403.
    Retorna (html, url_final, error).
    """
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    chrome_major = _get_local_chrome_major_version()
    driver = None
    try:
        if chrome_major:
            log.info("Inicializando UC Chrome con version_main=%s", chrome_major)
            try:
                driver = uc.Chrome(headless=True, options=options, version_main=chrome_major)
            except Exception as exc:
                log.warning("UC fall? con version_main=%s: %s. Reintentando autodetecci?n.", chrome_major, exc)
        if driver is None:
            driver = uc.Chrome(headless=True, options=options)

        driver.get(TARGET_URL)
        cloudflare_titles = {"un momento", "just a moment", "please wait", "checking your browser"}
        waited = 0
        while waited < 20:
            try:
                current_title = (driver.title or "").lower()
            except Exception:
                current_title = ""
            if not any(token in current_title for token in cloudflare_titles):
                break
            log.info("Cloudflare challenge activo ('%s') ? esperando... (%ds)", driver.title, waited)
            _time.sleep(2)
            waited += 2

        html = driver.page_source or ""
        final_url = driver.current_url or TARGET_URL
        log.info("Browser fallback | URL final: %s | t?tulo: %s | html_len=%d", final_url, driver.title, len(html))
        return html, final_url, None
    except Exception as exc:
        return "", TARGET_URL, str(exc)
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
    }

    try:
        html = ""
        final_url = TARGET_URL
        content_type = ""
        status_code = None
        browser_error = None

        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(TARGET_URL)

        status_code = resp.status_code
        final_url = str(resp.url)
        content_type = resp.headers.get("content-type", "")
        html = resp.text
        resultado["status_code"] = status_code
        resultado["url_final"] = final_url
        resultado["content_type"] = content_type
        log.info(
            "HTTP %s | URL final: %s | Content-Type: %s",
            status_code,
            final_url,
            content_type,
        )

        usar_browser = status_code == 403
        if usar_browser:
            log.warning("HTTP 403 detectado en Mining Weekly. Intentando browser fallback para validar noticias reales...")
            browser_html, browser_url, browser_error = _descargar_html_con_browser()
            if browser_html:
                html = browser_html
                final_url = browser_url
                resultado["url_final"] = browser_url
                resultado["content_type"] = "text/html; browser-rendered"
            elif browser_error:
                log.warning("Browser fallback tambi?n fall?: %s", browser_error)

        soup = BeautifulSoup(html, "html.parser")

        resultado["selectores"] = _probar_selectores(soup, final_url)
        noticias = _extraer_noticias_por_cards(soup, final_url)
        scraper_preview = _comparar_con_scraper_generico(soup, final_url)

        resultado["noticias_encontradas"] = len(noticias)
        resultado["noticias_preview"] = noticias[:15]
        resultado["scraper_generico_encontradas"] = len(scraper_preview)
        resultado["scraper_generico_preview"] = scraper_preview
        resultado["estado"] = "ok"
        if browser_error:
            resultado["error"] = browser_error

        log.info(
            "Resumen test Mining Weekly | noticias_cards=%d | scraper_generico=%d",
            len(noticias),
            len(scraper_preview),
        )

    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error ejecutando test de Mining Weekly: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _guardar_resultado(resultado)

    log.info("=== FIN TEST MINING WEEKLY ===")
    return resultado


if __name__ == "__main__":
    run_test()
