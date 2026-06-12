# Índice de documentación — RPA Boletín Minero-Energético

Esta carpeta concentra la documentación vigente del proyecto y separa explícitamente lo operativo, lo funcional, el modelo de datos y el material legado que quedó desactualizado.

## Estructura vigente

### Operación

| Archivo | Propósito |
|---------|-----------|
| `operacion/README_sistema.md` | Vista operativa del pipeline, fecha efectiva, artefactos, logs y variables de entorno. |

### Arquitectura

| Archivo | Propósito |
|---------|-----------|
| `arquitectura/estructura_actual_boletin.md` | Árbol resumido de `src/boletin` y criterio actual de organización por capas. |
| `arquitectura/checklist_cierre_arquitectonico.md` | Checklist para declarar cerrada la reestructuración arquitectónica. |
| `arquitectura/decisiones_capas_y_facade_db.md` | Decisión explícita sobre `infrastructure/db/facade.py` y reglas de dependencia entre capas. |

### Negocio

| Archivo | Propósito |
|---------|-----------|
| `negocio/reglas_negocio.md` | Reglas funcionales y operativas verificadas contra el código y la base de datos. |
| `negocio/Reglas_de_Negocio.docx` | Documento DOCX de referencia funcional vigente. |

### Datos / Base de datos

| Archivo | Propósito |
|---------|-----------|
| `datos/modelo_datos.md` | Diccionario de datos alineado al schema real que crea `schema_repository.py`. |
| `datos/modelo_relacional.md` | Vista resumida del modelo relacional actual y sus relaciones lógicas. |

### Legado

| Carpeta | Contenido |
|---------|-----------|
| `legacy/duplicados/` | Copias exactas `*.updated.*` que no agregaban valor. |
| `legacy/datos/` | Artefactos generados con modelos viejos o incompletos. |
| `legacy/ejecutiva/` | Entregables docx/xlsx históricos o regenerados para referencia. |
| `legacy/operacion/` | README/requirements/instrucciones heredadas de la estructura previa del proyecto. |

## Orden recomendado de lectura

### Para entender el sistema rápido
1. `operacion/README_sistema.md`
2. `arquitectura/estructura_actual_boletin.md`
3. `arquitectura/checklist_cierre_arquitectonico.md`
4. `arquitectura/decisiones_capas_y_facade_db.md`
5. `negocio/reglas_negocio.md`
6. `datos/modelo_relacional.md`

### Para revisar lógica funcional
1. `negocio/reglas_negocio.md`
2. `operacion/README_sistema.md`

### Para revisar esquema y persistencia
1. `datos/modelo_datos.md`
2. `datos/modelo_relacional.md`

## Criterio de validación aplicado

Los documentos movidos a secciones vigentes fueron contrastados contra:

- `src/boletin/main.py`
- `src/boletin/services/pipeline_service.py`
- `src/boletin/services/scoring_service.py`
- `src/boletin/scraping/orchestrator.py`
- `src/boletin/infrastructure/notifications/emailer.py`
- `src/boletin/infrastructure/db/facade.py`
- `src/boletin/infrastructure/db/*.py`

Si un archivo no reflejaba el estado real del proyecto, se movió o se copió a `docs/legacy/` en lugar de dejarlo como referencia vigente.
