"""
Diagnóstico de scraping para https://laprensaaustral.cl

Objetivo:
- Validar el selector combinado que extrae noticias de la columna principal
  (.articulos .noticia-titulo a) y de las secciones de categorías
  (.articulosCategoria .noticiaCategoria a).
- Comparar cantidad de artículos con el selector anterior (solo columna principal).
- Mostrar distribución por año/mes para detectar si se cuelan artículos viejos.
- Guardar evidencia en JSON para diagnóstico posterior.

Ejecución desde la raíz del proyecto:
    python tests/helper/integration/test_laprensaaustral.py

Salidas:
- tests/output/integration/test_laprensaaustral.log
- tests/output/integration/test_laprensaaustral_resultado.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from boletin.scraping.extraction import extract_noticias_from_soup

log = logging.getLogger("test_laprensaaustral")

BASE_URL = "https://laprensaaustral.cl"
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_laprensaaustral_resultado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}

SELECTOR_VIEJO = ".articulos .noticia-titulo a"
SELECTOR_NUEVO = ".articulos .noticia-titulo a, .articulosCategoria .noticiaCategoria a"

SOURCE_FIXTURE_VIEJO = {
    "id": 34,
    "name": "La Prensa Austral",
    "url": BASE_URL,
    "country": "Chile",
    "scrape_selector": SELECTOR_VIEJO,
}

SOURCE_FIXTURE_NUEVO = {
    "id": 34,
    "name": "La Prensa Austral",
    "url": BASE_URL,
    "country": "Chile",
    "scrape_selector": SELECTOR_NUEVO,
}

RE_FECHA = re.compile(r"/(20\d{2})/(\d{2})/")


def setup_logging() -> Path:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in root.handlers
    ):
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    log_file = RESULT_DIR / "test_laprensaaustral.log"
    if not any(
        isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == log_file
        for h in root.handlers
    ):
        fh = logging.FileHandler(str(log_file), encoding="utf-8", mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return log_file


def _distribucion_por_anio_mes(noticias: list[dict]) -> dict:
    conteo: dict[str, int] = defaultdict(int)
    for n in noticias:
        m = RE_FECHA.search(n.get("url", ""))
        if m:
            clave = f"{m.group(1)}/{m.group(2)}"
        else:
            clave = "sin_fecha"
        conteo[clave] += 1
    return dict(sorted(conteo.items(), reverse=True))


def run_test() -> dict:
    setup_logging()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url": BASE_URL,
        "status_code": None,
        "html_size_chars": 0,
        "selector_viejo": SELECTOR_VIEJO,
        "selector_nuevo": SELECTOR_NUEVO,
        "noticias_viejo": 0,
        "noticias_nuevo": 0,
        "adicionales": 0,
        "distribucion_viejo": {},
        "distribucion_nuevo": {},
        "noticias_preview_nuevo": [],
        "estado": "iniciado",
        "error": None,
    }

    try:
        log.info("Descargando %s ...", BASE_URL)
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(BASE_URL)

        resultado["status_code"] = resp.status_code
        resultado["html_size_chars"] = len(resp.text)
        log.info("HTTP %s | %d chars | URL final: %s", resp.status_code, len(resp.text), resp.url)

        soup = BeautifulSoup(resp.text, "html.parser")

        log.info("Extrayendo con selector VIEJO: %s", SELECTOR_VIEJO)
        noticias_viejo = extract_noticias_from_soup(SOURCE_FIXTURE_VIEJO, soup, BASE_URL, "test_viejo")
        resultado["noticias_viejo"] = len(noticias_viejo)
        resultado["distribucion_viejo"] = _distribucion_por_anio_mes(noticias_viejo)
        log.info("Selector VIEJO: %d noticias", len(noticias_viejo))

        log.info("Extrayendo con selector NUEVO: %s", SELECTOR_NUEVO)
        noticias_nuevo = extract_noticias_from_soup(SOURCE_FIXTURE_NUEVO, soup, BASE_URL, "test_nuevo")
        resultado["noticias_nuevo"] = len(noticias_nuevo)
        resultado["distribucion_nuevo"] = _distribucion_por_anio_mes(noticias_nuevo)
        log.info("Selector NUEVO: %d noticias", len(noticias_nuevo))

        urls_viejas = {n["url"] for n in noticias_viejo}
        adicionales = [n for n in noticias_nuevo if n["url"] not in urls_viejas]
        resultado["adicionales"] = len(adicionales)

        resultado["noticias_preview_nuevo"] = [
            {"titulo": n.get("titulo", ""), "url": n.get("url", "")}
            for n in noticias_nuevo[:20]
        ]

        resultado["estado"] = "ok"

        log.info(
            "=== RESUMEN === | viejo=%d | nuevo=%d | adicionales=%d",
            len(noticias_viejo),
            len(noticias_nuevo),
            len(adicionales),
        )
        log.info("Distribución por año/mes (nuevo selector):")
        for clave, cnt in resultado["distribucion_nuevo"].items():
            log.info("  %s: %d articulos", clave, cnt)

    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error en test La Prensa Austral: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        RESULT_JSON.write_text(
            json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Resultado guardado: %s", RESULT_JSON)

    log.info("=== FIN TEST LA PRENSA AUSTRAL ===")
    return resultado


if __name__ == "__main__":
    resultado = run_test()
    print(f"\nSelector viejo : {resultado['noticias_viejo']} noticias")
    print(f"Selector nuevo : {resultado['noticias_nuevo']} noticias")
    print(f"Adicionales    : {resultado['adicionales']}")
    print(f"\nDistribución por año/mes:")
    for clave, cnt in resultado.get("distribucion_nuevo", {}).items():
        print(f"  {clave}: {cnt}")
