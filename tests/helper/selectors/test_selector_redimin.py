from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_redimin.py
------------------------
Diagnostico y configuracion del selector CSS para redimin.cl.

OBJETIVO
    Mantener un test independiente para recalcular y actualizar
    `fuentes.scrape_selector` si el respaldo HTML de REDIMIN cambia.

CONTEXTO
    REDIMIN hoy opera principalmente por RSS, pero si el feed falla el
    scraper cae a HTTPX/BeautifulSoup. Este script permite recalcular un
    selector explícito para que ese fallback sea más estable.

USO
    python test_selector_redimin.py
    python test_selector_redimin.py --update
    python test_selector_redimin.py --update --force
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from boletin import db

URL = "https://www.redimin.cl"

HEADERS = {
    "Accept-Language": "es-CL,es;q=0.9",
    "User-Agent": "python-httpx/0.28 selector-diagnostic",
}

SELECTORES_CANDIDATOS = [
    "h3 a, h2 a",
    "h3 a",
    "h2 a",
    "article h3 a",
    "article h2 a",
    "main article a",
    ".entry-title a",
    ".post-title a",
    ".jeg_post_title a",
    ".td-module-title a",
    "a",
]

SELECTORES_PREFERIDOS = [
    "h3 a, h2 a",
    "h3 a",
    "h2 a",
    "article h3 a",
    "article h2 a",
]

EXCLUDE_HREF_TOKENS = (
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com", "mailto:", "javascript:", "#",
    "/tag/", "/author/", "/category/", "/feed", "/podcast", "/newsletter",
    "/contacto", "/quienes-somos", "/acerca-de", "/media-kit", "/suscrib",
    ".jpg", ".jpeg", ".png", ".webp", ".pdf", ".xml",
)

EXCLUDE_TEXT_TOKENS = (
    "leer más", "leer mas", "ver más", "ver mas", "newsletter", "podcast",
    "facebook", "twitter", "instagram", "linkedin", "suscribirme",
    "publicidad", "media kit", "contacto", "quienes somos",
)

MIN_UTILES = 5
MAX_UTILES = 80


def _normalizar_href(href: str, base_url: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"{base_url.rstrip('/')}{href}"
    return href


def _es_link_noticia(href: str, text: str, base_host: str, base_url: str) -> bool:
    href = _normalizar_href(href, base_url)
    text = " ".join((text or "").split())
    if not href or not text or len(text) < 20:
        return False

    href_lower = href.lower()
    text_lower = text.lower()

    if any(t in href_lower for t in EXCLUDE_HREF_TOKENS):
        return False
    if any(t in text_lower for t in EXCLUDE_TEXT_TOKENS):
        return False

    host = urlparse(href).netloc.replace("www.", "").lower()
    if host and host != base_host:
        return False

    path = urlparse(href).path.strip("/")
    if not path:
        return False

    partes = [p for p in path.split("/") if p]
    return len(partes) >= 1


def _descargar_html(url: str) -> str:
    print(f"\n[httpx] Descargando {url} ...")
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} para {url}")
    print(f"[httpx] HTML obtenido: {len(r.text):,} chars | Status: {r.status_code}")
    return r.text


def _probar_selectores(html: str, base_host: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    resultados = []

    for selector in SELECTORES_CANDIDATOS:
        try:
            elementos = soup.select(selector)
        except Exception:
            continue
        if not elementos:
            continue

        utiles = []
        vistos: set[str] = set()
        for el in elementos[:200]:
            href = el.get("href") or ""
            if not href:
                parent = el.find_parent("a")
                if parent:
                    href = parent.get("href") or ""
            text = " ".join(el.get_text(" ", strip=True).split())
            href = _normalizar_href(href, base_url)
            if _es_link_noticia(href, text, base_host, base_url) and href not in vistos:
                vistos.add(href)
                utiles.append({"text": text[:100], "href": href[:140]})

        if utiles:
            resultados.append({
                "selector": selector,
                "total_matches": len(elementos),
                "utiles": len(utiles),
                "ejemplos": utiles[:5],
            })

    return sorted(resultados, key=lambda r: (-r["utiles"], r["total_matches"]))


def _selector_preferencia(selector: str) -> int:
    try:
        return SELECTORES_PREFERIDOS.index(selector)
    except ValueError:
        return len(SELECTORES_PREFERIDOS) + 100


def _elegir_mejor_selector(resultados: list[dict]) -> dict:
    validos = [r for r in resultados if MIN_UTILES <= r["utiles"] <= MAX_UTILES]
    candidatos = validos or resultados
    return min(
        candidatos,
        key=lambda r: (
            0 if MIN_UTILES <= r["utiles"] <= MAX_UTILES else 1,
            _selector_preferencia(r["selector"]),
            abs(r["utiles"] - 30),
            r["total_matches"],
            -r["utiles"],
        ),
    )


def _get_fuente(url_patron: str) -> dict | None:
    for f in db.get_fuentes_activas():
        if url_patron in (f.get("url") or ""):
            return f
    return None


def main(actualizar: bool = False, force: bool = False) -> None:
    base = urlparse(URL)
    base_host = base.netloc.replace("www.", "").lower()
    base_url = f"{base.scheme}://{base.netloc}"

    if actualizar and not force:
        fuente = _get_fuente("redimin.cl")
        if fuente and (fuente.get("scrape_selector") or "").strip():
            print(f"\n[DB] Ya existe scrape_selector para redimin.cl: '{fuente['scrape_selector']}'")
            print("[DB] Para re-diagnosticar y sobreescribir, usa: --update --force")
            return

    html = _descargar_html(URL)
    resultados = _probar_selectores(html, base_host, base_url)

    if not resultados:
        print("\n[ERROR] Ningun selector encontro links utiles.")
        return

    mejor = _elegir_mejor_selector(resultados)

    print(f"\n{'=' * 60}")
    print("Selectores válidos:\n")
    for r in resultados:
        marca = ">>" if r["selector"] == mejor["selector"] else "  "
        print(f"{marca} OK '{r['selector']}' -> {r['utiles']} utiles / {r['total_matches']} totales")
        for ej in r["ejemplos"]:
            print(f"       - {ej['text']}")
            print(f"         {ej['href']}")

    print(f"\nMEJOR SELECTOR: '{mejor['selector']}'")

    if not (MIN_UTILES <= mejor["utiles"] <= MAX_UTILES):
        print("[AVISO] Resultado fuera de rango. DB no modificada.")
        return

    if not actualizar:
        print("\n(modo solo diagnostico — DB no modificada)")
        return

    fuente = _get_fuente("redimin.cl")
    if fuente is None:
        print("[ERROR] No se encontró redimin.cl en la tabla fuentes.")
        return

    nota = (
        "Selector descubierto via test_selector_redimin.py. "
        f"Matches utiles: {mejor['utiles']} / {mejor['total_matches']} totales. "
        "Respaldo HTML para fuente RSS."
    )
    db.actualizar_selector_fuente(fuente["id"], mejor["selector"], nota=nota)
    print(f"[DB] Actualizado — fuente_id={fuente['id']} | scrape_selector='{mejor['selector']}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnostico de selector CSS para redimin.cl")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.force and not args.update:
        parser.error("--force requiere --update")

    main(actualizar=args.update, force=args.force)
