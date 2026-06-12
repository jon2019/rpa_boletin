# Operación del sistema — RPA Boletín Minero-Energético

Este documento describe la operación real del sistema según el código vigente en `src/boletin/`.

## Estructura operativa vigente

- **Entrada principal**: `src/boletin/main.py`
- **Orquestación del pipeline**: `src/boletin/services/pipeline_service.py`
- **Configuración de paths base**: `src/boletin/config/settings.py`
- **Fachada de base de datos**: `src/boletin/db.py`
- **Repositorios DB reales**: `src/boletin/infrastructure/db/*.py`
- **Scraping / RSS**: `src/boletin/scraper.py`
- **Scoring**: `src/boletin/scorer.py`
- **Traducción**: `src/boletin/translator.py`
- **Email / preview**: `src/boletin/emailer.py`
- **Reintentos y colector de errores**: `src/boletin/retrier.py`

## Flujo operativo

1. `boletin.main` carga `.env` desde la raíz del proyecto.
2. Se configura logging diario en `logs/boletin_YYYY-MM-DD.log`.
3. Se toma un lock de proceso único usando `.lock` en la raíz del proyecto.
4. `pipeline_service.ejecutar_pipeline()`:
   - detecta fuentes con problemas operativos
   - calcula la `fecha_efectiva`
   - obtiene fuentes activas y pendientes
   - ejecuta scraping / recuperación de artículos pendientes
   - filtra noticias ya enviadas
   - puntúa con scoring local + IA
   - traduce al inglés
   - genera preview o envía correo
   - registra ejecución y errores acumulados

## Fecha efectiva

La fecha efectiva NO es siempre la fecha actual.

Regla vigente en `pipeline_service.calcular_fecha_efectiva`:

- **Martes** → usa martes actual
- **Jueves** → usa jueves actual
- **Miércoles** → compensa el martes anterior inmediato
- **Lunes, viernes, sábado y domingo** → compensa el jueves anterior

Si la base indica que esa fecha ya fue procesada completamente, el pipeline no se ejecuta.

## Clasificación operativa de fuentes

Las fuentes activas se separan en:

- **Login**: tienen `usuario` y `clave`
- **RSS**: no usan login y disponen de `rss`
- **Scrape**: no usan login, no tienen RSS, pero sí `scrape_selector`
- **Omitidas por configuración**: no cumplen ninguna regla operativa válida

Las omitidas quedan registradas como problemas operativos y pueden disparar alertas.

## Artefactos y salidas

### Logs

- `logs/boletin_YYYY-MM-DD.log`

Solo ese archivo debe vivir en `logs/`. Otros artefactos de debugging o prueba deben ir a `tests/output/`.

### Preview HTML

En modo preview, el archivo se genera en:

- `tests/output/previews/preview.html`

### Exportables documentales

Los scripts de exportación ya NO usan `doc/`.

- DOCX consolidado: `docs/legacy/ejecutiva/documentacion_completa.docx`
- Excel técnico: `docs/legacy/ejecutiva/diccionario_boletin.xlsx`

## Base de datos involucrada en operación

Tablas relevantes para operación diaria:

- `fuentes`
- `paises`
- `score_reglas`
- `score_empresas`
- `score_empresas_conocidas`
- `score_keywords`
- `ejecucion_fuentes`
- `articulos_pendientes`
- `noticias_enviadas`
- `envios_log`
- `procesos_programados`

## Comandos operativos recomendados

### Ejecución manual

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m boletin.main --run-now
```

### Preview

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m boletin.main --preview
```

### Scheduler

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m boletin.main
```

### Batch Windows

También existe:

- `ejecutar_boletin.bat`

que activa `.venv`, setea `PYTHONPATH` y ejecuta `python -m boletin.main --run-now`.

## Variables sensibles de entorno

El sistema depende, entre otras, de estas variables:

- `ANTHROPIC_API_KEY`
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`
- `FROM_EMAIL`, `TO_EMAILS`, `TO_EMAILS_ERRORES`
- `TIMEZONE`
- `HORA_ENVIO`
- `REINTENTOS_MAX`
- `REINTENTOS_BACKOFF_BASE`

## Archivos que NO son documentación operativa vigente

Estos archivos pertenecen a una estructura histórica previa y deben considerarse legacy:

- `docs/legacy/operacion/MEMORY.md`
- `docs/legacy/operacion/SKILL.md`
- `docs/legacy/operacion/README_original.md`
- `docs/legacy/operacion/requirements_original.txt`
