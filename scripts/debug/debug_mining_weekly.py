from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
Guarda el HTML de Mining Weekly para investigación manual.
"""

import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from pathlib import Path

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

options = Options()
options.add_argument("--headless")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument(f"--user-agent={HEADERS['User-Agent']}")

driver = webdriver.Chrome(options=options)
try:
    driver.get("https://www.miningweekly.com")
    time.sleep(5)
    
    for _ in range(3):
        driver.execute_script("window.scrollBy(0, 300)")
        time.sleep(1)
    
    html = driver.page_source
    
    # Guardar HTML con encoding UTF-8
    output = ROOT_DIR / "tests" / "output" / "debug" / "mining_weekly_html.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    
    print(f"✓ HTML guardado en: {output}")
    print(f"Tamaño: {len(html)} caracteres")
    
    # Mostrar información de debug
    print("\nPrimeros 3000 caracteres del HTML:")
    print("=" * 80)
    print(html[:3000])
    print("=" * 80)
    
finally:
    driver.quit()
