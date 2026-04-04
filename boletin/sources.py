"""
sources.py
----------
ARCHIVO OBSOLETO — solo se mantiene como referencia histórica.

Las fuentes, empresas, keywords y reglas de scoring ahora viven
en la base de datos PostgreSQL en las siguientes tablas:

    fuentes         → reemplaza la lista SOURCES
    score_reglas    → reemplaza los pesos hardcodeados (+250, +150, etc.)
    score_empresas  → reemplaza HIGH_VALUE_COMPANIES
    score_keywords  → reemplaza CONTRACT_KEYWORDS

Para gestionar la configuración usa SQL directamente o cualquier
cliente PostgreSQL (DBeaver, pgAdmin, psql, TablePlus):

    -- Desactivar una fuente sin borrarla
    UPDATE fuentes SET activa = FALSE WHERE url = 'https://www.bnamericas.com';

    -- Agregar una fuente nueva
    INSERT INTO fuentes (nombre, url, url_rss, pais, metodo)
    VALUES ('Mi Fuente', 'https://mifuente.com', 'https://mifuente.com/feed', 'Chile', 'rss');

    -- Cambiar el puntaje de contratos
    UPDATE score_reglas SET puntos = 300 WHERE codigo = 'contrato';

    -- Agregar una empresa al scoring
    INSERT INTO score_empresas (nombre) VALUES ('Rio Tinto');

    -- Agregar un keyword de contrato
    INSERT INTO score_keywords (keyword) VALUES ('concesion minera');

    -- Ver estado actual
    SELECT * FROM fuentes ORDER BY pais, nombre;
    SELECT * FROM score_reglas ORDER BY puntos DESC;
    SELECT * FROM score_empresas ORDER BY nombre;
    SELECT * FROM score_keywords ORDER BY keyword;

Los datos iniciales se insertan automáticamente en la primera
ejecución via db._seed_if_empty().
"""
