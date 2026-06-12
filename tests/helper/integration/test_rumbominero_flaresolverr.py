"""
Diagnóstico de acceso a Rumbo Minero vía FlareSolverr.

Objetivo:
- validar si FlareSolverr puede resolver el challenge de Cloudflare
- guardar HTML renderizado, cookies y screenshot devueltos por FlareSolverr
- probar los mismos selectores de noticias usados en el test local
- comparar contra la extracción genérica del scraper productivo

Ejecución desde la raíz del proyecto:
    .\.venv\Scripts\python.exe boletin/test_rumbominero_flaresolverr.py

Requisitos:
- FlareSolverr corriendo en http://127.0.0.1:8191 (o FLARESOLVERR_URL)

Salidas:
- tests/output/integration/test_rumbominero_flaresolverr.log
- tests/output/integration/test_rumbominero_flaresolverr_resultado.json
- tests/output/integration/test_rumbominero_flaresolverr.html
- tests/output/integration/test_rumbominero_flaresolverr.png (si FlareSolverr devuelve screenshot)
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from boletin.scraping.extraction import extract_noticias_from_soup as _extract_noticias_from_soup


load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("test_rumbominero_flaresolverr")
BASE_URL = "https://www.rumbominero.com"
TARGET_URL = BASE_URL
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191/v1")
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_rumbominero_flaresolverr_resultado.json"
RESULT_HTML = RESULT_DIR / "test_rumbominero_flaresolverr.html"
RESULT_PNG = RESULT_DIR / "test_rumbominero_flaresolverr.png"

SOURCE_FIXTURE = {
    "name": "Rumbo Minero",
    "url": BASE_URL,
    "country": "Perú",
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
    "verificación de seguridad en curso",
    "verifique que es un ser humano",
    "checking your browser",
    "cloudflare",
    "verify you are human",
    "un momento",
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

    log_file = RESULT_DIR / "test_rumbominero_flaresolverr.log"
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


def _html_parece_challenge(html: str, title: str = "") -> bool:
    sample = f"{title}\n{html[:40000]}".lower()
    return any(token in sample for token in CHALLENGE_TEXT_TOKENS)


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
        log.info("Selector '%s' encontró %d nodos", selector, len(nodes))

    return resultados


def _comparar_con_scraper_generico(soup: BeautifulSoup, base_url: str) -> list[dict]:
    try:
        noticias = _extract_noticias_from_soup(
            SOURCE_FIXTURE,
            soup,
            base_url,
            "Test Rumbo Minero FlareSolverr",
        )
    except Exception as exc:
        log.exception("Falló la comparación con _extract_noticias_from_soup: %s", exc)
        return [{"titulo": "ERROR", "url": str(exc)}]

    return [
        {
            "titulo": noticia.get("titulo", ""),
            "url": noticia.get("url", ""),
        }
        for noticia in noticias[:20]
    ]


def _guardar_screenshot_base64(base64_png: str) -> str | None:
    if not base64_png:
        return None
    try:
        RESULT_PNG.write_bytes(base64.b64decode(base64_png))
        log.info("Screenshot guardado: %s", RESULT_PNG)
        return str(RESULT_PNG)
    except Exception as exc:
        log.warning("No pude guardar screenshot base64: %s", exc)
        return None


def _llamar_flaresolverr() -> dict:
    payload = {
        "cmd": "request.get",
        "url": TARGET_URL,
        "maxTimeout": 180000,
        "waitInSeconds": 8,
        "returnScreenshot": True,
    }

    log.info("Llamando a FlareSolverr en %s", FLARESOLVERR_URL)
    log.info("Payload: %s", payload)

    with httpx.Client(timeout=190, follow_redirects=True) as client:
        response = client.post(
            FLARESOLVERR_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def run_test() -> dict:
    setup_logging()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target_url": TARGET_URL,
        "flaresolverr_url": FLARESOLVERR_URL,
        "status": "iniciado",
        "error": None,
        "flaresolverr_status": None,
        "flaresolverr_message": None,
        "solution_status_code": None,
        "solution_url": None,
        "solution_title": None,
        "solution_user_agent": None,
        "cookies_count": 0,
        "challenge_persistente": None,
        "selectores": [],
        "noticias_encontradas": 0,
        "noticias_preview": [],
        "scraper_generico_encontradas": 0,
        "scraper_generico_preview": [],
        "html_guardado": None,
        "screenshot_guardado": None,
    }

    try:
        flaresolverr_response = _llamar_flaresolverr()
        resultado["flaresolverr_status"] = flaresolverr_response.get("status")
        resultado["flaresolverr_message"] = flaresolverr_response.get("message")

        if flaresolverr_response.get("status") != "ok":
            raise RuntimeError(
                f"FlareSolverr respondió status={flaresolverr_response.get('status')} "
                f"message={flaresolverr_response.get('message')}"
            )

        solution = flaresolverr_response.get("solution") or {}
        html = solution.get("response") or ""
        final_url = solution.get("url") or TARGET_URL
        headers = solution.get("headers") or {}
        user_agent = solution.get("userAgent")
        cookies = solution.get("cookies") or []
        screenshot_b64 = solution.get("screenshot") or ""

        RESULT_HTML.write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "html.parser")
        title = _normalizar_texto((soup.title.string if soup.title and soup.title.string else ""))
        challenge_persistente = _html_parece_challenge(html, title)
        selector_resultados = _probar_selectores(soup, final_url)
        noticias = _extraer_noticias_desde_modulos(soup, final_url)
        scraper_preview = _comparar_con_scraper_generico(soup, final_url)
        screenshot_path = _guardar_screenshot_base64(screenshot_b64)

        resultado["solution_status_code"] = solution.get("status") or headers.get("status")
        resultado["solution_url"] = final_url
        resultado["solution_title"] = title
        resultado["solution_user_agent"] = user_agent
        resultado["cookies_count"] = len(cookies)
        resultado["challenge_persistente"] = challenge_persistente
        resultado["selectores"] = selector_resultados
        resultado["noticias_encontradas"] = len(noticias)
        resultado["noticias_preview"] = noticias[:20]
        resultado["scraper_generico_encontradas"] = len(scraper_preview)
        resultado["scraper_generico_preview"] = scraper_preview
        resultado["html_guardado"] = str(RESULT_HTML)
        resultado["screenshot_guardado"] = screenshot_path
        resultado["status"] = "ok"

        log.info(
            "Resumen FlareSolverr | solution_status=%s | title=%s | cookies=%d | challenge=%s | noticias=%d | scraper_generico=%d",
            resultado["solution_status_code"],
            title,
            len(cookies),
            challenge_persistente,
            len(noticias),
            len(scraper_preview),
        )

    except Exception as exc:
        resultado["status"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error ejecutando test de Rumbo Minero con FlareSolverr: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _guardar_resultado(resultado)

    log.info("=== FIN TEST RUMBO MINERO / FLARESOLVERR ===")
    return resultado


if __name__ == "__main__":
    run_test()
