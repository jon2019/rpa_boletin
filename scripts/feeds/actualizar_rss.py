from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
actualizar_rss.py
-----------------
Actualiza las URLs de RSS en la tabla fuentes para las fuentes que no tenían
feed configurado correctamente.

Ejecutar desde la raíz del proyecto:
    python actualizar_rss.py
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=ROOT_DIR / '.env')

ACTUALIZACIONES = [
    {
        "url_fuente": "https://noticiasdemineria.com.ar",
        "url_rss":    "https://noticiasdemineria.com.ar/feed/",
        "nombre":     "Noticias de Minería Argentina",
    },
    {
        "url_fuente": "https://www.redimin.cl",
        "url_rss":    "https://www.redimin.cl/feed/",
        "nombre":     "Revista Digital Minera",
    },
    {
        "url_fuente": "https://www.mineriahoy.com",
        "url_rss":    "https://www.mineriahoy.com/feed/",
        "nombre":     "Minería Hoy",
    },
    {
        "url_fuente": "https://www.rumbominero.com",
        "url_rss":    "https://www.rumbominero.com/feed/",
        "nombre":     "Rubro Minero",
    },
    {
        "url_fuente": "https://www.acades.cl",
        "url_rss":    "https://www.acades.cl/feed/",
        "nombre":     "ACADES",
    },
]

def main():
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
    except Exception as e:
        print(f"❌ No se pudo conectar a la BD: {e}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            for f in ACTUALIZACIONES:
                # Buscar por URL base (más confiable que por nombre)
                cur.execute(
                    "SELECT id, nombre, url_rss FROM fuentes WHERE url = %s",
                    (f["url_fuente"],)
                )
                row = cur.fetchone()
                if not row:
                    print(f"⚠️  No encontrada en BD: {f['nombre']} ({f['url_fuente']})")
                    continue

                fuente_id, nombre_bd, rss_actual = row
                if rss_actual == f["url_rss"]:
                    print(f"✅ Sin cambios (ya correcto): {nombre_bd}")
                    continue

                cur.execute(
                    "UPDATE fuentes SET url_rss = %s, metodo = 'rss' WHERE id = %s",
                    (f["url_rss"], fuente_id)
                )
                print(f"✅ Actualizado: {nombre_bd}")
                print(f"   Antes:  {rss_actual or '(vacío)'}")
                print(f"   Ahora:  {f['url_rss']}")

    conn.close()
    print("\nListo. Las URLs de RSS han sido actualizadas.")
    print("En la próxima ejecución del pipeline estas fuentes se procesarán correctamente.")

if __name__ == "__main__":
    main()
