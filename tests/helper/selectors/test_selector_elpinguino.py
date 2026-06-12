from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_elpinguino.py
---------------------------
Diagnóstico y configuración del selector CSS para elpinguino.com.

POR QUE EXISTE ESTE SCRIPT
    El scraper del boletín necesita un selector CSS para saber qué links
    de la portada de elpinguino.com son noticias reales. Este script lo
    descubre automáticamente renderizando la página con un navegador real
    (Playwright) y probando una lista de selectores candidatos.

    Una vez encontrado el selector correcto, puede guardarlo en la DB
    con el flag --update.

POR QUE USA PLAYWRIGHT (y no httpx directo)
    elpinguino.com es JS-heavy: carga ads, lazy-loading y contenido
    dinámico. Con httpx se obtiene HTML estático sin noticias.
    Playwright lanza un Chromium headless, espera a que la página
    termine de cargar y hace scroll progresivo para activar lazy-loading.

MODOS DE USO
    Solo diagnóstico (NO modifica la DB):
        python test_selector_elpinguino.py

    Diagnóstico + guardar el mejor selector en la DB:
        python test_selector_elpinguino.py --update

    Forzar re-diagnóstico aunque ya haya selector en la DB:
        python test_selector_elpinguino.py --update --force

    IMPORTANTE: sin --update el script nunca toca la base de datos,
    independientemente de lo que encuentre. Es seguro correrlo para
    inspeccionar sin riesgo de modificar nada.

COMPORTAMIENTO CON --update
    1. Consulta la DB para ver si ya hay un selector configurado.
    2. Si ya existe → informa y NO re-renderiza ni sobreescribe,
       a menos que se agregue --force.
    3. Si no existe (o se usa --force) → renderiza, prueba selectores
       y guarda el mejor en fuentes.scrape_selector.

CRITERIO DE VALIDEZ
    Un selector se considera válido si encuentra entre 3 y 60 links
    que apunten al mismo dominio, con texto de al menos 20 caracteres,
    sin ser links de navegación, categorías, RRSS ni publicidad.

AGREGAR SELECTORES CANDIDATOS
    Si el sitio cambia su estructura HTML, agregá el nuevo selector
    a la lista SELECTORES_CANDIDATOS al inicio del archivo y volvé
    a ejecutar el script.
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Permite importar db.py desde el mismo directorio
sys.path.insert(0, str(Path(__file__).parent))
from boletin import db

# ── Configuración ──────────────────────────────────────────────────────────────

URL = "https://elpinguino.com"

# Lista de selectores a probar, ordenados de más específico a más genérico.
# Los específicos del sitio (site3-*) van primero porque son más estables.
# Si el sitio cambia su HTML y deja de funcionar, agregá el nuevo selector
# al inicio de esta lista antes de volver a ejecutar.
SELECTORES_CANDIDATOS = [
    # --- Clases propias del CMS de elpinguino.com (site3-*) ---
    # Descubiertas inspeccionando el HTML renderizado el 2026-04-16.
    # Son las más estables porque son parte del diseño propio del sitio.
    ".site3-noticia a",             # contenedor principal de cada noticia
    ".site3-noticia-texto a",       # texto + link dentro del bloque noticia
    ".site3-noticia-texto h2 a",
    ".site3-noticia-texto h3 a",
    ".site3-noticias-wrapper .site3-noticia a",  # wrapper general > noticia
    ".site3-content .site3-noticia a",           # área de contenido > noticia
    ".site3-noticia-titulo a",
    ".site3-titulo a",
    ".site3-carousel a",            # carrusel de noticias destacadas
    ".site3-carousel-text a",

    # --- Selectores semánticos HTML5 (genéricos pero semánticos) ---
    "article a",
    "article h2 a",
    "article h3 a",
    "main h2 a",
    "main h3 a",

    # --- Selectores de título genéricos ---
    # Funcionan en muchos portales pero pueden traer ruido (menú, footer).
    # Solo se usarán si los anteriores no encuentran nada.
    "h2 a",
    "h3 a",
    "h4 a",

    # --- Otros patrones comunes en portales chilenos ---
    ".noticia a",
    ".noticias a",
    ".entry-title a",
    ".post-title a",
    ".titulo a",
]

# Tokens en la URL del link que indican que NO es una noticia.
# Sirve para filtrar links de navegación, categorías, feeds, RRSS, etc.
EXCLUDE_HREF_TOKENS = (
    "/tag/", "/author/", "/category/", "/feed", "/comments/",
    "#", "javascript:", "mailto:", "tel:", "/buscar", "/newsletter",
    "/urlrotator", "/issuu", "/papeldigital", "/digital",
    "facebook.com", "twitter.com", "instagram.com", "youtube.com",
    "googlesyndication", "doubleclick",
)

# Tokens en el texto del link que indican que NO es una noticia.
EXCLUDE_TEXT_TOKENS = (
    "leer mas", "read more", "ver mas", "home", "inicio",
    "contacto", "suscrib", "newsletter", "buscar", "compartir",
    "descargar", "denuncias",
)

# Rango válido de matches utiles para aceptar un selector.
# Menos de 3 → probablemente no encontró noticias reales.
# Más de 60  → probablemente está capturando links de menú/footer también.
MIN_UTILES = 3
MAX_UTILES = 60


# ── Funciones internas ─────────────────────────────────────────────────────────

def _es_link_noticia(href: str, text: str, base_host: str) -> bool:
    """
    Determina si un link (href + texto visible) parece ser una noticia real.
    Descarta links de navegación, RRSS, publicidad y textos muy cortos.
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
    # Solo acepta links del mismo dominio
    host = urlparse(href).netloc.replace("www.", "").lower()
    if host and host != base_host:
        return False
    return True


def _renderizar_con_playwright(url: str) -> str:
    """
    Abre el sitio con Chromium headless, espera a que termine de cargar
    el JavaScript y hace scroll progresivo para activar lazy-loading.
    Retorna el HTML completo post-renderizado.
    """
    print(f"\n[Playwright] Renderizando {url} ...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 1080},
        )
        page = ctx.new_page()
        page.goto(url, timeout=40_000)
        try:
            page.wait_for_load_state("networkidle", timeout=25_000)
        except Exception:
            pass
        # Scroll progresivo: activa lazy-loading en cada tramo de la página
        for pos in (400, 800, 1400, 2200, 3000):
            page.evaluate(f"window.scrollTo(0, {pos})")
            page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
        html = page.content()
        browser.close()
    print(f"[Playwright] HTML obtenido: {len(html):,} chars")
    return html


def _probar_selectores(html: str, base_host: str) -> list[dict]:
    """
    Prueba cada selector de SELECTORES_CANDIDATOS sobre el HTML renderizado.
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
        for el in elementos[:40]:
            href = el.get("href") or ""
            if not href:
                # El selector puede apuntar a un elemento hijo del <a>
                parent = el.find_parent("a")
                if parent:
                    href = parent.get("href") or ""
            if href.startswith("/"):
                href = f"https://{base_host}{href}"
            text = " ".join(el.get_text(" ", strip=True).split())
            if _es_link_noticia(href, text, base_host):
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
    Extrae todas las clases CSS presentes en el HTML y filtra las que
    parecen ser del CMS propio del sitio (site3-*, noticia*, portada*).
    Util para descubrir nuevos selectores candidatos cuando el sitio cambia.
    """
    soup = BeautifulSoup(html, "html.parser")
    clases: set[str] = set()
    for tag in soup.find_all(True):
        for cls in tag.get("class", []):
            clases.add(cls)
    relevantes = [
        c for c in sorted(clases)
        if any(k in c.lower() for k in ("site3", "noticia", "portada", "news", "article"))
    ]
    return relevantes


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
    base_host = urlparse(URL).netloc.replace("www.", "").lower()

    # ── Verificación previa en DB ──────────────────────────────────────────────
    # Si ya hay un selector configurado y no se pidió --force,
    # no hace falta re-renderizar ni sobreescribir nada.
    if actualizar and not force:
        fuente = _get_fuente("elpinguino.com")
        if fuente and (fuente.get("scrape_selector") or "").strip():
            selector_actual = fuente["scrape_selector"].strip()
            print(f"\n[DB] Ya existe scrape_selector para elpinguino.com: '{selector_actual}'")
            print("[DB] Para re-diagnosticar y sobreescribir, usa: --update --force")
            return

    # ── Renderizado ────────────────────────────────────────────────────────────
    html = _renderizar_con_playwright(URL)

    # Clases CSS relevantes — util si el sitio cambió y hay que agregar selectores
    clases = _clases_css_relevantes(html)
    print(f"\n{'='*60}")
    print(f"Clases CSS relevantes encontradas en el HTML ({len(clases)}):")
    for c in clases[:40]:
        print(f"  .{c}")
    if len(clases) > 40:
        print(f"  ... y {len(clases) - 40} mas")

    # ── Prueba de selectores ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Probando {len(SELECTORES_CANDIDATOS)} selectores candidatos...")
    resultados = _probar_selectores(html, base_host)

    if not resultados:
        print("\n[ERROR] Ningún selector encontro links utiles de noticias.")
        print("Revisá las clases CSS de arriba y agregá nuevos selectores en SELECTORES_CANDIDATOS.")
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

    # ── Validación del rango ───────────────────────────────────────────────────
    valido = MIN_UTILES <= mejor["utiles"] <= MAX_UTILES
    if not valido:
        print(
            f"\n[AVISO] El selector tiene {mejor['utiles']} matches utiles, "
            f"fuera del rango [{MIN_UTILES}, {MAX_UTILES}]. "
            "No se actualiza la DB."
        )
        return

    # ── Actualización en DB ────────────────────────────────────────────────────
    if not actualizar:
        print("\n(modo solo diagnostico — DB no modificada)")
        print("Para guardar este selector en la DB, ejecuta con: --update")
        return

    fuente = _get_fuente("elpinguino.com")
    if fuente is None:
        print("\n[ERROR] No se encontro elpinguino.com en la tabla fuentes. Verificá la URL en DB.")
        return

    nota = (
        f"Selector descubierto via test_selector_elpinguino.py. "
        f"Matches utiles: {mejor['utiles']} / {mejor['total_matches']} totales."
    )
    db.actualizar_selector_fuente(fuente["id"], mejor["selector"], nota=nota)
    print(f"\n[DB] Actualizado — fuente_id={fuente['id']} | scrape_selector='{mejor['selector']}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnostico y configuracion del selector CSS para elpinguino.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ejemplos:
  python test_selector_elpinguino.py                  solo diagnostico, no toca DB
  python test_selector_elpinguino.py --update         guarda el mejor selector en DB
  python test_selector_elpinguino.py --update --force re-diagnostica aunque ya haya selector
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
