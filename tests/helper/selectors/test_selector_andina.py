from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_andina.py
-----------------------
Diagnostico y configuracion del selector CSS para andina.pe.

POR QUE EXISTE ESTE SCRIPT
    El scraper del boletin necesita un selector CSS para saber que links
    de la portada de Andina son noticias reales. Este script descarga la
    home de la agencia, prueba varios selectores candidatos y permite
    guardar el mejor en fuentes.scrape_selector.

SITIO OBJETIVO
    URL usada para el diagnostico:
        https://andina.pe/agencia/

    Aunque en DB la fuente vive como https://andina.pe, la portada real
    de noticias usa el path /agencia/ y ahi se observan los bloques con
    cards de noticias, secciones y destacados.

TECNOLOGIA DEL SITIO
    Andina entrega HTML server-side completo. No hace falta Playwright
    para descubrir selectores: con httpx alcanza para obtener la portada
    y evaluar la estructura real.

ESTRUCTURA OBSERVADA
    En la home actual aparecen varios bloques repetibles:
      - cards de secciones: .card-panel.white.padding5
      - cards secundarias: .card-panel.white.no-padding
      - titulares de bloque: h2 / h3 / h4 dentro de cards
      - clases de truncado semantico: .truncateseccion, .truncate3, .truncate7

    Los enlaces de noticias reales siguen el patron:
        noticia-<slug>-<id>.aspx

    Se excluyen explicitamente:
      - videos, galerias, secciones, newsletter, podcast, especiales
      - RRSS, banners, widgets, legales y links externos

MODOS DE USO
    Solo diagnostico:
        python test_selector_andina.py

    Diagnostico + guardar el mejor selector en la DB:
        python test_selector_andina.py --update

    Forzar re-diagnostico aunque ya exista selector:
        python test_selector_andina.py --update --force
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

URL = "https://andina.pe/agencia/"

HEADERS = {
    "Accept-Language": "es-PE,es;q=0.9",
    "User-Agent": "python-httpx/0.28 selector-diagnostic",
}

# Ordenados de mas especifico/utile a mas generico.
SELECTORES_CANDIDATOS = [
    ".card-panel h2 a, .card-panel.white.padding5 h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.padding5 h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.no-padding h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.padding5 h4 a",
    ".truncateseccion a",
    ".card-panel h3 a",
    ".card-panel.white.padding5 h3 a",
    ".card-panel.white.no-padding h3 a",
    ".underline.truncate3 a",
    ".underline.truncate7 a",
    "h2 a",
    "h3 a",
    "h4 a",
    "a",
]

EXCLUDE_HREF_TOKENS = (
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "instagram.com", "soundcloud.com", "spotify.com", "tiktok.com",
    "apps.apple.com", "play.google.com",
    "podcast.andina.pe", "newsletter.andina.pe",
    "portal.andina.pe/edpespeciales", "coberturaespecial-",
    "seccion-", "video-", "galeria-", "interactivo",
    "canalonline", "resultadoselecciones2026", "api/ResultadosElecciones.ashx",
    "legal/", "nosotros", "english", "ingles",
    "#", "javascript:", "mailto:", "tel:",
)

EXCLUDE_TEXT_TOKENS = (
    "facebook", "twitter", "linkedin", "podcast", "newsletter",
    "english", "inicio", "lo último", "lo ultimo", "app store",
    "google play", "salir", "especiales", "interactivos",
    "videos andina", "canal online",
)

MIN_UTILES = 5
MAX_UTILES = 60
RE_URL_NOTICIA = re.compile(r"noticia-[\w\-áéíóúñ]+-\d+\.aspx$", re.IGNORECASE)

SELECTORES_PREFERIDOS = [
    ".card-panel h2 a, .card-panel.white.padding5 h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.padding5 h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.no-padding h3 a, .card-panel.white.padding5 h4 a",
    ".card-panel.white.padding5 h4 a",
    ".truncateseccion a",
    ".card-panel h3 a",
]


def _normalizar_href(href: str, base_url: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"{base_url.rstrip('/')}{href}"
    if href.startswith(("noticia-", "video-", "galeria-", "seccion-")):
        return f"{base_url.rstrip('/')}/agencia/{href}"
    return href


def _es_link_noticia(href: str, text: str, base_host: str, base_url: str) -> bool:
    href = _normalizar_href(href, base_url)
    text = " ".join((text or "").split())
    if not href or not text:
        return False
    if len(text) < 20:
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

    return bool(RE_URL_NOTICIA.search(urlparse(href).path))


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
        for el in elementos[:300]:
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
            abs(r["utiles"] - 45),
            r["total_matches"],
            -r["utiles"],
        ),
    )


def _clases_css_relevantes(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    clases: set[str] = set()
    for tag in soup.find_all(True):
        for cls in tag.get("class", []):
            clases.add(cls)
    keywords = (
        "card", "noticia", "titular", "seccion", "truncate",
        "panel", "carousel", "foto",
    )
    return [c for c in sorted(clases) if any(k in c.lower() for k in keywords)]


def _get_fuente(url_patron: str) -> dict | None:
    for f in db.get_fuentes_activas():
        if url_patron in (f.get("url") or ""):
            return f
    return None


def main(actualizar: bool = False, force: bool = False) -> None:
    base = urlparse(URL)
    base_host = base.netloc.replace("www.", "").lower()
    base_url = f"{base.scheme}://{base_host}"

    if actualizar and not force:
        fuente = _get_fuente("andina.pe")
        if fuente and (fuente.get("scrape_selector") or "").strip():
            selector_actual = fuente["scrape_selector"].strip()
            print(f"\n[DB] Ya existe scrape_selector para andina.pe: '{selector_actual}'")
            print("[DB] Para re-diagnosticar y sobreescribir, usa: --update --force")
            return

    html = _descargar_html(URL)

    clases = _clases_css_relevantes(html)
    print(f"\n{'=' * 60}")
    print(f"Clases CSS relevantes encontradas en el HTML ({len(clases)}):")
    for c in clases[:80]:
        print(f"  .{c}")
    if len(clases) > 80:
        print(f"  ... y {len(clases) - 80} mas")

    print(f"\n{'=' * 60}")
    print(f"Probando {len(SELECTORES_CANDIDATOS)} selectores candidatos...")
    resultados = _probar_selectores(html, base_host, base_url)

    if not resultados:
        print("\n[ERROR] Ningun selector encontro links utiles de noticias.")
        print("Revisa las clases CSS de arriba y agrega nuevos selectores en SELECTORES_CANDIDATOS.")
        return

    mejor = _elegir_mejor_selector(resultados)

    print(f"\n{'=' * 60}")
    print(f"Selectores que encontraron noticias ({len(resultados)}):\n")
    for r in resultados:
        marca = ">>" if r["selector"] == mejor["selector"] else "  "
        print(f"{marca} OK  '{r['selector']}'  ->  {r['utiles']} utiles / {r['total_matches']} totales")
        for ej in r["ejemplos"]:
            print(f"        - {ej['text']}")
            print(f"          {ej['href']}")

    print(f"\n{'=' * 60}")
    print(f"MEJOR SELECTOR : '{mejor['selector']}'")
    print(f"Utiles         : {mejor['utiles']}  (rango valido: {MIN_UTILES}-{MAX_UTILES})")
    print(f"Total matches  : {mejor['total_matches']}")

    valido = MIN_UTILES <= mejor["utiles"] <= MAX_UTILES
    if not valido:
        print(
            f"\n[AVISO] El selector tiene {mejor['utiles']} matches utiles, "
            f"fuera del rango [{MIN_UTILES}, {MAX_UTILES}]. No se actualiza la DB."
        )
        return

    if not actualizar:
        print("\n(modo solo diagnostico — DB no modificada)")
        print("Para guardar este selector en la DB, ejecuta con: --update")
        return

    fuente = _get_fuente("andina.pe")
    if fuente is None:
        print("\n[ERROR] No se encontro andina.pe en la tabla fuentes. Verifica la URL en DB.")
        return

    nota = (
        "Selector descubierto via test_selector_andina.py. "
        f"Matches utiles: {mejor['utiles']} / {mejor['total_matches']} totales. "
        "Sitio: portada server-side en /agencia/. Descarga: httpx."
    )
    db.actualizar_selector_fuente(fuente["id"], mejor["selector"], nota=nota)
    print(f"\n[DB] Actualizado — fuente_id={fuente['id']} | scrape_selector='{mejor['selector']}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnostico y configuracion del selector CSS para andina.pe",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Guarda el mejor selector encontrado en fuentes.scrape_selector",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza re-diagnostico aunque ya exista un selector en la DB (requiere --update)",
    )
    args = parser.parse_args()

    if args.force and not args.update:
        parser.error("--force requiere --update")

    main(actualizar=args.update, force=args.force)
