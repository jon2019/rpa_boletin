"""
Diagnóstico de scraping para https://www.acades.cl

Objetivo:
- Validar el extractor combinado: home (selector CSS) + /noticias/ (WP REST API).
- Mostrar noticias del mes actual obtenidas desde la API.
- Confirmar que el total es superior al selector del home solo.

Ejecución desde la raíz del proyecto:
    python tests/helper/integration/test_acades.py

Salidas:
- tests/output/integration/test_acades.log
- tests/output/integration/test_acades_resultado.json
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from boletin.scraping.extraction import extract_acades_with_api, extract_noticias_from_soup

log = logging.getLogger("test_acades")

BASE_URL = "https://www.acades.cl"
RESULT_DIR = ROOT_DIR / "tests" / "output" / "integration"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON = RESULT_DIR / "test_acades_resultado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9",
}

SELECTOR_HOME = ".jet-smart-listing__featured-box-link, .jet-smart-listing__post-title a"

SOURCE_FIXTURE = {
    "id": 37,
    "name": "ACADES",
    "url": BASE_URL,
    "country": "Chile",
    "scrape_selector": SELECTOR_HOME,
}


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

    log_file = RESULT_DIR / "test_acades.log"
    if not any(
        isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == log_file
        for h in root.handlers
    ):
        fh = logging.FileHandler(str(log_file), encoding="utf-8", mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return log_file


def run_test() -> dict:
    setup_logging()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "url_home": BASE_URL,
        "url_noticias": f"{BASE_URL}/noticias/",
        "status_code_home": None,
        "html_size_chars": 0,
        "noticias_solo_home": 0,
        "noticias_combinado": 0,
        "noticias_de_api": 0,
        "noticias_preview": [],
        "estado": "iniciado",
        "error": None,
    }

    try:
        log.info("Descargando home: %s", BASE_URL)
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            resp = client.get(BASE_URL)

        resultado["status_code_home"] = resp.status_code
        resultado["html_size_chars"] = len(resp.text)
        log.info("HTTP %s | %d chars", resp.status_code, len(resp.text))

        soup = BeautifulSoup(resp.text, "html.parser")

        log.info("Extrayendo solo con selector home...")
        noticias_home = extract_noticias_from_soup(SOURCE_FIXTURE, soup, BASE_URL, "test_home_only")
        resultado["noticias_solo_home"] = len(noticias_home)
        log.info("Solo home: %d noticias", len(noticias_home))

        log.info("Extrayendo con extractor combinado (home + WP REST API)...")
        noticias_combo = extract_acades_with_api(SOURCE_FIXTURE, soup, BASE_URL, "test_combinado")
        resultado["noticias_combinado"] = len(noticias_combo)
        resultado["noticias_de_api"] = len(noticias_combo) - len(noticias_home)
        log.info("Combinado: %d noticias (%d adicionales de la API)", len(noticias_combo), resultado["noticias_de_api"])

        resultado["noticias_preview"] = [
            {"titulo": n.get("titulo", ""), "url": n.get("url", "")}
            for n in noticias_combo[:20]
        ]

        resultado["estado"] = "ok"
        log.info(
            "=== RESUMEN === | home=%d | combinado=%d | adicionales_api=%d",
            len(noticias_home),
            len(noticias_combo),
            resultado["noticias_de_api"],
        )

    except Exception as exc:
        resultado["estado"] = "error"
        resultado["error"] = str(exc)
        log.exception("Error en test ACADES: %s", exc)
    finally:
        resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
        RESULT_JSON.write_text(
            json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Resultado guardado: %s", RESULT_JSON)

    log.info("=== FIN TEST ACADES ===")
    return resultado


if __name__ == "__main__":
    resultado = run_test()
    print(f"\nSolo home     : {resultado['noticias_solo_home']} noticias")
    print(f"Combinado     : {resultado['noticias_combinado']} noticias")
    print(f"De la API     : {resultado['noticias_de_api']} adicionales")
    print(f"\nNoticias encontradas:")
    for n in resultado.get("noticias_preview", []):
        print(f"  - {n['titulo'][:80]}")
        print(f"    -> {n['url']}")
