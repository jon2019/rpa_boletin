"""
Diagn?stico de scraping para https://www.mineriaydesarrollo.com

Objetivo:
- validar selectores reales contra el HTML actual del home
- detectar noticias destacadas y simples
- comparar el resultado del test con la extracci?n gen?rica del scraper productivo
- dejar evidencia en JSON para comparar luego con el scraper productivo

Ejecuci?n desde la ra?z del proyecto:
    python boletin/test_mineriaydesarrollo.py

Salidas:
- tests/output/integration/test_mineriaydesarrollo.log
- tests/output/integration/test_mineriaydesarrollo_resultado.json
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
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from boletin.scraper import _extract_noticias_from_soup


load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("test_mineriaydesarrollo")
BASE_URL = "https://www.mineriaydesarrollo.com"
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_mineriaydesarrollo_resultado.json"

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
    "name": "Miner?a y Desarrollo",
    "url": BASE_URL,
    "country": "Argentina",
    "scrape_selector": "article.noticia-destacada a[href*='/noticias/'], article.noticia-simple a[href*='/noticias/'], article h2 a[href*='/noticias/']",
}

SELECTORES_CANDIDATOS = [
    "article.noticia-destacada",
    "article.noticia-simple",
    "article.noticia-destacada a[href*='/noticias/']",
    "article.noticia-simple a[href*='/noticias/']",
    "article h2 a[href*='/noticias/']",
    "a[href*='/noticias/'][aria-label]",
    "article a[href*='/noticias/']",
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

    log_file = RESULT_DIR / "test_mineriaydesarrollo.log"
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


def _extraer_titulo_desde_article(article) -> str:
    h2 = article.select_one("h2")
    if h2:
        titulo = _normalizar_texto(h2.get_text(" ", strip=True))
        if titulo:
            return titulo

    anchor_textual = article.select_one(".div-info a[href*='/noticias/'], h2 a[href*='/noticias/']")
    if anchor_textual:
        titulo = _normalizar_texto(anchor_textual.get_text(" ", strip=True))
        if titulo:
            return titulo

    anchor = article.select_one("a[href*='/noticias/']")
    if anchor:
        titulo = (anchor.get("aria-label") or "").strip()
        if titulo:
            return titulo
        titulo = _normalizar_texto(anchor.get_text(" ", strip=True))
        if titulo:
            return titulo

    return ""


def _extraer_noticias_por_articles(soup: BeautifulSoup) -> list[dict]:
    noticias: list[dict] = []
    seen: set[str] = set()

    for article in soup.select("article.noticia-destacada, article.noticia-simple"):
        anchor = article.select_one("a[href*='/noticias/']")
        if not anchor:
            continue

        href = urljoin(BASE_URL, (anchor.get("href") or "").strip())
        if not href or href in seen:
            continue

        titulo = _extraer_titulo_desde_article(article)
        if not titulo:
            continue

        categoria_tag = article.select_one(".categoria a")
        categoria = _normalizar_texto(categoria_tag.get_text(" ", strip=True)) if categoria_tag else ""

        copete_tag = article.select_one(".text-noticia-destacada-copete, .text-noticia-simple-copete")
        copete = _normalizar_texto(copete_tag.get_text(" ", strip=True)) if copete_tag else ""

        noticia = {
            "titulo": titulo[:220],
            "url": href,
            "categoria": categoria,
            "copete": copete[:240],
            "tipo": "destacada" if "noticia-destacada" in (article.get("class") or []) else "simple",
        }
        noticias.append(noticia)
        seen.add(href)
        log.info(
            "Noticia detectada | tipo=%s | categoria=%s | titulo=%s | url=%s",
            noticia["tipo"],
            categoria,
            titulo[:140],
            href,
        )

    return noticias


def _probar_selectores(soup: BeautifulSoup) -> list[dict]:
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
            texto = _normalizar_texto(node.get_text(" ", strip=True))[:160]
            href = ""
            if getattr(node, "name", None) == "a":
                href = urljoin(BASE_URL, (node.get("href") or "").strip())
            else:
                parent_a = node.select_one("a[href*='/noticias/']") or node.find_parent("a")
                if parent_a:
                    href = urljoin(BASE_URL, (parent_a.get("href") or "").strip())
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
            "Test Miner?a y Desarrollo",
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


def run_test():
    setup_logging()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": BASE_URL,
        "status_code": None,
        "url_final": None,
        "content_type": None,
        "selectores": [],
        "selector_recomendado": None,
        "noticias_encontradas": 0,
        "noticias_preview": [],
        "scraper_generico_encontradas": 0,
        "scraper_generico_preview": [],
        "estado": "iniciado",
        "error": None,
    }

    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(BASE_URL)

        resultado["status_code"] = resp.status_code
        resultado["url_final"] = str(resp.url)
        resultado["content_type"] = resp.headers.get("content-type", "")
        log.info(
            "HTTP %s | URL final: %s | Content-Type: %s",
            resp.status_code,
            resp.url,
            resp.headers.get("content-type", ""),
        )

        soup = BeautifulSoup(resp.text, "html.parser")

        resultado["selectores"] = _probar_selectores(soup)
        noticias = _extraer_noticias_por_articles(soup)
        scraper_preview = _comparar_con_scraper_generico(soup, str(resp.url))

        resultado["noticias_encontradas"] = len(noticias)
        resultado["noticias_preview"] = noticias[:15]
        resultado["scraper_generico_encontradas"] = len(scraper_preview)
        resultado["scraper_generico_preview"] = scraper_preview
        resultado["selector_recomendado"] = (
            "article.noticia-destacada a[href*='/noticias/'], "
            "article.noticia-simple a[href*='/noticias/'], "
            "article h2 a[href*='/noticias/']"
        )
        resultado["estado"] = "ok"

        log.info(
            "Resumen test Miner?a y Desarrollo | noticias_articles=%d | scraper_generico=%d",
            len(noticias),
            len(scraper_preview),
        )

    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error ejecutando test de Miner?a y Desarrollo: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _guardar_resultado(resultado)

    log.info("=== FIN TEST MINERIA Y DESARROLLO ===")
    return resultado


if __name__ == "__main__":
    run_test()
