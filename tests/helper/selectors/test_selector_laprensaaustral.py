from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_laprensaaustral.py
--------------------------------
Diagnostico y configuracion del selector CSS para laprensaaustral.cl.

POR QUE EXISTE ESTE SCRIPT
    El scraper del boletin necesita un selector CSS para saber que links
    de la portada de La Prensa Austral son noticias reales. Este script lo
    descubre automaticamente descargando la pagina con httpx y probando
    una lista de selectores candidatos.

    Una vez encontrado el selector correcto, puede guardarlo en la DB
    con el flag --update.

TECNOLOGIA DEL SITIO
    laprensaaustral.cl corre WordPress con un theme legacy propio.
    En la portada se observan bloques bien definidos:
      - destacado principal: .titular a
      - destacados secundarios: .titular-side a
      - listados de noticias: .noticia-titulo a
      - cajas de categorias: .titulo-destacado-categoria a, .links-categoria a

POR QUE USA HTTPX
    A diferencia de otros portales JS-heavy, La Prensa Austral devuelve
    HTML server-side completo. No hace falta Playwright para diagnosticar
    selectores de portada.

MODOS DE USO
    Solo diagnostico (NO modifica la DB):
        python test_selector_laprensaaustral.py

    Diagnostico + guardar el mejor selector en la DB:
        python test_selector_laprensaaustral.py --update

    Forzar re-diagnostico aunque ya haya selector en la DB:
        python test_selector_laprensaaustral.py --update --force

COMPORTAMIENTO CON --update
    1. Consulta la DB para ver si ya hay un selector configurado.
    2. Si ya existe → informa y NO re-descarga ni sobreescribe,
       a menos que se agregue --force.
    3. Si no existe (o se usa --force) → descarga, prueba selectores
       y guarda el mejor en fuentes.scrape_selector.

CRITERIO DE VALIDEZ
    Un selector se considera valido si encuentra entre 3 y 60 links
    del mismo dominio, con texto de al menos 20 caracteres, sin incluir
    navegacion, categorias, RRSS ni publicidad.
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

URL = "https://laprensaaustral.cl"

HEADERS = {
    "Accept-Language": "es-CL,es;q=0.9",
    "User-Agent": "python-httpx/0.28 selector-diagnostic",
}

# La portada tiene dos zonas muy utiles para scraping diario:
#   1) destacados (.titular / .titular-side)
#   2) listados principales (.articulos .noticia-titulo a)
# Ese ultimo suele ser el mejor candidato para scrape_selector porque
# captura noticias reales y evita mucho ruido de cajas historicas.
SELECTORES_CANDIDATOS = [
    ".articulos .noticia-titulo a",
    ".main-col .articulos .noticia-titulo a",
    ".noticia .noticia-titulo a",
    ".titular a, .titular-side a, .articulos .noticia-titulo a",
    ".titular a, .titular-side a",
    ".titular a",
    ".titular-side a",
    ".titulo-destacado-categoria a",
    ".links-categoria a",
    ".sidebar-box-content .texto-columna a",
    ".articulosCategoria .titulo-destacado-categoria a",
    ".articulosCategoria .links-categoria a",
    ".noticiaCategoria a",
    "article h2 a",
    "article h3 a",
    "h2 a",
    "h3 a",
    "a",
]

EXCLUDE_HREF_TOKENS = (
    "/tag/", "/author/", "/category/", "/feed", "/comments/",
    "/nuestra-empresa", "/impresos-la-prensa-austral", "/wp-json/",
    "/wp-admin/", "/xmlrpc.php", "/assets/", "/legales/",
    "digital.laprensaaustral.cl", "facebook.com", "twitter.com",
    "google.com", "googlesyndication", "doubleclick", "ads.",
    "javascript:", "mailto:", "tel:", "#",
)

EXCLUDE_TEXT_TOKENS = (
    "leer más", "leer mas", "ver más", "ver mas", "portada", "inicio",
    "facebook", "twitter", "google+", "rss", "ir arriba", "ver edición completa",
    "ver edicion completa", "necrológicas", "necrologicas", "categorías", "categorias",
    "corporativo", "social", "buscar", "editorial de hoy",
)

MIN_UTILES = 3
MAX_UTILES = 60
RE_URL_NOTICIA = re.compile(r"/20\d{2}/\d{2}/\d{2}/")

# Preferimos selectores del listado principal del home antes que cajas historicas
# de categorias. Si varios caen dentro del rango valido, este orden desempata.
SELECTORES_PREFERIDOS = [
    ".articulos .noticia-titulo a",
    ".main-col .articulos .noticia-titulo a",
    ".noticia .noticia-titulo a",
    ".titular a, .titular-side a, .articulos .noticia-titulo a",
    ".titular a, .titular-side a",
    ".titular-side a",
    ".titular a",
]


def _normalizar_href(href: str, base_host: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://{base_host}{href}"
    return href


def _es_link_noticia(href: str, text: str, base_host: str) -> bool:
    href = _normalizar_href(href, base_host)
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

    # Para este sitio exigimos permalink de noticia con fecha.
    if not RE_URL_NOTICIA.search(href):
        return False

    return True


def _descargar_html(url: str) -> str:
    print(f"\n[httpx] Descargando {url} ...")
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} para {url}")
    print(f"[httpx] HTML obtenido: {len(r.text):,} chars | Status: {r.status_code}")
    return r.text


def _probar_selectores(html: str, base_host: str) -> list[dict]:
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
        for el in elementos[:120]:
            href = el.get("href") or ""
            if not href:
                parent = el.find_parent("a")
                if parent:
                    href = parent.get("href") or ""
            text = " ".join(el.get_text(" ", strip=True).split())
            href = _normalizar_href(href, base_host)
            if _es_link_noticia(href, text, base_host) and href not in vistos:
                vistos.add(href)
                utiles.append({"text": text[:100], "href": href[:120]})

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
    """
    Elige el mejor selector priorizando primero los que caen dentro del rango
    valido y, dentro de ellos, los selectores mas especificos/esperables para
    el home diario.

    Esto evita que una caja historica con cientos de links le gane al listado
    principal solo por volumen.
    """
    validos = [r for r in resultados if MIN_UTILES <= r["utiles"] <= MAX_UTILES]
    candidatos = validos or resultados

    return min(
        candidatos,
        key=lambda r: (
            0 if MIN_UTILES <= r["utiles"] <= MAX_UTILES else 1,
            _selector_preferencia(r["selector"]),
            abs(r["utiles"] - 12),
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
        "titular", "noticia", "articulos", "categoria", "sidebar",
        "editorial", "columna", "feat", "resumen",
    )
    return [c for c in sorted(clases) if any(k in c.lower() for k in keywords)]


def _get_fuente(url_patron: str) -> dict | None:
    for f in db.get_fuentes_activas():
        if url_patron in (f.get("url") or ""):
            return f
    return None


def main(actualizar: bool = False, force: bool = False) -> None:
    base_host = urlparse(URL).netloc.replace("www.", "").lower()

    if actualizar and not force:
        fuente = _get_fuente("laprensaaustral.cl")
        if fuente and (fuente.get("scrape_selector") or "").strip():
            selector_actual = fuente["scrape_selector"].strip()
            print(f"\n[DB] Ya existe scrape_selector para laprensaaustral.cl: '{selector_actual}'")
            print("[DB] Para re-diagnosticar y sobreescribir, usa: --update --force")
            return

    html = _descargar_html(URL)

    clases = _clases_css_relevantes(html)
    print(f"\n{'=' * 60}")
    print(f"Clases CSS relevantes encontradas en el HTML ({len(clases)}):")
    for c in clases[:60]:
        print(f"  .{c}")
    if len(clases) > 60:
        print(f"  ... y {len(clases) - 60} mas")

    print(f"\n{'=' * 60}")
    print(f"Probando {len(SELECTORES_CANDIDATOS)} selectores candidatos...")
    resultados = _probar_selectores(html, base_host)

    if not resultados:
        print("\n[ERROR] Ningun selector encontro links utiles de noticias.")
        print("Revisa las clases CSS de arriba y agrega nuevos selectores en SELECTORES_CANDIDATOS.")
        return

    print(f"\n{'=' * 60}")
    print(f"Selectores que encontraron noticias ({len(resultados)}):\n")
    for r in resultados:
        print(f"  OK  '{r['selector']}'  ->  {r['utiles']} utiles / {r['total_matches']} totales")
        for ej in r["ejemplos"]:
            print(f"        - {ej['text']}")
            print(f"          {ej['href']}")

    mejor = _elegir_mejor_selector(resultados)
    print(f"\n{'=' * 60}")
    print(f"MEJOR SELECTOR : '{mejor['selector']}'")
    print(f"Utiles         : {mejor['utiles']}  (rango valido: {MIN_UTILES}-{MAX_UTILES})")
    print(f"Total matches  : {mejor['total_matches']}")

    valido = MIN_UTILES <= mejor["utiles"] <= MAX_UTILES
    if not valido:
        print(
            f"\n[AVISO] El selector tiene {mejor['utiles']} matches utiles, "
            f"fuera del rango [{MIN_UTILES}, {MAX_UTILES}]. "
            "No se actualiza la DB."
        )
        return

    if not actualizar:
        print("\n(modo solo diagnostico — DB no modificada)")
        print("Para guardar este selector en la DB, ejecuta con: --update")
        return

    fuente = _get_fuente("laprensaaustral.cl")
    if fuente is None:
        print("\n[ERROR] No se encontro laprensaaustral.cl en la tabla fuentes. Verifica la URL en DB.")
        return

    nota = (
        f"Selector descubierto via test_selector_laprensaaustral.py. "
        f"Matches utiles: {mejor['utiles']} / {mejor['total_matches']} totales. "
        f"Sitio: WordPress legacy server-side. Descarga: httpx."
    )
    db.actualizar_selector_fuente(fuente["id"], mejor["selector"], nota=nota)
    print(f"\n[DB] Actualizado — fuente_id={fuente['id']} | scrape_selector='{mejor['selector']}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnostico y configuracion del selector CSS para laprensaaustral.cl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ejemplos:
  python test_selector_laprensaaustral.py                  solo diagnostico, no toca DB
  python test_selector_laprensaaustral.py --update         guarda el mejor selector en DB
  python test_selector_laprensaaustral.py --update --force re-diagnostica aunque ya haya selector
        """,
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
