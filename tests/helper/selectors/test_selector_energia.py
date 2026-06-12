from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
test_selector_energia.py
------------------------
Script de diagnóstico para encontrar el selector CSS correcto de argentina.gob.ar/energia.
Corre este script desde tu máquina para inspeccionar la estructura HTML actual del sitio.

Uso:
    python test_selector_energia.py
"""

import httpx
from bs4 import BeautifulSoup

# URL de noticias de energía (probamos ambas variantes)
URLS_A_PROBAR = [
    "https://www.argentina.gob.ar/economia/energia",
    "https://www.argentina.gob.ar/node/11603/noticias",
    "https://www.argentina.gob.ar/economia/energia/noticias",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-AR,es;q=0.9",
}

# Selectores candidatos para Drupal + Poncho (Bootstrap 3)
SELECTORES_CANDIDATOS = [
    # Drupal Views estándar
    ".views-field-title a",
    ".view-content .views-row a",
    ".views-row h2 a",
    ".views-row h3 a",
    # Poncho / Bootstrap
    ".card-title a",
    ".panel-title a",
    ".media-heading a",
    # Genéricos Drupal
    "h2.node-title a",
    "h3.node-title a",
    ".node-title a",
    # Genéricos HTML
    "article h2 a",
    "article h3 a",
    "h2 a",
    "h3 a",
    # Por clase poncho
    ".col-md-12 h3 a",
    ".col-sm-12 h3 a",
    # Poncho "noticias" lista
    ".poncho-noticias a",
    ".noticias-listing a",
]


def probar_url(url: str):
    print(f"\n{'='*60}")
    print(f"Probando URL: {url}")
    print(f"{'='*60}")
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        print(f"Status: {r.status_code} | URL final: {r.url}")
        if r.status_code != 200:
            print("⚠️  No se pudo obtener la página")
            return

        soup = BeautifulSoup(r.text, "html.parser")

        print(f"\n📋 Clases CSS únicas encontradas (primeras 50):")
        clases = set()
        for tag in soup.find_all(True):
            for cls in tag.get("class", []):
                clases.add(cls)
        for cls in sorted(clases)[:50]:
            print(f"  .{cls}")

        print(f"\n🔍 Probando {len(SELECTORES_CANDIDATOS)} selectores candidatos:")
        exitosos = []
        for selector in SELECTORES_CANDIDATOS:
            elementos = soup.select(selector)
            if elementos:
                titulos = [el.get_text(strip=True)[:80] for el in elementos[:3] if el.get_text(strip=True)]
                if titulos:
                    print(f"\n  ✅ '{selector}' → {len(elementos)} elementos")
                    for t in titulos:
                        print(f"     - {t}")
                    exitosos.append((selector, len(elementos)))

        if exitosos:
            mejor = max(exitosos, key=lambda x: x[1])
            print(f"\n🏆 MEJOR SELECTOR: '{mejor[0]}' ({mejor[1]} artículos)")
        else:
            print("\n❌ Ningún selector encontró artículos")
            print("\n📄 Primeros 2000 chars del HTML para diagnóstico manual:")
            print(r.text[:2000])

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    print("🔎 Diagnóstico de selector CSS para argentina.gob.ar/energia")
    for url in URLS_A_PROBAR:
        probar_url(url)
    print("\n✅ Diagnóstico completo.")