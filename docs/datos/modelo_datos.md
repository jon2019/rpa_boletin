# Modelo de datos — RPA Boletín Minero-Energético

> **Estado**: verificado contra `src/boletin/infrastructure/db/schema_repository.py`
>
> Este documento refleja el schema que hoy crea `init_db()` y el uso real observado en `sources_repository.py`, `execution_repository.py`, `pending_articles_repository.py` y `sent_news_repository.py`.

---

## Tablas de configuración

### `fuentes`

Catálogo central de fuentes de noticias.

| Campo | Tipo | Restricción | Nullable | Default | Uso real |
|------|------|-------------|----------|---------|----------|
| id | SERIAL | PK | No | AUTO | Identificador interno. |
| nombre | TEXT | — | No | — | Nombre visible. |
| url | TEXT | UNIQUE | No | — | Clave lógica operativa. |
| url_rss | TEXT | — | Sí | NULL | Feed RSS cuando aplica. |
| pais | VARCHAR(50) | — | No | — | País lógico de la fuente. |
| metodo | VARCHAR(10) | CHECK (`rss`,`scrape`) o NULL | Sí | NULL | Campo heredado; hoy el flujo operativo también usa presencia de RSS, selector o credenciales. |
| scrape_selector | TEXT | — | Sí | NULL | Selector CSS para scraping. |
| nota | TEXT | — | Sí | NULL | Observación libre. |
| activa | BOOLEAN | — | No | TRUE | Activa/desactiva la fuente. |
| creado_en | TIMESTAMPTZ | — | No | NOW() | Timestamp de creación. |
| actualizado_en | TIMESTAMPTZ | — | No | NOW() | Timestamp de última actualización. |
| usuario | TEXT | — | Sí | NULL | Usuario para fuentes con login. |
| clave | TEXT | — | Sí | NULL | Clave para fuentes con login. |
| login_url | TEXT | — | Sí | NULL | URL de autenticación. |
| post_login_url | TEXT | — | Sí | NULL | URL esperada tras login exitoso. |

**Índices**: `idx_fuentes_activa`, `idx_fuentes_pais`

### `paises`

Define el universo editorial activo y sus cuotas.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| nombre | VARCHAR(100) | UNIQUE | No | — |
| nombre_en | VARCHAR(100) | — | No | — |
| codigo_iso | VARCHAR(3) | — | No | — |
| bandera | VARCHAR(10) | — | No | — |
| cuota | INTEGER | `CHECK (cuota > 0)` | No | 10 |
| orden | INTEGER | — | No | 99 |
| activo | BOOLEAN | — | No | TRUE |

### `score_reglas`

Pesos configurables del scoring.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| codigo | VARCHAR(50) | UNIQUE | No | — |
| descripcion | TEXT | — | No | — |
| puntos | INTEGER | — | No | — |
| activa | BOOLEAN | — | No | TRUE |

### `score_empresas`

Empresas que suman puntaje por mención relevante.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| nombre | TEXT | UNIQUE | No | — |
| activa | BOOLEAN | — | No | TRUE |

### `score_empresas_conocidas`

Empresas que suman puntaje adicional cuando aparecen en contexto contractual.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| nombre | TEXT | UNIQUE | No | — |
| activa | BOOLEAN | — | No | TRUE |

### `score_keywords`

Keywords de contratos, adjudicaciones y licitaciones.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| keyword | TEXT | UNIQUE | No | — |
| activa | BOOLEAN | — | No | TRUE |

## Tablas operativas

### `ejecucion_fuentes`

Checkpoint por fuente y fecha efectiva.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| url_fuente | TEXT | UNIQUE compuesta con `fecha_ejecucion` | No | — |
| nombre_fuente | TEXT | — | No | — |
| fecha_ejecucion | DATE | UNIQUE compuesta con `url_fuente` | No | — |
| scraping_ok | BOOLEAN | — | No | FALSE |
| ia_ok | BOOLEAN | — | No | FALSE |
| noticias_obtenidas | INTEGER | — | No | 0 |
| noticias_enviadas | INTEGER | — | No | 0 |
| error_detalle | TEXT | — | Sí | NULL |
| creado_en | TIMESTAMPTZ | — | No | NOW() |
| actualizado_en | TIMESTAMPTZ | — | No | NOW() |

### `articulos_pendientes`

Staging para reintentos solo-IA.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| url_fuente | TEXT | — | No | — |
| fecha_ef | DATE | UNIQUE compuesta con `url` | No | — |
| titulo | TEXT | — | No | — |
| url | TEXT | UNIQUE compuesta con `fecha_ef` | No | — |
| resumen | TEXT | — | Sí | NULL |
| fecha | TEXT | — | Sí | NULL |
| fuente | TEXT | — | Sí | NULL |
| pais | TEXT | — | Sí | NULL |
| ia_procesada | BOOLEAN | — | No | FALSE |
| creado_en | TIMESTAMPTZ | — | No | NOW() |

**Índices**: `idx_ap_fuente_fecha`, `idx_ap_pendientes`

### `noticias_enviadas`

Historial permanente de deduplicación.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| url_hash | VARCHAR(64) | UNIQUE | No | — |
| titulo | TEXT | — | No | — |
| fuente | TEXT | — | No | — |
| pais | VARCHAR(50) | — | No | — |
| url | TEXT | — | No | — |
| enviado_en | TIMESTAMPTZ | — | No | NOW() |

### `envios_log`

Registro de cada intento de envío del boletín.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| fecha | TIMESTAMPTZ | — | No | NOW() |
| total_noticias | INTEGER | — | No | — |
| por_pais | JSONB | — | No | '{}' |
| ok | BOOLEAN | — | No | TRUE |

### `procesos_programados`

Persistencia de procesos auxiliares con frecuencia propia.

| Campo | Tipo | Restricción | Nullable | Default |
|------|------|-------------|----------|---------|
| id | SERIAL | PK | No | AUTO |
| nombre | VARCHAR(100) | UNIQUE | No | — |
| ultima_ejecucion | TIMESTAMPTZ | — | Sí | NULL |
| ultimo_estado_ok | BOOLEAN | — | Sí | NULL |
| detalle | JSONB | — | No | '{}' |
| actualizado_en | TIMESTAMPTZ | — | No | NOW() |

## Relaciones lógicas

- `ejecucion_fuentes.url_fuente` -> `fuentes.url`
- `articulos_pendientes.url_fuente` -> `fuentes.url`
- `fuentes.pais` se cruza lógicamente con `paises.nombre`
- `score_reglas.codigo` es consumido por `scorer.py`

## Reglas operativas asociadas al modelo

```sql
-- una fuente está completa solo si
scraping_ok = TRUE AND ia_ok = TRUE

-- solo-IA se activa cuando
scraping_ok = TRUE AND ia_ok = FALSE

-- solo se recuperan pendientes seguros
ia_procesada = FALSE

-- nunca se reenvía una URL ya registrada
SHA256(url) NOT IN noticias_enviadas.url_hash
```

## Notas de validación

1. El schema vigente **sí** incluye credenciales y URLs de login en `fuentes`.
2. El schema vigente **sí** incluye `procesos_programados`.
3. No hay FK físicas entre tablas operativas y `fuentes`; las relaciones son lógicas.
4. Los artefactos SQL/AQL/MMD generados con el modelo anterior fueron movidos a `docs/legacy/datos/`.
