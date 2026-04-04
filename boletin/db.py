"""
db.py
-----
Gestión de la base de datos PostgreSQL.
Toda la configuración se lee desde variables de entorno — sin valores hardcodeados.

Tablas:
    fuentes             — Catálogo de fuentes de noticias (reemplaza sources.py)
    score_reglas        — Reglas de puntuación configurables (reemplaza constantes)
    score_empresas      — Empresas de alto valor para scoring
    score_keywords      — Keywords de contratos/licitaciones
    noticias_enviadas   — Historial de URLs incluidas en boletines
    ejecucion_fuentes   — Checkpoint por fuente+fecha (control de reintentos)
    envios_log          — Log de cada boletín enviado

Variables de entorno requeridas:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import hashlib
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from pathlib import Path
import psycopg2
from pathlib import Path
import psycopg2.extras
from dotenv import load_dotenv
import retrier

# .env vive en rpa_boletin/ — un nivel arriba del código
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)
logger = logging.getLogger(__name__)

_DB_CONFIG = {
    "host":     os.environ["DB_HOST"],
    "port":     os.getenv("DB_PORT", "5432"),
    "dbname":   os.environ["DB_NAME"],
    "user":     os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "connect_timeout": 10,
    "options":  "-c client_encoding=UTF8",
}


# ══════════════════════════════════════════════════════════════════════════════
# CONEXIÓN
# ══════════════════════════════════════════════════════════════════════════════

@contextmanager
def get_connection():
    conn = psycopg2.connect(**_DB_CONFIG)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

def init_db() -> None:
    """
    Crea todas las tablas e índices si no existen.
    Luego llama a seed_data() para poblar los datos iniciales si las tablas
    están vacías (primera ejecución).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""

            -- ── FUENTES ───────────────────────────────────────────────────────
            -- Catálogo de fuentes de noticias. Reemplaza sources.py.
            -- activa=FALSE desactiva la fuente sin borrarla.
            CREATE TABLE IF NOT EXISTS fuentes (
                id               SERIAL PRIMARY KEY,
                nombre           TEXT        NOT NULL,
                url              TEXT        NOT NULL UNIQUE,
                url_rss          TEXT,                        -- NULL = scraping
                pais             VARCHAR(50) NOT NULL,        -- Chile|Peru|Argentina|Internacional
                metodo           VARCHAR(10) NOT NULL         -- rss | scrape
                                 CHECK (metodo IN ('rss','scrape')),
                scrape_selector  TEXT,                        -- CSS selector si metodo=scrape
                nota             TEXT,                        -- comentario libre
                activa           BOOLEAN     NOT NULL DEFAULT TRUE,
                creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_fuentes_activa ON fuentes(activa);
            CREATE INDEX IF NOT EXISTS idx_fuentes_pais   ON fuentes(pais);

            -- ── SCORE REGLAS ──────────────────────────────────────────────────
            -- Pesos del sistema de puntuación. Editables desde la DB.
            -- codigo es la clave que usa scorer.py para leer cada valor.
            CREATE TABLE IF NOT EXISTS score_reglas (
                id          SERIAL PRIMARY KEY,
                codigo      VARCHAR(50) NOT NULL UNIQUE,   -- ej: 'contrato', 'empresa_conocida'
                descripcion TEXT        NOT NULL,
                puntos      INTEGER     NOT NULL,
                activa      BOOLEAN     NOT NULL DEFAULT TRUE
            );

            -- ── SCORE EMPRESAS ────────────────────────────────────────────────
            -- Lista de empresas de alto valor para el pre-scoring local.
            CREATE TABLE IF NOT EXISTS score_empresas (
                id        SERIAL PRIMARY KEY,
                nombre    TEXT    NOT NULL UNIQUE,
                activa    BOOLEAN NOT NULL DEFAULT TRUE
            );

            -- ── SCORE KEYWORDS ────────────────────────────────────────────────
            -- Keywords que identifican noticias de contratos/licitaciones.
            CREATE TABLE IF NOT EXISTS score_keywords (
                id       SERIAL PRIMARY KEY,
                keyword  TEXT    NOT NULL UNIQUE,
                activa   BOOLEAN NOT NULL DEFAULT TRUE
            );

            -- ── PAISES ───────────────────────────────────────────────────────
            -- Catálogo de países del boletín. Configurable desde la DB.
            -- cuota: noticias que se incluyen por país en cada boletín.
            -- orden: posición en el boletín (1=primero).
            -- codigo_iso: código ISO-3166 para banderas y referencias.
            -- nombre_en: nombre en inglés para la columna derecha del boletín.
            -- activo=FALSE excluye el país del boletín sin borrarlo.
            CREATE TABLE IF NOT EXISTS paises (
                id          SERIAL PRIMARY KEY,
                nombre      VARCHAR(100) NOT NULL UNIQUE,
                nombre_en   VARCHAR(100) NOT NULL,
                codigo_iso  VARCHAR(3)   NOT NULL,
                bandera     VARCHAR(10)  NOT NULL,
                cuota       INTEGER      NOT NULL DEFAULT 10
                            CHECK (cuota > 0),
                orden       INTEGER      NOT NULL DEFAULT 99,
                activo      BOOLEAN      NOT NULL DEFAULT TRUE
            );
            CREATE INDEX IF NOT EXISTS idx_paises_activo ON paises(activo);
            CREATE INDEX IF NOT EXISTS idx_paises_orden  ON paises(orden);

            -- ── NOTICIAS ENVIADAS ─────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS noticias_enviadas (
                id          SERIAL PRIMARY KEY,
                url_hash    VARCHAR(64) UNIQUE NOT NULL,
                titulo      TEXT        NOT NULL,
                fuente      TEXT        NOT NULL,
                pais        VARCHAR(50) NOT NULL,
                url         TEXT        NOT NULL,
                enviado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_ne_url_hash   ON noticias_enviadas(url_hash);
            CREATE INDEX IF NOT EXISTS idx_ne_enviado_en ON noticias_enviadas(enviado_en);

            -- ── EJECUCION FUENTES ─────────────────────────────────────────────
            -- Checkpoint por fuente y fecha. Control de reintentos.
            CREATE TABLE IF NOT EXISTS ejecucion_fuentes (
                id                  SERIAL PRIMARY KEY,
                url_fuente          TEXT        NOT NULL,
                nombre_fuente       TEXT        NOT NULL,
                fecha_ejecucion     DATE        NOT NULL,
                scraping_ok         BOOLEAN     NOT NULL DEFAULT FALSE,
                ia_ok               BOOLEAN     NOT NULL DEFAULT FALSE,
                noticias_obtenidas  INTEGER     NOT NULL DEFAULT 0,
                noticias_enviadas   INTEGER     NOT NULL DEFAULT 0,
                error_detalle       TEXT,
                creado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (url_fuente, fecha_ejecucion)
            );
            CREATE INDEX IF NOT EXISTS idx_ef_fecha     ON ejecucion_fuentes(fecha_ejecucion);
            CREATE INDEX IF NOT EXISTS idx_ef_url_fecha ON ejecucion_fuentes(url_fuente, fecha_ejecucion);

            -- ── ENVIOS LOG ────────────────────────────────────────────────────
            -- por_pais: JSONB con conteo por país {"Chile": 10, "Peru": 8, ...}
            -- Flexible para cualquier cantidad de países sin cambiar el schema.
            CREATE TABLE IF NOT EXISTS envios_log (
                id              SERIAL PRIMARY KEY,
                fecha           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                total_noticias  INTEGER     NOT NULL,
                por_pais        JSONB       NOT NULL DEFAULT '{}',
                ok              BOOLEAN     NOT NULL DEFAULT TRUE
            );

            """)

    logger.info("PostgreSQL schema OK — %s:%s/%s",
                _DB_CONFIG["host"], _DB_CONFIG["port"], _DB_CONFIG["dbname"])

    # Poblar datos iniciales si las tablas están vacías
    _seed_if_empty()


# ══════════════════════════════════════════════════════════════════════════════
# SEED — datos iniciales (solo si las tablas están vacías)
# ══════════════════════════════════════════════════════════════════════════════

def _seed_if_empty() -> None:
    """Inserta los datos iniciales si las tablas de configuración están vacías."""
    with get_connection() as conn:
        with conn.cursor() as cur:

            # ── Países ───────────────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) FROM paises")
            if cur.fetchone()[0] == 0:
                paises = [
                    # (nombre, nombre_en, codigo_iso, bandera, cuota, orden)
                    ("Chile",     "Chile",     "CHL", "🇨🇱", 10, 1),
                    ("Peru",      "Peru",      "PER", "🇵🇪", 10, 2),
                    ("Argentina", "Argentina", "ARG", "🇦🇷", 10, 3),
                ]
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO paises (nombre, nombre_en, codigo_iso, bandera, cuota, orden)
                    VALUES %s
                """, paises)
                logger.info("Seed: %d países insertados", len(paises))

            # ── Fuentes ───────────────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) FROM fuentes")
            if cur.fetchone()[0] == 0:
                fuentes = [
                    # Chile — RSS
                    ("Nueva Minería y Energía",      "https://www.nuevamineria.com",   "https://www.nuevamineria.com/feed",             "Chile",         "rss",    None,                 None),
                    ("H2 News",                       "https://www.h2news.cl",          "https://www.h2news.cl/feed",                    "Chile",         "rss",    None,                 None),
                    ("Revista Digital Minera",        "https://www.redimin.cl",         "https://www.redimin.cl/feed",                   "Chile",         "rss",    None,                 None),
                    ("Reporte Minero y Energético",   "https://www.reporteminero.cl",   "https://www.reporteminero.cl/feed",             "Chile",         "rss",    None,                 None),
                    ("Minería Chilena (MCH)",         "https://www.mch.cl",             "https://www.mch.cl/feed",                       "Chile",         "rss",    None,                 None),
                    ("ACERA",                         "https://www.acera.cl",           "https://www.acera.cl/feed",                     "Chile",         "rss",    None,                 None),
                    ("El Pingüino",                   "https://elpinguino.com",         "https://elpinguino.com/feed",                   "Chile",         "rss",    None,                 None),
                    ("La Prensa Austral",             "https://laprensaaustral.cl",     "https://laprensaaustral.cl/feed",               "Chile",         "rss",    None,                 None),
                    # Chile — Scrape
                    ("Portal Minero",                 "https://www.portalminero.com",   None,                                            "Chile",         "scrape", "article h2 a",       None),
                    ("SEA",                           "https://www.sea.gob.cl",         None,                                            "Chile",         "scrape", ".noticias a",         None),
                    ("ACADES",                        "https://www.acades.cl",          None,                                            "Chile",         "scrape", ".entry-title a",      None),
                    # Perú — RSS
                    ("Rubro Minero",                  "https://www.rumbominero.com",    "https://www.rumbominero.com/feed",              "Peru",          "rss",    None,                 None),
                    ("Minería & Energía Perú",        "https://mineriaenergia.com",     "https://mineriaenergia.com/feed",               "Peru",          "rss",    None,                 None),
                    ("Minería Hoy",                   "https://www.mineriahoy.com",     "https://www.mineriahoy.com/feed",               "Peru",          "rss",    None,                 None),
                    ("Andina",                        "https://andina.pe",              "https://andina.pe/agencia/rss.aspx",            "Peru",          "rss",    None,                 None),
                    # Perú — Scrape
                    ("Diario Minero",                 "https://www.diariominero.com",   None,                                            "Peru",          "scrape", "h2.entry-title a",   None),
                    # Argentina — RSS
                    ("Noticias de Minería Argentina", "https://noticiasdemineria.com.ar",  "https://noticiasdemineria.com.ar/feed",      "Argentina",     "rss",    None,                 None),
                    ("Minería y Desarrollo",          "https://www.mineriaydesarrollo.com","https://www.mineriaydesarrollo.com/feed",    "Argentina",     "rss",    None,                 None),
                    # Argentina — Scrape
                    ("Panorama Minero",               "https://www.panorama-minero.com","None",                                          "Argentina",     "scrape", "h2.entry-title a",   None),
                    ("Argentina.gob.ar Energía",      "https://www.argentina.gob.ar/economia/energia", None,                            "Argentina",     "scrape", ".news-item a",        None),
                    # Internacional — RSS
                    ("Energías Renovables",           "https://www.energias-renovables.com","https://www.energias-renovables.com/feed", "Internacional", "rss",    None,                 None),
                    ("Periódico de la Energía",       "https://elperiodicodelaenergia.com","https://elperiodicodelaenergia.com/feed",   "Internacional", "rss",    None,                 None),
                    ("Mining Digital",                "https://miningdigital.com",      "https://miningdigital.com/rss.xml",             "Internacional", "rss",    None,                 None),
                    ("Mining.com",                    "https://www.mining.com",         "https://www.mining.com/feed",                   "Internacional", "rss",    None,                 None),
                    ("Mining Weekly",                 "https://www.miningweekly.com",   "https://www.miningweekly.com/rss/rss.xml",      "Internacional", "rss",    None,                 None),
                    # Internacional — Scrape
                    ("BNamericas",                    "https://www.bnamericas.com",     None,                                            "Internacional", "scrape", "h3.article-title a", "Paywall parcial"),
                ]
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO fuentes (nombre, url, url_rss, pais, metodo, scrape_selector, nota)
                    VALUES %s
                """, fuentes)
                logger.info("Seed: %d fuentes insertadas", len(fuentes))

            # ── Score Reglas ──────────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) FROM score_reglas")
            if cur.fetchone()[0] == 0:
                reglas = [
                    ("contrato",        "Noticia de contrato / licitación / adjudicación",          250),
                    ("empresa_conocida","Contrato que involucra empresa conocida del sector",        150),
                    ("empresa_noticia", "Noticia relevante de empresa importante (sin contrato)",     80),
                    ("reciente_3dias",  "Noticia publicada hace 3 días o menos",                     60),
                    ("reciente_hoy",    "Noticia publicada hoy",                                     25),
                ]
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO score_reglas (codigo, descripcion, puntos)
                    VALUES %s
                """, reglas)
                logger.info("Seed: %d reglas de scoring insertadas", len(reglas))

            # ── Score Empresas ────────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) FROM score_empresas")
            if cur.fetchone()[0] == 0:
                empresas = [
                    ("Caterpillar",), ("Komatsu",), ("Hitachi",), ("Epiroc",), ("Sandvik",),
                    ("Liebherr",), ("Terex",), ("XCMG",), ("SSAB",), ("FLSmidth",),
                    ("Doosan",), ("Schlam",), ("Westech",), ("Bechtel",), ("Fluor",),
                    ("Worley",), ("Hatch",), ("Jacobs",), ("Ausenco",), ("Techint",),
                    ("Besalco",), ("Wood",), ("Tecnimont",), ("ABB",), ("Siemens",),
                    ("Schneider",), ("GE",), ("CG Power",), ("Hyosung",), ("Mitsubishi",),
                    ("Ormazabal",), ("Toshiba",), ("WEG",), ("Vestas",), ("Acciona",),
                    ("Enercon",), ("Goldwind",), ("Nordex",), ("Nexans",), ("Prysmian",),
                    ("Codelco",), ("Antofagasta",), ("BHP",), ("Anglo American",), ("Glencore",),
                ]
                psycopg2.extras.execute_values(cur,
                    "INSERT INTO score_empresas (nombre) VALUES %s", empresas)
                logger.info("Seed: %d empresas insertadas", len(empresas))

            # ── Score Keywords ────────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) FROM score_keywords")
            if cur.fetchone()[0] == 0:
                keywords = [
                    ("contrato",), ("licitación",), ("adjudicación",), ("licitacion",),
                    ("adjudicacion",), ("EPC",), ("EPCM",), ("concesión",), ("concesion",),
                    ("suministro",), ("award",), ("contract",), ("tender",), ("bid",),
                    ("ganó contrato",), ("gano contrato",),
                ]
                psycopg2.extras.execute_values(cur,
                    "INSERT INTO score_keywords (keyword) VALUES %s", keywords)
                logger.info("Seed: %d keywords insertados", len(keywords))

            # ── Score Empresas Conocidas ──────────────────────────────────────
            # Lista específica de empresas que al FIRMAR un contrato activan
            # el bonus empresa_conocida (+150). Separada de score_empresas.
            cur.execute("SELECT COUNT(*) FROM score_empresas_conocidas")
            if cur.fetchone()[0] == 0:
                empresas_conocidas = [
                    # Ingeniería y construcción EPC/EPCM
                    ("Bechtel",), ("Fluor",), ("Worley",), ("Hatch",), ("Jacobs",),
                    ("Ausenco",), ("Techint",), ("Besalco",), ("Wood",), ("Tecnimont",),
                    ("SNC-Lavalin",), ("Aecom",), ("Amec Foster Wheeler",), ("Mott MacDonald",),
                    # Equipos mineros grandes
                    ("Caterpillar",), ("Komatsu",), ("Hitachi",), ("Epiroc",), ("Sandvik",),
                    ("Liebherr",), ("Terex",), ("XCMG",), ("FLSmidth",),
                    # Tecnología y energía
                    ("ABB",), ("Siemens",), ("Schneider Electric",), ("GE Grid Solutions",),
                    ("Mitsubishi Electric",), ("Toshiba Energy",), ("WEG",),
                    # Energía renovable
                    ("Vestas",), ("Acciona",), ("Goldwind",), ("Nordex",),
                    # Cables
                    ("Nexans",), ("Prysmian",),
                    # Mineras grandes (cuando firman como contratantes)
                    ("Codelco",), ("BHP",), ("Anglo American",), ("Glencore",),
                    ("Antofagasta Minerals",), ("Teck",), ("Freeport-McMoRan",),
                ]
                psycopg2.extras.execute_values(cur,
                    "INSERT INTO score_empresas_conocidas (nombre) VALUES %s", empresas_conocidas)
                logger.info("Seed: %d empresas conocidas insertadas", len(empresas_conocidas))

    logger.info("Seed completado.")


# ══════════════════════════════════════════════════════════════════════════════
# LECTURA DE CONFIGURACIÓN DESDE DB
# ══════════════════════════════════════════════════════════════════════════════

def get_fuentes_activas() -> list[dict]:
    """
    Retorna las fuentes activas desde la tabla `fuentes`.
    Reemplaza la constante SOURCES de sources.py.
    El formato del dict es idéntico al que usaba sources.py para
    mantener compatibilidad con scraper.py sin cambios.
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, nombre AS name, url, url_rss AS rss,
                       pais AS country, metodo AS method,
                       scrape_selector, nota AS note
                FROM fuentes
                WHERE activa = TRUE
                ORDER BY pais, nombre
            """)
            rows = cur.fetchall()

    fuentes = [dict(r) for r in rows]
    logger.info("Fuentes activas cargadas desde DB: %d", len(fuentes))
    return fuentes


def get_paises_activos() -> list[dict]:
    """
    Retorna los países activos ordenados por el campo `orden`.
    Cada dict tiene: nombre, nombre_en, codigo_iso, bandera, cuota.
    Reemplaza la lista hardcodeada QUOTA = {"Chile":10, "Peru":10, "Argentina":10}.

    Para agregar un país nuevo:
        INSERT INTO paises (nombre, nombre_en, codigo_iso, bandera, cuota, orden)
        VALUES ('Colombia', 'Colombia', 'COL', '🇨🇴', 10, 4);

    Para cambiar la cuota de un país:
        UPDATE paises SET cuota = 15 WHERE nombre = 'Chile';

    Para desactivar un país sin borrarlo:
        UPDATE paises SET activo = FALSE WHERE nombre = 'Peru';
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT nombre, nombre_en, codigo_iso, bandera, cuota
                FROM paises
                WHERE activo = TRUE
                ORDER BY orden ASC
            """)
            rows = cur.fetchall()

    paises = [dict(r) for r in rows]
    logger.info("Países activos: %s",
                ", ".join(f"{p['nombre']}({p['cuota']})" for p in paises))
    return paises


def get_score_config() -> dict:
    """
    Retorna la configuración completa de scoring desde la DB:
      {
        "reglas":             { "contrato": 250, "empresa_conocida": 150, ... },
        "empresas":           ["Caterpillar", "Komatsu", ...],   -- cualquier mención → +80
        "empresas_conocidas": ["Bechtel", "Fluor", ...],         -- firman contrato → +150
        "keywords":           ["contrato", "licitacion", ...]
      }

    Diferencia clave:
      - score_empresas       → empresa_noticia (+80): aparece en cualquier noticia
      - score_empresas_conocidas → empresa_conocida (+150): solo cuando hay contrato
    """
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT codigo, puntos FROM score_reglas WHERE activa = TRUE")
            reglas = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("SELECT nombre FROM score_empresas WHERE activa = TRUE")
            empresas = [row[0] for row in cur.fetchall()]

            cur.execute("SELECT nombre FROM score_empresas_conocidas WHERE activa = TRUE")
            empresas_conocidas = [row[0] for row in cur.fetchall()]

            cur.execute("SELECT keyword FROM score_keywords WHERE activa = TRUE")
            keywords = [row[0] for row in cur.fetchall()]

    logger.info("Score config: %d reglas | %d empresas | %d empresas_conocidas | %d keywords",
                len(reglas), len(empresas), len(empresas_conocidas), len(keywords))
    return {
        "reglas":             reglas,
        "empresas":           empresas,
        "empresas_conocidas": empresas_conocidas,
        "keywords":           keywords,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT DE EJECUCIÓN POR FUENTE
# ══════════════════════════════════════════════════════════════════════════════

def _hoy() -> date:
    return datetime.now(tz=timezone.utc).date()


def fuentes_pendientes_hoy(fuentes: list[dict]) -> list[dict]:
    """
    Filtra la lista recibida y retorna solo las fuentes que aún
    no completaron scraping+IA exitosamente hoy.
    """
    hoy = _hoy()
    urls = [f["url"] for f in fuentes]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT url_fuente FROM ejecucion_fuentes
                WHERE fecha_ejecucion = %s
                  AND scraping_ok = TRUE
                  AND ia_ok = TRUE
                  AND url_fuente = ANY(%s)
            """, (hoy, urls))
            completadas = {row[0] for row in cur.fetchall()}

    pendientes = [f for f in fuentes if f["url"] not in completadas]
    logger.info("Fuentes hoy [%s]: %d total | %d completadas | %d pendientes",
                hoy, len(fuentes), len(completadas), len(pendientes))
    return pendientes


def registrar_scraping(url_fuente: str, nombre: str, ok: bool,
                       noticias_obtenidas: int = 0, error: str = None) -> None:
    hoy   = _hoy()
    ahora = datetime.now(tz=timezone.utc)

    def _upsert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ejecucion_fuentes
                        (url_fuente, nombre_fuente, fecha_ejecucion,
                         scraping_ok, noticias_obtenidas, error_detalle, actualizado_en)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (url_fuente, fecha_ejecucion) DO UPDATE SET
                        scraping_ok        = EXCLUDED.scraping_ok,
                        noticias_obtenidas = EXCLUDED.noticias_obtenidas,
                        error_detalle      = CASE WHEN EXCLUDED.scraping_ok THEN NULL
                                                 ELSE EXCLUDED.error_detalle END,
                        actualizado_en     = EXCLUDED.actualizado_en
                """, (url_fuente, nombre, hoy, ok, noticias_obtenidas,
                      error[:500] if error else None, ahora))

    _, db_ok = retrier.con_reintentos(
        fn=_upsert,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=nombre,
        url=url_fuente,
        registrar_en_collector=False,
    )
    estado = "OK" if ok else "FALLO"
    if db_ok:
        logger.info("Scraping [%s] %s — %d noticias", nombre, estado, noticias_obtenidas)
    else:
        logger.error("No se pudo registrar checkpoint de scraping para %s", nombre)


def registrar_ia(url_fuente: str, ok: bool,
                 noticias_enviadas: int = 0, error: str = None) -> None:
    hoy   = _hoy()
    ahora = datetime.now(tz=timezone.utc)

    def _update():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ejecucion_fuentes SET
                        ia_ok             = %s,
                        noticias_enviadas = %s,
                        error_detalle     = CASE WHEN %s THEN NULL ELSE %s END,
                        actualizado_en    = %s
                    WHERE url_fuente = %s AND fecha_ejecucion = %s
                """, (ok, noticias_enviadas, ok,
                      error[:500] if error else None,
                      ahora, url_fuente, hoy))

    _, db_ok = retrier.con_reintentos(
        fn=_update,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente=url_fuente,
        url=url_fuente,
        registrar_en_collector=False,
    )
    if not db_ok:
        logger.error("No se pudo registrar resultado IA para %s en DB", url_fuente)


def resumen_ejecucion_hoy() -> dict:
    hoy = _hoy()
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                         AS total,
                    SUM(CASE WHEN scraping_ok THEN 1 ELSE 0 END)    AS scraping_ok,
                    SUM(CASE WHEN ia_ok       THEN 1 ELSE 0 END)    AS ia_ok,
                    SUM(CASE WHEN scraping_ok AND ia_ok THEN 1 ELSE 0 END) AS completas,
                    SUM(noticias_obtenidas)                          AS noticias_obtenidas,
                    SUM(noticias_enviadas)                           AS noticias_al_boletin
                FROM ejecucion_fuentes WHERE fecha_ejecucion = %s
            """, (hoy,))
            return dict(cur.fetchone())


# ══════════════════════════════════════════════════════════════════════════════
# HISTORIAL DE NOTICIAS ENVIADAS
# ══════════════════════════════════════════════════════════════════════════════

def _hash_url(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def filtrar_enviadas(noticias: list[dict]) -> list[dict]:
    if not noticias:
        return []
    hashes = {_hash_url(n["url"]): n for n in noticias}

    def _query():
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT url_hash FROM noticias_enviadas WHERE url_hash = ANY(%s)",
                    (list(hashes.keys()),))
                return {row[0] for row in cur.fetchall()}

    resultado, ok = retrier.con_reintentos(
        fn=_query,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="filtrar_enviadas",
        url="noticias_enviadas",
    )
    ya = resultado if ok else set()
    nuevas = [n for h, n in hashes.items() if h not in ya]
    logger.info("Filtro historial: %d total -> %d nuevas (%d ya enviadas)",
                len(noticias), len(nuevas), len(ya))
    return nuevas


def marcar_enviadas(noticias: list[dict]) -> None:
    if not noticias:
        return
    ahora = datetime.now(tz=timezone.utc)
    rows = [(
        _hash_url(n["url"]), n["titulo"][:500], n["fuente"][:200],
        n["pais"][:50], n["url"][:2000], ahora
    ) for n in noticias]

    def _insert():
        with get_connection() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO noticias_enviadas
                        (url_hash, titulo, fuente, pais, url, enviado_en)
                    VALUES %s ON CONFLICT (url_hash) DO NOTHING
                """, rows)

    _, ok = retrier.con_reintentos(
        fn=_insert,
        tipo_error=retrier.TipoError.BASE_DE_DATOS,
        fuente="marcar_enviadas",
        url="noticias_enviadas",
    )
    if ok:
        logger.info("Marcadas %d noticias como enviadas", len(rows))
    else:
        logger.error("No se pudieron marcar %d noticias en DB", len(rows))


def registrar_envio(noticias: list[dict], ok: bool = True) -> None:
    conteo = {"Chile": 0, "Peru": 0, "Argentina": 0}
    for n in noticias:
        pais = n.get("pais_boletin", n.get("pais", ""))
        if pais in conteo:
            conteo[pais] += 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO envios_log (fecha, total_noticias, chile, peru, argentina, ok)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (datetime.now(tz=timezone.utc), len(noticias),
                  conteo["Chile"], conteo["Peru"], conteo["Argentina"], ok))
    logger.info("Envío registrado — Total:%d CL:%d PE:%d AR:%d ok:%s",
                len(noticias), conteo["Chile"], conteo["Peru"], conteo["Argentina"], ok)


def limpiar_antiguos(dias: int = 60) -> None:
    limite = datetime.now(tz=timezone.utc) - timedelta(days=dias)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM noticias_enviadas WHERE enviado_en < %s", (limite,))
            n1 = cur.rowcount
            cur.execute("DELETE FROM ejecucion_fuentes WHERE creado_en < %s", (limite,))
            n2 = cur.rowcount
    logger.info("Limpieza DB: %d noticias + %d checkpoints eliminados", n1, n2)


def test_conexion() -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                v = cur.fetchone()[0]
        logger.info("Conexión PostgreSQL OK — %s", v)
        return True
    except Exception as e:
        logger.error("Error de conexión PostgreSQL: %s", e)
        return False
