from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_h2news.py
-----------------------
Diagnostico y configuracion del selector CSS para h2news.cl.

POR QUE EXISTE ESTE SCRIPT
    El scraper del boletin necesita un selector CSS para saber que links
    de la portada de h2news.cl son noticias reales. Este script lo
    descubre automaticamente descargando la pagina con httpx y probando
    una lista de selectores candidatos.

    Una vez encontrado el selector correcto, puede guardarlo en la DB
    con el flag --update.

TECNOLOGIA DEL SITIO
    h2news.cl corre WordPress con el tema Avada (Builder).
    Las clases propias del tema empiezan con "fusion-" o "awb-".
    Los posts usan estructura estandar de WordPress:
        <article> con clase "entry-title" para el titulo.

POR QUE USA HTTPX (y NO Playwright)
    IMPORTANTE: el servidor de h2news.cl bloquea requests con User-Agent
    de browsers reales (Chrome, Firefox) y devuelve 403. En cambio,
    permite el User-Agent de httpx (python-httpx/x.x.x) y devuelve el
    HTML completo con 420K chars de contenido.
    NO uses headers de browser con este sitio — va a fallar.

MODOS DE USO
    Solo diagnostico (NO modifica la DB):
        python test_selector_h2news.py

    Diagnostico + guardar el mejor selector en la DB:
        python test_selector_h2news.py --update

    Forzar re-diagnostico aunque ya haya selector en la DB:
        python test_selector_h2news.py --update --force

    IMPORTANTE: sin --update el script nunca toca la base de datos,
    independientemente de lo que encuentre.

COMPORTAMIENTO CON --update
    1. Consulta la DB para ver si ya hay un selector configurado.
    2. Si ya existe → informa y NO re-descarga ni sobreescribe,
       a menos que se agregue --force.
    3. Si no existe (o se usa --force) → descarga, prueba selectores
       y guarda el mejor en fuentes.scrape_selector.

CRITERIO DE VALIDEZ
    Un selector se considera valido si encuentra entre 3 y 60 links
    que apunten al mismo dominio, con texto de al menos 20 caracteres,
    sin ser links de navegacion, categorias, RRSS ni publicidad.

AGREGAR SELECTORES CANDIDATOS
    Si el sitio cambia su estructura HTML, agregá el nuevo selector
    a la lista SELECTORES_CANDIDATOS y volvé a ejecutar el script.
"""

import sys
import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# Permite importar db.py desde el mismo directorio
sys.path.insert(0, str(Path(__file__).parent))
from boletin import db

# ── Configuracion ──────────────────────────────────────────────────────────────

URL = "https://www.h2news.cl"

# CRITICO: NO usar User-Agent de browser. El sitio bloquea Chrome/Firefox con 403.
# httpx usa "python-httpx/x.x.x" por defecto y el sitio lo permite.
# Solo se agrega Accept-Language para evitar respuestas en ingles.
HEADERS = {
    "Accept-Language": "es-CL,es;q=0.9",
}

# Lista de selectores a probar, de mas especifico a mas generico.
# Los de Avada/WordPress van primero por ser mas estables ante cambios de layout.
# IMPORTANTE: en H2News muchos titulos visibles NO son <a>, sino headings
# dentro de cards de Avada. Por eso tambien probamos nodos de titulo y luego
# resolvemos el href buscando el link real dentro del contenedor.
# Si el sitio cambia y deja de funcionar, agreg? el nuevo selector al inicio.
SELECTORES_CANDIDATOS = [
    # --- Avada grid/cards: estructura real observada en H2News ---
    ".fusion-grid-posts-cards .fusion-title-heading a",
    ".fusion-grid-posts-cards .fusion-title-heading",
    ".fusion-grid-posts-cards h5 a",
    ".fusion-grid-posts-cards h5",
    ".fusion-grid-posts-cards h4 a",
    ".fusion-grid-posts-cards h4",
    ".fusion-post-cards-grid-column .fusion-title-heading a",
    ".fusion-post-cards-grid-column .fusion-title-heading",
    "ul.fusion-grid-posts-cards .fusion-title-heading a",
    "ul.fusion-grid-posts-cards .fusion-title-heading",
    ".fusion-post-cards .fusion-title-heading a",
    ".fusion-post-cards .fusion-title-heading",
    ".post-card .fusion-title-heading a",
    ".post-card .fusion-title-heading",
    ".swiper-slide .fusion-title-heading a",
    ".swiper-slide .fusion-title-heading",
    ".fusion-title-heading a",
    ".fusion-title-heading",
    ".awb-news-ticker-link",
    # --- WordPress estandar: funciona en casi todos los temas WP ---
    # "entry-title" es la clase estandar de WordPress para titulos de posts.
    ".entry-title a",
    "h2.entry-title a",
    "h3.entry-title a",
    "article .entry-title a",
    # --- Avada theme: clases propias de posts/titulos ---
    ".fusion-post-title a",
    "h2.fusion-post-title a",
    ".fusion-blog-layout-large .entry-title a",
    ".fusion-blog-layout-grid .entry-title a",
    ".fusion-post-content .entry-title a",
    # Avada: rollover sobre imagen del post
    ".fusion-rollover-title a",
    # --- Combinaciones semanticas HTML5 ---
    "article h2 a",
    "article h3 a",
    "main .entry-title a",
    # --- Selectores de titulo genericos (mayor cobertura, mas ruidosos) ---
    "h2 a",
    "h3 a",
    # --- Otros patrones comunes en portales de noticias ---
    ".post-title a",
    ".article-title a",
    ".news-title a",
]

# Tokens en la URL que indican que NO es una noticia.
EXCLUDE_HREF_TOKENS = (
    "/tag/", "/author/", "/category/", "/feed", "/comments/",
    "/eventos/", "/estudios/", "/subscribete", "/newsletter",
    "/h2-news-next/", "/sobre-nosotros/", "/contacto/",
    "/wp-json/", "/wp-admin/", "/wp-login",
    "#", "javascript:", "mailto:", "tel:",
    "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "linkedin.com", "mobile.twitter.com",
    "googlesyndication", "doubleclick", "marcachile.cl",
)

# Tokens en el texto del link que indican que NO es una noticia.
EXCLUDE_TEXT_TOKENS = (
    "leer mas", "read more", "ver mas", "home", "inicio",
    "contacto", "suscrib", "newsletter", "buscar", "eventos",
    "estudios", "siguiente", "anterior", "ver todas",
    "todas las categorias", "skip to content",
)

# Rango valido de matches utiles para aceptar un selector.
MIN_UTILES = 3
MAX_UTILES = 60

# Detecta permalinks tipicos de WordPress, por ejemplo /2026/04/16/slug/
RE_URL_NOTICIA = re.compile(r"/20\d{2}/\d{2}/\d{2}/")


# ── Funciones internas ─────────────────────────────────────────────────────────

def _es_link_noticia(href: str, text: str, base_host: str) -> bool:
    """
    Determina si un link (href + texto visible) parece ser una noticia real.
    Descarta links de navegacion, RRSS, publicidad y textos muy cortos.
    """
    href = (href or "").strip()
    text = (text or "").strip()
    if not href or not text:
        return False
    if len(text) < 20:
        return False
    if any(t in href.lower() for t in EXCLUDE_HREF_TOKENS):
        return False
    if any(t in text.lower() for t in EXCLUDE_TEXT_TOKENS):
        return False
    # Solo acepta links del mismo dominio (h2news.cl)
    host = urlparse(href).netloc.replace("www.", "").lower()
    if host and host != base_host:
        return False
    return True


def _normalizar_href(href: str, base_host: str) -> str:
    """
    Normaliza hrefs relativos/absolutos a una URL absoluta del dominio base.
    """
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://{base_host}{href}"
    return href


def _extraer_href_directo_o_padre(el, base_host: str) -> str:
    """
    Intenta obtener href del elemento actual o de un <a> ancestro.
    """
    href = el.get("href") or ""
    if href:
        return _normalizar_href(href, base_host)
    parent = el.find_parent("a")
    if parent:
        return _normalizar_href(parent.get("href") or "", base_host)
    return ""


def _parece_permalink_noticia(href: str, base_host: str) -> bool:
    """
    Heuristica de permalink de noticia.

    En H2News las noticias reales suelen tener URL con fecha:
      /2026/04/16/slug/
    Como fallback, aceptamos paths con varias secciones siempre que no
    caigan en exclusiones.
    """
    href = _normalizar_href(href, base_host)
    if not href:
        return False

    host = urlparse(href).netloc.replace("www.", "").lower()
    if host and host != base_host:
        return False

    href_lower = href.lower()
    if any(t in href_lower for t in EXCLUDE_HREF_TOKENS):
        return False

    if RE_URL_NOTICIA.search(href):
        return True

    path = urlparse(href).path.strip("/")
    partes = [p for p in path.split("/") if p]
    return len(partes) >= 2


def _buscar_href_en_contenedor(el, base_host: str) -> str:
    """
    Resuelve el href real de una noticia cuando el selector apunta a un titulo
    que NO es link.

    Estrategia:
      1. buscar href directo o en <a> padre
      2. subir al contenedor/card mas cercano
      3. dentro del contenedor, priorizar anchors que parezcan permalinks
         de noticia (idealmente con /YYYY/MM/DD/)
    """
    href_directo = _extraer_href_directo_o_padre(el, base_host)
    if _parece_permalink_noticia(href_directo, base_host):
        return href_directo

    contenedor = None
    for ancestro in [el, *el.parents]:
        clases = " ".join(ancestro.get("class", []))
        if any(token in clases for token in (
            "post-card",
            "fusion-post-cards-grid-column",
            "fusion-grid-post",
            "swiper-slide",
            "fusion-column-wrapper",
            "hentry",
        )) or ancestro.name == "article":
            contenedor = ancestro
            break

    if contenedor is None:
        contenedor = el.parent

    anchors = contenedor.select("a[href]") if contenedor else []

    for anchor in anchors:
        href = _normalizar_href(anchor.get("href") or "", base_host)
        if _parece_permalink_noticia(href, base_host):
            return href

    for anchor in anchors:
        href = _normalizar_href(anchor.get("href") or "", base_host)
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if _es_link_noticia(href, text or "x" * 25, base_host):
            return href

    return href_directo


def _descargar_html(url: str) -> str:
    """
    Descarga el HTML de la pagina usando httpx SIN headers de browser.

    ATENCION: h2news.cl bloquea User-Agent de Chrome/Firefox con 403.
    httpx usa "python-httpx/x.x.x" por defecto y el sitio lo permite.
    """
    print(f"\n[httpx] Descargando {url} ...")
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(
            f"HTTP {r.status_code} para {url}. "
            "Si ves 403: no agregues User-Agent de browser, eso activa el bloqueo."
        )
    print(f"[httpx] HTML obtenido: {len(r.text):,} chars | Status: {r.status_code}")
    return r.text


def _probar_selectores(html: str, base_host: str) -> list[dict]:
    """
    Prueba cada selector de SELECTORES_CANDIDATOS sobre el HTML descargado.
    Retorna la lista de selectores que encontraron links utiles, ordenada
    de mayor a menor cantidad de matches utiles.
    """
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
        for el in elementos[:40]:
            href = _buscar_href_en_contenedor(el, base_host)
            text = " ".join(el.get_text(" ", strip=True).split())
            if _es_link_noticia(href, text, base_host) and href not in vistos:
                vistos.add(href)
                utiles.append({"text": text[:100], "href": href[:120]})

        if utiles:
            resultados.append({
                "selector": selector,
                "total_matches": len(elementos),
                "utiles": len(utiles),
                "ejemplos": utiles[:3],
            })

    return sorted(resultados, key=lambda r: r["utiles"], reverse=True)


def _clases_css_relevantes(html: str) -> list[str]:
    """
    Extrae clases CSS del HTML filtradas por palabras clave relevantes.
    Util para descubrir nuevos selectores si el sitio cambia de estructura.
    """
    soup = BeautifulSoup(html, "html.parser")
    clases: set[str] = set()
    for tag in soup.find_all(True):
        for cls in tag.get("class", []):
            clases.add(cls)
    keywords = ("fusion", "awb", "post", "entry", "article", "noticia", "news", "title", "blog")
    return [c for c in sorted(clases) if any(k in c.lower() for k in keywords)]


def _get_fuente(url_patron: str) -> dict | None:
    """
    Busca en la DB la fuente cuya URL contiene url_patron.
    Retorna el dict completo de la fuente, o None si no existe.
    """
    for f in db.get_fuentes_activas():
        if url_patron in (f.get("url") or ""):
            return f
    return None


# ── Punto de entrada ───────────────────────────────────────────────────────────

def main(actualizar: bool = False, force: bool = False) -> None:
    base_host = urlparse(URL).netloc.replace("www.", "").lower()  # h2news.cl

    # ── Verificacion previa en DB ──────────────────────────────────────────────
    # Si ya hay un selector configurado y no se pidio --force,
    # no hace falta re-descargar ni sobreescribir nada.
    if actualizar and not force:
        fuente = _get_fuente("h2news.cl")
        if fuente and (fuente.get("scrape_selector") or "").strip():
            selector_actual = fuente["scrape_selector"].strip()
            print(f"\n[DB] Ya existe scrape_selector para h2news.cl: '{selector_actual}'")
            print("[DB] Para re-diagnosticar y sobreescribir, usa: --update --force")
            return

    # ── Descarga ───────────────────────────────────────────────────────────────
    html = _descargar_html(URL)

    # Clases CSS relevantes — util si el sitio cambia y hay que agregar selectores
    clases = _clases_css_relevantes(html)
    print(f"\n{'='*60}")
    print(f"Clases CSS relevantes encontradas en el HTML ({len(clases)}):")
    for c in clases[:50]:
        print(f"  .{c}")
    if len(clases) > 50:
        print(f"  ... y {len(clases) - 50} mas")

    # ── Prueba de selectores ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Probando {len(SELECTORES_CANDIDATOS)} selectores candidatos...")
    resultados = _probar_selectores(html, base_host)

    if not resultados:
        print("\n[ERROR] Ningun selector encontro links utiles de noticias.")
        print("Revisa las clases CSS de arriba y agrega nuevos selectores en SELECTORES_CANDIDATOS.")
        return

    print(f"\n{'='*60}")
    print(f"Selectores que encontraron noticias ({len(resultados)}):\n")
    for r in resultados:
        print(f"  OK  '{r['selector']}'  ->  {r['utiles']} utiles / {r['total_matches']} totales")
        for ej in r["ejemplos"]:
            print(f"        - {ej['text']}")
            print(f"          {ej['href']}")

    mejor = resultados[0]
    print(f"\n{'='*60}")
    print(f"MEJOR SELECTOR : '{mejor['selector']}'")
    print(f"Utiles         : {mejor['utiles']}  (rango valido: {MIN_UTILES}-{MAX_UTILES})")
    print(f"Total matches  : {mejor['total_matches']}")

    # ── Validacion del rango ───────────────────────────────────────────────────
    valido = MIN_UTILES <= mejor["utiles"] <= MAX_UTILES
    if not valido:
        print(
            f"\n[AVISO] El selector tiene {mejor['utiles']} matches utiles, "
            f"fuera del rango [{MIN_UTILES}, {MAX_UTILES}]. "
            "No se actualiza la DB."
        )
        return

    # ── Actualizacion en DB ────────────────────────────────────────────────────
    if not actualizar:
        print("\n(modo solo diagnostico — DB no modificada)")
        print("Para guardar este selector en la DB, ejecuta con: --update")
        return

    fuente = _get_fuente("h2news.cl")
    if fuente is None:
        print("\n[ERROR] No se encontro h2news.cl en la tabla fuentes. Verifica la URL en DB.")
        return

    nota = (
        f"Selector descubierto via test_selector_h2news.py. "
        f"Matches utiles: {mejor['utiles']} / {mejor['total_matches']} totales. "
        f"Sitio: WordPress + tema Avada. Descarga: httpx sin UA de browser."
    )
    db.actualizar_selector_fuente(fuente["id"], mejor["selector"], nota=nota)
    print(f"\n[DB] Actualizado — fuente_id={fuente['id']} | scrape_selector='{mejor['selector']}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnostico y configuracion del selector CSS para h2news.cl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ejemplos:
  python test_selector_h2news.py                  solo diagnostico, no toca DB
  python test_selector_h2news.py --update         guarda el mejor selector en DB
  python test_selector_h2news.py --update --force re-diagnostica aunque ya haya selector

NOTA: este sitio bloquea User-Agent de browsers. El script usa httpx
por defecto (sin simular browser) — eso es lo correcto para este sitio.
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
