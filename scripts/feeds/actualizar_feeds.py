from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
actualizar_feeds.py
-------------------
Actualiza las URLs de feed RSS en la tabla fuentes para las fuentes
que fallaban por no tener feed configurado correctamente.

Ejecutar desde la raíz del proyecto:
    python actualizar_feeds.py
"""

import os
import sys
from pathlib import Path

# Cargar .env
from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT_DIR / '.env')

import psycopg2

ACTUALIZACIONES = [
    # (nombre en BD,                     nueva URL de feed RSS)
    ("Noticias de Minería Argentina",    "https://noticiasdemineria.com.ar/feed/"),
    ("Revista Digital Minera",           "https://www.redimin.cl/feed/"),
    ("Minería Hoy",                      "https://www.mineriahoy.com/feed/"),
    ("Rubro Minero",                     "https://www.rumbominero.com/feed/"),
    ("ACADES",                           "https://www.acades.cl/feed/"),
]

def main():
    try:
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"],
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=10,
        )
    except Exception as e:
        print(f"❌ Error de conexión a la BD: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                for nombre, feed_url in ACTUALIZACIONES:
                    cur.execute(
                        "UPDATE fuentes SET url_rss = %s WHERE nombre = %s",
                        (feed_url, nombre)
                    )
                    if cur.rowcount == 0:
                        print(f"⚠️  No se encontró la fuente: '{nombre}'  ← revisar nombre exacto en BD")
                    else:
                        print(f"✅ {nombre}")
                        print(f"   feed → {feed_url}")

        print("\nCambios guardados en la BD.")

        # Verificar resultado
        print("\n── Estado final de fuentes afectadas ──────────────────────")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT nombre, url, url_rss, activa
                FROM fuentes
                WHERE nombre = ANY(%s)
                ORDER BY nombre
            """, ([n for n, _ in ACTUALIZACIONES],))
            rows = cur.fetchall()
            if not rows:
                print("  (ninguna fila encontrada — revisar nombres)")
            for nombre, url, url_rss, activa in rows:
                estado = "✅ activa" if activa else "⏸  inactiva"
                print(f"  {estado}  {nombre}")
                print(f"           URL:  {url}")
                print(f"           RSS:  {url_rss or '(vacío)'}")

    except Exception as e:
        print(f"❌ Error al actualizar: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
