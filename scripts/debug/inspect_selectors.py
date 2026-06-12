from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
inspect_selectors.py
Inspecciona la estructura HTML de ACERA y Mining Weekly para identificar selectores CSS correctos.
Actualiza los selectores en PostgreSQL automáticamente.
"""

import time
import psycopg2
from pathlib import Path
from dotenv import load_dotenv
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

load_dotenv(dotenv_path=ROOT_DIR / '.env')

SITES = [
    ("ACERA", "https://www.acera.cl"),
    ("Mining Weekly", "https://www.miningweekly.com"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

def get_db_connection():
    """Conecta a la base de datos PostgreSQL."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def update_selector_in_db(name: str, selector: str):
    """Actualiza el selector en la base de datos."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE fuentes SET scrape_selector = %s WHERE nombre = %s",
            (selector, name)
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"✓ Base de datos actualizada: {name} → '{selector}'")
        return True
    except Exception as e:
        print(f"✗ Error actualizando DB para {name}: {e}")
        return False

def inspect_site(name: str, url: str) -> str | None:
    print(f"\n{'='*80}")
    print(f"INSPECCIONANDO: {name}")
    print(f"URL: {url}")
    print(f"{'='*80}\n")
    
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-images")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={HEADERS['User-Agent']}")
    
    driver = webdriver.Chrome(options=options)
    best_selector = None
    best_count = 0
    
    try:
        driver.get(url)
        time.sleep(5)  # Más tiempo para carga dinámica
        
        # Hacer scroll para cargar más contenido
        for _ in range(3):
            driver.execute_script("window.scrollBy(0, 500)")
            time.sleep(1)
        
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # Buscar elementos comunes de artículos
        print("BÚSQUEDA DE SELECTORES POTENCIALES:\n")
        
        selectors_to_try = [
            ("article", "article"),
            ("article a", "article a"),
            ("h1 a", "h1 a"),
            ("h2 a", "h2 a"),
            ("h3 a", "h3 a"),
            (".post", ".post"),
            (".post a", ".post a"),
            (".article", ".article"),
            (".article a", ".article a"),
            (".news", ".news"),
            (".news a", ".news a"),
            (".news-article a", ".news-article a"),
            (".item", ".item"),
            (".item a", ".item a"),
            (".entry-title a", ".entry-title a"),
            ("div.entry-title a", "div.entry-title a"),
            ("h2.entry-title a", "h2.entry-title a"),
            (".post-title a", ".post-title a"),
            (".headline a", ".headline a"),
            (".news-item a", ".news-item a"),
            (".story-title a", ".story-title a"),
            ("a.article-link", "a.article-link"),
            ("a.story-link", "a.story-link"),
        ]
        
        for desc, selector in selectors_to_try:
            elements = soup.select(selector)
            if elements:
                count_with_text = len([e for e in elements if e.get_text(strip=True)])
                if count_with_text > 0:
                    print(f"✓ '{selector}' — {count_with_text} elementos con texto")
                    for i, el in enumerate(elements[:2]):
                        texto = el.get_text(strip=True)[:60]
                        href = el.get("href", "")
                        if href and texto:
                            print(f"  [{i+1}] {texto}...")
                            if count_with_text > best_count:
                                best_selector = selector
                                best_count = count_with_text
                    print()
        
        print(f"\n➤ MEJOR SELECTOR: '{best_selector}' ({best_count} elementos)\n")
        
        return best_selector
        
    finally:
        driver.quit()

if __name__ == "__main__":
    print("\n" + "="*80)
    print("HERRAMIENTA DE INSPECCIÓN Y ACTUALIZACIÓN DE SELECTORES")
    print("="*80)
    
    for name, url in SITES:
        try:
            selector = inspect_site(name, url)
            if selector:
                print(f"\n➜ Actualizando base de datos para {name}...")
                update_selector_in_db(name, selector)
            else:
                print(f"⚠ No se pudo encontrar un selector válido para {name}")
        except Exception as e:
            print(f"✗ ERROR inspeccionando {name}: {e}\n")
    
    print("\n" + "="*80)
    print("INSPECCIÓN COMPLETADA")
    print("="*80 + "\n")
