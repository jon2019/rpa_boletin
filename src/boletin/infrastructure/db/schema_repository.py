"""Creación de schema y carga inicial de datos."""

from __future__ import annotations

import logging

import psycopg2.extras

from boletin.infrastructure.db.connection import get_connection, get_db_config

logger = logging.getLogger(__name__)



def init_db() -> None:
    """Crea tablas e índices si no existen y luego ejecuta el seed inicial."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""

            -- —— FUENTES ————————————————————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS fuentes (
                id               SERIAL PRIMARY KEY,
                nombre           TEXT        NOT NULL,
                url              TEXT        NOT NULL UNIQUE,
                url_rss          TEXT,
                pais             VARCHAR(50) NOT NULL,
                metodo           VARCHAR(10),
                scrape_selector  TEXT,
                nota             TEXT,
                activa           BOOLEAN     NOT NULL DEFAULT TRUE,
                creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                actualizado_en   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_fuentes_activa ON fuentes(activa);
            CREATE INDEX IF NOT EXISTS idx_fuentes_pais   ON fuentes(pais);
            ALTER TABLE fuentes ALTER COLUMN metodo DROP NOT NULL;
            ALTER TABLE fuentes DROP CONSTRAINT IF EXISTS fuentes_metodo_check;
            ALTER TABLE fuentes ADD CONSTRAINT fuentes_metodo_check
                CHECK (metodo IN ('rss','scrape') OR metodo IS NULL);

            -- —— SCORE REGLAS ———————————————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS score_reglas (
                id          SERIAL PRIMARY KEY,
                codigo      VARCHAR(50) NOT NULL UNIQUE,
                descripcion TEXT        NOT NULL,
                puntos      INTEGER     NOT NULL,
                activa      BOOLEAN     NOT NULL DEFAULT TRUE
            );

            -- —— SCORE EMPRESAS —————————————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS score_empresas (
                id        SERIAL PRIMARY KEY,
                nombre    TEXT    NOT NULL UNIQUE,
                activa    BOOLEAN NOT NULL DEFAULT TRUE
            );

            -- —— SCORE EMPRESAS CONOCIDAS —————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS score_empresas_conocidas (
                id        SERIAL PRIMARY KEY,
                nombre    TEXT    NOT NULL UNIQUE,
                activa    BOOLEAN NOT NULL DEFAULT TRUE
            );

            -- —— SCORE KEYWORDS —————————————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS score_keywords (
                id       SERIAL PRIMARY KEY,
                keyword  TEXT    NOT NULL UNIQUE,
                activa   BOOLEAN NOT NULL DEFAULT TRUE,
                tipo     VARCHAR(20) NOT NULL DEFAULT 'contrato'
            );
            ALTER TABLE score_keywords ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) NOT NULL DEFAULT 'contrato';

            -- —— SCORE EMPRESA TIPO ——————————————————————————————————————————————————————
            -- Scoring diferenciado por tipo de empresa (Opción C: sin tocar score_empresas)
            -- tipos: empresa_minera | empresa_epc | empresa_energia
            --        equipo_global  | cable_global | equipo_local | cable_local
            CREATE TABLE IF NOT EXISTS score_empresa_tipo (
                id      SERIAL PRIMARY KEY,
                nombre  TEXT        NOT NULL,
                tipo    VARCHAR(50) NOT NULL,
                puntos  INTEGER     NOT NULL,
                activa  BOOLEAN     NOT NULL DEFAULT TRUE,
                UNIQUE (nombre, tipo)
            );
            CREATE INDEX IF NOT EXISTS idx_set_tipo   ON score_empresa_tipo(tipo);
            CREATE INDEX IF NOT EXISTS idx_set_activa ON score_empresa_tipo(activa);

            -- —— PAISES ————————————————————————————————————————————————————————————————
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

            -- —— NOTICIAS ENVIADAS —————————————————————————————————————————————————————
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

            -- —— EJECUCION FUENTES —————————————————————————————————————————————————————
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

            -- —— ENVIOS LOG ———————————————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS envios_log (
                id              SERIAL PRIMARY KEY,
                fecha           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                total_noticias  INTEGER     NOT NULL,
                por_pais        JSONB       NOT NULL DEFAULT '{}',
                ok              BOOLEAN     NOT NULL DEFAULT TRUE
            );

            -- —— PROCESOS PROGRAMADOS ————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS procesos_programados (
                id                SERIAL PRIMARY KEY,
                nombre            VARCHAR(100) NOT NULL UNIQUE,
                ultima_ejecucion  TIMESTAMPTZ,
                ultimo_estado_ok  BOOLEAN,
                detalle           JSONB NOT NULL DEFAULT '{}',
                actualizado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- —— IA SCORING LOG ──────────────────────────────────────────────────────
            -- Registro de todas las noticias que llegaron a Claude por ejecución del RPA.
            -- numero_insercion es MAX+1 por día (empieza en 1 cada día).
            -- resultado JSONB: {
            --   fecha, total_noticias,
            --   "<Pais>": { total_noticias_pais, noticias:[{url,titulo,fuente,ia_score,criterios}] }
            -- }
            CREATE TABLE IF NOT EXISTS ia_scoring_log (
                id               SERIAL PRIMARY KEY,
                fecha            DATE        NOT NULL,
                numero_insercion INTEGER     NOT NULL,
                total_noticias   INTEGER     NOT NULL DEFAULT 0,
                resultado        JSONB       NOT NULL DEFAULT '{}',
                creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (fecha, numero_insercion)
            );
            CREATE INDEX IF NOT EXISTS idx_isl_fecha ON ia_scoring_log(fecha);

            -- —— SCRAPING LOG ────────────────────────────────────────────────────────
            -- Registro de todas las noticias encontradas por fuente/fecha/ejecución.
            -- numero_ejecucion empieza en 1 cada día y es incremental por fuente.
            -- resultado JSONB: {total, noticias:[{url,titulo}]} si ok=TRUE
            --                  {total:0, error:"..."} si ok=FALSE
            CREATE TABLE IF NOT EXISTS scraping_log (
                id               SERIAL PRIMARY KEY,
                url_fuente       TEXT        NOT NULL,
                fecha            DATE        NOT NULL,
                numero_ejecucion INTEGER     NOT NULL,
                ok               BOOLEAN     NOT NULL,
                total_noticias   INTEGER     NOT NULL DEFAULT 0,
                resultado        JSONB       NOT NULL DEFAULT '{}',
                creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (url_fuente, fecha, numero_ejecucion)
            );
            CREATE INDEX IF NOT EXISTS idx_sl_fuente_fecha ON scraping_log(url_fuente, fecha);
            CREATE INDEX IF NOT EXISTS idx_sl_fecha        ON scraping_log(fecha);

            -- —— ARTICULOS PENDIENTES ————————————————————————————————————————————————
            CREATE TABLE IF NOT EXISTS articulos_pendientes (
                id            SERIAL PRIMARY KEY,
                url_fuente    TEXT        NOT NULL,
                fecha_ef      DATE        NOT NULL,
                titulo        TEXT        NOT NULL,
                url           TEXT        NOT NULL,
                resumen       TEXT,
                fecha         TEXT,
                fuente        TEXT,
                pais          TEXT,
                ia_procesada  BOOLEAN     NOT NULL DEFAULT FALSE,
                creado_en     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (url, fecha_ef)
            );
            CREATE INDEX IF NOT EXISTS idx_ap_fuente_fecha ON articulos_pendientes(url_fuente, fecha_ef);
            CREATE INDEX IF NOT EXISTS idx_ap_pendientes  ON articulos_pendientes(url_fuente, fecha_ef, ia_procesada);
            ALTER TABLE articulos_pendientes ADD COLUMN IF NOT EXISTS ia_procesada BOOLEAN NOT NULL DEFAULT FALSE;

            -- —— SCORE SECTOR CONTEXTO ────────────────────────────────────────────
            -- Términos que confirman contexto minero-energético.
            -- Se usan como segundo requisito para activar score de "contrato"
            -- y el tag visual CONTRATO, evitando falsos positivos en noticias
            -- laborales, políticas o sociales que mencionen la palabra "contrato".
            CREATE TABLE IF NOT EXISTS score_sector_contexto (
                id      SERIAL PRIMARY KEY,
                termino TEXT    NOT NULL UNIQUE,
                activa  BOOLEAN NOT NULL DEFAULT TRUE
            );

            -- Credenciales y URLs de login
            ALTER TABLE fuentes ADD COLUMN IF NOT EXISTS usuario TEXT;
            ALTER TABLE fuentes ADD COLUMN IF NOT EXISTS clave TEXT;
            ALTER TABLE fuentes ADD COLUMN IF NOT EXISTS login_url TEXT;
            ALTER TABLE fuentes ADD COLUMN IF NOT EXISTS post_login_url TEXT;
            """
        )

        # ── Migración fuentes: elimina RSS, todas pasan a scraping ──────────────
        # Idempotente — solo actualiza fuentes que aún tienen url_rss y no son login.
        # El scraper usa scrape_selector si existe; si no, aplica selectores genéricos.
        with conn.cursor() as cur_rss:
            cur_rss.execute("""
                UPDATE fuentes
                SET url_rss = NULL,
                    metodo   = 'scrape'
                WHERE url_rss IS NOT NULL
                  AND (usuario IS NULL OR TRIM(usuario) = '')
                  AND (clave   IS NULL OR TRIM(clave)   = '')
            """)

        # ── Migración scrape_selector: fija selectores específicos por fuente ────
        # Idempotente — fuerza el selector correcto independientemente del valor previo.
        with conn.cursor() as cur_sel:
            cur_sel.execute("""
                UPDATE fuentes
                SET scrape_selector = 'div.bloque-caja > a[href], div.bloque-caja-b > a[href]'
                WHERE url = 'https://www.nuevamineria.com'
                  AND scrape_selector IS DISTINCT FROM 'div.bloque-caja > a[href], div.bloque-caja-b > a[href]'
            """)
            cur_sel.execute("""
                UPDATE fuentes
                SET scrape_selector = '.recently-updated a[href*=''pageId'']'
                WHERE url = 'https://www.portalminero.com'
                  AND scrape_selector IS DISTINCT FROM '.recently-updated a[href*=''pageId'']'
            """)

        # ── Migración de keywords: elimina genéricos, agrega frases específicas ─
        # Idempotente — corre en cada init_db sin romper instalaciones existentes.
        # "suministro", "award", "contract" solos disparaban CONTRATO en informes
        # de producción que mencionan suministro operacional ("suministro de energía").
        with conn.cursor() as cur_mig:
            cur_mig.execute("""
                DELETE FROM score_keywords
                WHERE keyword IN ('suministro', 'award', 'contract')
            """)
            cur_mig.execute("""
                INSERT INTO score_keywords (keyword) VALUES
                    ('contrato de suministro'),
                    ('licitación de suministro'),
                    ('licitacion de suministro'),
                    ('contract award'),
                    ('awarded contract'),
                    ('supply contract'),
                    ('planificación estratégica'),
                    ('planificacion estrategica'),
                    ('nuevo estudio'),
                    ('cero emisiones')
                ON CONFLICT (keyword) DO NOTHING
            """)

        # ── Migración keywords: separa 'contrato' de 'concepto' ─────────────────
        # Los keywords de concepto sectorial (hidrógeno, planificación, etc.) daban
        # 250 pts de contrato por error — ahora se clasifican como 'concepto' (80 pts).
        with conn.cursor() as cur_tipo:
            cur_tipo.execute("""
                UPDATE score_keywords
                SET tipo = 'concepto'
                WHERE keyword IN (
                    'planificación estratégica', 'planificacion estrategica',
                    'nuevo estudio',
                    'cero emisiones',
                    'hidrógeno sostenible',  'hidrogeno sostenible',
                    'hidrógeno',             'hidrogeno',
                    'hidrógeno verde',       'hidrogeno verde',
                    'almacenamiento de hidrógeno', 'almacenamiento de hidrogeno'
                )
            """)

        # ── Migración sector_contexto: agrega eléctrico e hidrógeno ─────────────
        with conn.cursor() as cur_ctx:
            cur_ctx.execute("""
                INSERT INTO score_sector_contexto (termino) VALUES
                    ('eléctric'),
                    ('hidrógeno'),
                    ('hidrogeno'),
                    ('hydrogen')
                ON CONFLICT (termino) DO NOTHING
            """)

        # ── Migración keywords: agrega frases de hidrógeno ───────────────────────
        with conn.cursor() as cur_h2:
            cur_h2.execute("""
                INSERT INTO score_keywords (keyword) VALUES
                    ('hidrógeno sostenible'),
                    ('hidrogeno sostenible'),
                    ('hidrógeno'),
                    ('hidrogeno'),
                    ('hidrógeno verde'),
                    ('hidrogeno verde'),
                    ('almacenamiento de hidrógeno'),
                    ('almacenamiento de hidrogeno')
                ON CONFLICT (keyword) DO NOTHING
            """)

        # ── Migración: penalización por entrevista ────────────────────────────────
        with conn.cursor() as cur_ent:
            cur_ent.execute("""
                INSERT INTO score_reglas (codigo, descripcion, puntos)
                VALUES ('entrevista_penalizacion', 'Penalización: la noticia es una entrevista', -40)
                ON CONFLICT (codigo) DO NOTHING
            """)

        # ── Migración: regla inversión ────────────────────────────────────────────
        with conn.cursor() as cur_inv:
            cur_inv.execute("""
                INSERT INTO score_reglas (codigo, descripcion, puntos)
                VALUES ('inversion', 'Noticia de inversión en el sector minero-energético', 100)
                ON CONFLICT (codigo) DO NOTHING
            """)

        # ── Migración: encuentro de proveedores ──────────────────────────────────
        with conn.cursor() as cur_ep:
            cur_ep.execute("""
                INSERT INTO score_reglas (codigo, descripcion, puntos)
                VALUES ('encuentro_proveedores', 'noticia sobre financiamiento directo a través de concursos', 110)
                ON CONFLICT (codigo) DO NOTHING
            """)

        # ── Migración: Casposo en empresas conocidas ──────────────────────────────
        with conn.cursor() as cur_casp_ec:
            cur_casp_ec.execute("""
                INSERT INTO score_empresas_conocidas (nombre)
                VALUES ('Casposo')
                ON CONFLICT (nombre) DO NOTHING
            """)

        # ── Migración: Casposo en score_empresa_tipo ──────────────────────────────
        with conn.cursor() as cur_casp_et:
            cur_casp_et.execute("""
                INSERT INTO score_empresa_tipo (nombre, tipo, puntos)
                VALUES ('Casposo', 'empresa_minera', 200)
                ON CONFLICT (nombre, tipo) DO NOTHING
            """)

        with conn.cursor() as cur_ent_kw:
            cur_ent_kw.execute("""
                INSERT INTO score_keywords (keyword, tipo) VALUES
                    ('entrevista',          'entrevista'),
                    ('conversamos con',     'entrevista'),
                    ('conversacion con',    'entrevista'),
                    ('conversación con',    'entrevista'),
                    ('hablamos con',        'entrevista'),
                    ('hablamos con',        'entrevista')
                ON CONFLICT (keyword) DO NOTHING
            """)

        # ── Migración: Distrito Vicuña — empresas y proyectos (190 pts) ──────────
        # BHP ya existe en el seed como empresa_minera con 200 pts — no se modifica.
        # Se agregan: Lundin Mining, Vicuña Corp., Josemaría, Filo del Sol.
        _vicuna_entities = [
            "Lundin Mining",
            "Vicuña Corp.",
            "Vicuña Corp",
            "Josemaría",
            "Josemaria",
            "Filo del Sol",
            "Distrito Vicuña",
            "Distrito Vicuna",
        ]
        with conn.cursor() as cur_vic_ec:
            for nombre in _vicuna_entities:
                cur_vic_ec.execute(
                    "INSERT INTO score_empresas_conocidas (nombre) VALUES (%s) ON CONFLICT (nombre) DO NOTHING",
                    (nombre,),
                )

        with conn.cursor() as cur_vic_et:
            for nombre in _vicuna_entities:
                cur_vic_et.execute(
                    """
                    INSERT INTO score_empresa_tipo (nombre, tipo, puntos)
                    VALUES (%s, 'empresa_minera', 190)
                    ON CONFLICT (nombre, tipo) DO NOTHING
                    """,
                    (nombre,),
                )
        logger.info("Migración Distrito Vicuña: %d entidades registradas (190 pts)", len(_vicuna_entities))

        # ── Migración: fuente BNamericas App (app.bnamericas.com) ─────────────────
        # La URL del filtro NO se persiste en DB — se lee desde BNAMERICAS_APP_FILTER_PATH en .env.
        # Las credenciales (usuario/clave) deben setearse manualmente en la tabla fuentes.
        with conn.cursor() as cur_bna_app:
            cur_bna_app.execute("""
                INSERT INTO fuentes (nombre, url, pais, metodo, scrape_selector, login_url, nota)
                VALUES (
                    'BNamericas App',
                    'https://app.bnamericas.com/',
                    'Internacional',
                    'scrape',
                    'a[href*=''/article/content/'']',
                    'https://app.bnamericas.com/login',
                    'Login requerido. URL de filtro configurable en BNAMERICAS_APP_FILTER_PATH (.env)'
                )
                ON CONFLICT (url) DO UPDATE
                    SET login_url       = EXCLUDED.login_url,
                        scrape_selector = EXCLUDED.scrape_selector,
                        nota            = EXCLUDED.nota
                    WHERE fuentes.login_url IS DISTINCT FROM EXCLUDED.login_url
                       OR fuentes.scrape_selector IS DISTINCT FROM EXCLUDED.scrape_selector
            """)
        logger.info("Migración BNamericas App: fuente app.bnamericas.com registrada/actualizada")

    db_config = get_db_config()
    logger.info(
        "PostgreSQL schema OK — %s:%s/%s",
        db_config["host"], db_config["port"], db_config["dbname"],
    )
    seed_if_empty()



def seed_if_empty() -> None:
    """Inserta los datos iniciales si las tablas de configuración están vacías."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM paises")
            if cur.fetchone()[0] == 0:
                paises = [
                    ("Chile", "Chile", "CHL", "🇨🇱", 10, 1),
                    ("Peru", "Peru", "PER", "🇵🇪", 10, 2),
                    ("Argentina", "Argentina", "ARG", "🇦🇷", 10, 3),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO paises (nombre, nombre_en, codigo_iso, bandera, cuota, orden)
                    VALUES %s
                    """,
                    paises,
                )
                logger.info("Seed: %d países insertados", len(paises))

            cur.execute("SELECT COUNT(*) FROM fuentes")
            if cur.fetchone()[0] == 0:
                # Todas las fuentes usan scraping (url_rss = None).
                # Si tiene scrape_selector se usa ese; si no, aplican selectores genéricos.
                fuentes = [
                    ("Nueva Minería y Energía",       "https://www.nuevamineria.com",               None, "Chile",         "scrape", "div.bloque-caja > a[href], div.bloque-caja-b > a[href]", None),
                    ("H2 News",                        "https://www.h2news.cl",                      None, "Chile",         "scrape", None,                  None),
                    ("Revista Digital Minera",         "https://www.redimin.cl",                     None, "Chile",         "scrape", None,                  None),
                    ("Reporte Minero y Energético",    "https://www.reporteminero.cl",               None, "Chile",         "scrape", None,                  None),
                    ("Minería Chilena (MCH)",           "https://www.mch.cl",                         None, "Chile",         "scrape", None,                  None),
                    ("ACERA",                          "https://www.acera.cl",                       None, "Chile",         "scrape", None,                  None),
                    ("El Pingüino",                    "https://elpinguino.com",                     None, "Chile",         "scrape", None,                  None),
                    ("La Prensa Austral",              "https://laprensaaustral.cl",                 None, "Chile",         "scrape", None,                  None),
                    ("Portal Minero",                  "https://www.portalminero.com",               None, "Chile",         "scrape", '.recently-updated a[href*="pageId"]', None),
                    ("SEA",                            "https://www.sea.gob.cl",                     None, "Chile",         "scrape", ".noticias a",          None),
                    ("ACADES",                         "https://www.acades.cl",                      None, "Chile",         "scrape", ".entry-title a",       None),
                    ("Rubro Minero",                   "https://www.rumbominero.com",                None, "Peru",          "scrape", None,                  None),
                    ("Minería & Energía Perú",         "https://mineriaenergia.com",                 None, "Peru",          "scrape", None,                  None),
                    ("Minería Hoy",                    "https://www.mineriahoy.com",                 None, "Peru",          "scrape", None,                  None),
                    ("Andina",                         "https://andina.pe",                          None, "Peru",          "scrape", None,                  None),
                    ("Diario Minero",                  "https://www.diariominero.com",               None, "Peru",          "scrape", "h2.entry-title a",    None),
                    ("Noticias de Minería Argentina",  "https://noticiasdemineria.com.ar",           None, "Argentina",     "scrape", None,                  None),
                    ("Minería y Desarrollo",           "https://www.mineriaydesarrollo.com",         None, "Argentina",     "scrape", None,                  None),
                    ("Panorama Minero",                "https://www.panorama-minero.com",            None, "Argentina",     "scrape", "h2.entry-title a",    None),
                    ("Argentina.gob.ar Energía",       "https://www.argentina.gob.ar/economia/energia", None, "Argentina",  "scrape", ".news-item a",        None),
                    ("Energías Renovables",            "https://www.energias-renovables.com",        None, "Internacional", "scrape", None,                  None),
                    ("Periódico de la Energía",        "https://elperiodicodelaenergia.com",         None, "Internacional", "scrape", None,                  None),
                    ("Mining Digital",                 "https://miningdigital.com",                  None, "Internacional", "scrape", None,                  None),
                    ("Mining.com",                     "https://www.mining.com",                     None, "Internacional", "scrape", None,                  None),
                    ("Mining Weekly",                  "https://www.miningweekly.com",               None, "Internacional", "scrape", None,                  None),
                    ("BNamericas",                     "https://www.bnamericas.com",                 None, "Internacional", "scrape", "h3.article-title a",  "Paywall parcial"),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO fuentes (nombre, url, url_rss, pais, metodo, scrape_selector, nota)
                    VALUES %s
                    """,
                    fuentes,
                )
                logger.info("Seed: %d fuentes insertadas", len(fuentes))

            cur.execute("SELECT COUNT(*) FROM score_reglas")
            if cur.fetchone()[0] == 0:
                reglas = [
                    ("contrato", "Noticia de contrato / licitación / adjudicación", 250),
                    ("empresa_conocida", "Contrato que involucra empresa conocida del sector", 150),
                    ("empresa_noticia", "Noticia relevante de empresa importante (sin contrato)", 80),
                    ("concepto_sectorial", "Estudio, planificación estratégica o meta de cero emisiones del sector", 80),
                    ("reciente_3dias", "Noticia publicada hace 3 días o menos", 60),
                    ("reciente_hoy", "Noticia publicada hoy", 25),
                    ("inversion", "Noticia de inversión en el sector minero-energético", 100),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO score_reglas (codigo, descripcion, puntos)
                    VALUES %s
                    """,
                    reglas,
                )
                logger.info("Seed: %d reglas de scoring insertadas", len(reglas))

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
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO score_empresas (nombre) VALUES %s",
                    empresas,
                )
                logger.info("Seed: %d empresas insertadas", len(empresas))

            cur.execute("SELECT COUNT(*) FROM score_keywords")
            if cur.fetchone()[0] == 0:
                keywords = [
                    # tipo='contrato' → +250 pts en pre-scoring
                    ("contrato",            "contrato"),
                    ("licitación",          "contrato"),
                    ("licitacion",          "contrato"),
                    ("adjudicación",        "contrato"),
                    ("adjudicacion",        "contrato"),
                    ("concesión",           "contrato"),
                    ("concesion",           "contrato"),
                    ("ganó contrato",       "contrato"),
                    ("gano contrato",       "contrato"),
                    ("contrato de suministro",   "contrato"),
                    ("licitación de suministro", "contrato"),
                    ("licitacion de suministro", "contrato"),
                    ("EPC",                 "contrato"),
                    ("EPCM",                "contrato"),
                    ("tender",              "contrato"),
                    ("bid",                 "contrato"),
                    ("contract award",      "contrato"),
                    ("awarded contract",    "contrato"),
                    ("supply contract",     "contrato"),
                    # tipo='concepto' → +80 pts en pre-scoring (concepto_sectorial)
                    ("planificación estratégica",       "concepto"),
                    ("planificacion estrategica",       "concepto"),
                    ("nuevo estudio",                   "concepto"),
                    ("cero emisiones",                  "concepto"),
                    ("hidrógeno sostenible",            "concepto"),
                    ("hidrogeno sostenible",            "concepto"),
                    ("hidrógeno",                       "concepto"),
                    ("hidrogeno",                       "concepto"),
                    ("hidrógeno verde",                 "concepto"),
                    ("hidrogeno verde",                 "concepto"),
                    ("almacenamiento de hidrógeno",     "concepto"),
                    ("almacenamiento de hidrogeno",     "concepto"),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO score_keywords (keyword, tipo) VALUES %s",
                    keywords,
                )
                logger.info("Seed: %d keywords insertados", len(keywords))

            cur.execute("SELECT COUNT(*) FROM score_empresas_conocidas")
            if cur.fetchone()[0] == 0:
                empresas_conocidas = [
                    ("Bechtel",), ("Fluor",), ("Worley",), ("Hatch",), ("Jacobs",),
                    ("Ausenco",), ("Techint",), ("Besalco",), ("Wood",), ("Tecnimont",),
                    ("SNC-Lavalin",), ("Aecom",), ("Amec Foster Wheeler",), ("Mott MacDonald",),
                    ("Caterpillar",), ("Komatsu",), ("Hitachi",), ("Epiroc",), ("Sandvik",),
                    ("Liebherr",), ("Terex",), ("XCMG",), ("FLSmidth",), ("ABB",),
                    ("Siemens",), ("Schneider Electric",), ("GE Grid Solutions",),
                    ("Mitsubishi Electric",), ("Toshiba Energy",), ("WEG",), ("Vestas",),
                    ("Acciona",), ("Goldwind",), ("Nordex",), ("Nexans",), ("Prysmian",),
                    ("Codelco",), ("BHP",), ("Anglo American",), ("Glencore",),
                    ("Antofagasta Minerals",), ("Teck",), ("Freeport-McMoRan",),
                    ("Casposo",),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO score_empresas_conocidas (nombre) VALUES %s",
                    empresas_conocidas,
                )
                logger.info("Seed: %d empresas conocidas insertadas", len(empresas_conocidas))

            cur.execute("SELECT COUNT(*) FROM score_empresa_tipo")
            if cur.fetchone()[0] == 0:
                empresa_tipo_rows = [
                    # ── Mineras (+200) ────────────────────────────────────────────────────
                    ("Casposo",                    "empresa_minera",  200),
                    ("Codelco",                    "empresa_minera",  200),
                    ("BHP",                        "empresa_minera",  200),
                    ("Anglo American",             "empresa_minera",  200),
                    ("Glencore",                   "empresa_minera",  200),
                    ("Antofagasta Minerals",       "empresa_minera",  200),
                    ("Southern Copper",            "empresa_minera",  200),
                    ("Freeport-McMoRan",           "empresa_minera",  200),
                    ("MMG Limited",                "empresa_minera",  200),
                    ("Newmont",                    "empresa_minera",  200),
                    ("Antamina",                   "empresa_minera",  200),
                    ("Livent",                     "empresa_minera",  200),
                    ("Allkem",                     "empresa_minera",  200),
                    ("Barrick Gold",               "empresa_minera",  200),
                    ("Pan American Silver",        "empresa_minera",  200),
                    # ── EPC / EPCM (+180) ─────────────────────────────────────────────────
                    ("Bechtel",                    "empresa_epc",     180),
                    ("Fluor",                      "empresa_epc",     180),
                    ("Worley",                     "empresa_epc",     180),
                    ("Wood Group",                 "empresa_epc",     180),
                    ("Jacobs Engineering",         "empresa_epc",     180),
                    ("Hatch",                      "empresa_epc",     180),
                    ("SK ecoplant",                "empresa_epc",     180),
                    ("Techint",                    "empresa_epc",     180),
                    ("PowerChina",                 "empresa_epc",     180),
                    ("China Railway Construction", "empresa_epc",     180),
                    ("SNC-Lavalin",                "empresa_epc",     180),
                    ("Aecom",                      "empresa_epc",     180),
                    ("Ausenco",                    "empresa_epc",     180),
                    ("Besalco",                    "empresa_epc",     180),
                    ("Tecnimont",                  "empresa_epc",     180),
                    # ── Energía (+160) ────────────────────────────────────────────────────
                    ("Enel",                       "empresa_energia", 160),
                    ("Colbún",                     "empresa_energia", 160),
                    ("AES Andes",                  "empresa_energia", 160),
                    ("Engie",                      "empresa_energia", 160),
                    ("Innergex",                   "empresa_energia", 160),
                    ("Generadora Metropolitana",   "empresa_energia", 160),
                    ("Kallpa Generación",          "empresa_energia", 160),
                    ("Statkraft",                  "empresa_energia", 160),
                    ("Electroperú",                "empresa_energia", 160),
                    ("Celepsa",                    "empresa_energia", 160),
                    ("YPF",                        "empresa_energia", 160),
                    ("Pampa Energía",              "empresa_energia", 160),
                    ("Central Puerto",             "empresa_energia", 160),
                    ("Genneia",                    "empresa_energia", 160),
                    ("Pan American Energy",        "empresa_energia", 160),
                    ("Edenor",                     "empresa_energia", 160),
                    # ── Equipo global (+150) ──────────────────────────────────────────────
                    ("Epiroc",                     "equipo_global",   150),
                    ("Sandvik",                    "equipo_global",   150),
                    ("Boart Longyear",             "equipo_global",   150),
                    ("Furukawa Rock Drill",        "equipo_global",   150),
                    ("Atlas Copco",                "equipo_global",   150),
                    ("Caterpillar",                "equipo_global",   150),
                    ("Komatsu",                    "equipo_global",   150),
                    ("Liebherr",                   "equipo_global",   150),
                    ("Hitachi Construction",       "equipo_global",   150),
                    ("Doosan",                     "equipo_global",   150),
                    ("Metso",                      "equipo_global",   150),
                    ("FLSmidth",                   "equipo_global",   150),
                    ("Weir Group",                 "equipo_global",   150),
                    ("Outotec",                    "equipo_global",   150),
                    ("Thyssenkrupp",               "equipo_global",   150),
                    # ── Cable global (+140) ───────────────────────────────────────────────
                    ("Prysmian",                   "cable_global",    140),
                    ("Nexans",                     "cable_global",    140),
                    ("Southwire",                  "cable_global",    140),
                    ("General Cable",              "cable_global",    140),
                    ("LS Cable",                   "cable_global",    140),
                    ("Sumitomo Electric",          "cable_global",    140),
                    ("Furukawa Electric",          "cable_global",    140),
                    ("NKT",                        "cable_global",    140),
                    # ── Equipo local (+120) ───────────────────────────────────────────────
                    ("Finning",                    "equipo_local",    120),
                    ("Komatsu Cummins",            "equipo_local",    120),
                    ("Sigdo Koppers",              "equipo_local",    120),
                    ("SalfaCorp",                  "equipo_local",    120),
                    ("Ferreycorp",                 "equipo_local",    120),
                    ("Komatsu Mitsui",             "equipo_local",    120),
                    ("Unimaq",                     "equipo_local",    120),
                    ("SK Rental",                  "equipo_local",    120),
                    ("Hidromec",                   "equipo_local",    120),
                    # ── Cable local (+110) ────────────────────────────────────────────────
                    ("Madeco",                     "cable_local",     110),
                    ("Covisa",                     "cable_local",     110),
                    ("Condumex",                   "cable_local",     110),
                    ("Indeco",                     "cable_local",     110),
                    ("IMSA",                       "cable_local",     110),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO score_empresa_tipo (nombre, tipo, puntos)
                    VALUES %s ON CONFLICT (nombre, tipo) DO NOTHING
                    """,
                    empresa_tipo_rows,
                )
                logger.info("Seed: %d entradas en score_empresa_tipo insertadas", len(empresa_tipo_rows))

            cur.execute("SELECT COUNT(*) FROM score_sector_contexto")
            if cur.fetchone()[0] == 0:
                terminos = [
                    # Industria minera
                    ("miner",), ("litio",), ("cobre",), ("zinc",), ("molibden",),
                    ("niquel",), ("níquel",), ("yacimiento",), ("faena",), ("concentrador",),
                    # Industria energética
                    ("energ",), ("petról",), ("hidrocarbur",), ("gnl",), ("glp",),
                    ("fotovolt",), ("eólic",), ("geotérm",), ("termoeléctric",),
                    ("transmisi",), ("subestaci",),
                    # Sector eléctrico e hidrógeno
                    ("eléctric",),
                    ("hidrógeno",), ("hidrogeno",), ("hydrogen",),
                    # Contratos sector (ya son específicos por contexto)
                    ("epc",), ("epcm",), ("capex",),
                    ("licitac",), ("adjudic",),
                ]
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO score_sector_contexto (termino) VALUES %s",
                    terminos,
                )
                logger.info("Seed: %d términos de contexto sectorial insertados", len(terminos))

    logger.info("Seed completado.")
