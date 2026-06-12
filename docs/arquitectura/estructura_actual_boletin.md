# Estructura actual de `src/boletin`

## Árbol resumido

```text
src/boletin/
  main.py
  config/
    environment.py
    logging.py
    runtime.py
    settings.py
  infrastructure/
    db/
      connection.py
      execution_repository.py
      facade.py
      pending_articles_repository.py
      schema_repository.py
      sent_news_repository.py
      sources_repository.py
    notifications/
      emailer.py
    resilience/
      retrier.py
    runtime_lock.py
  runtime/
    bootstrap.py
  scraping/
    orchestrator.py
    browser.py
    constants.py
    extraction.py
    feed_detection.py
    flaresolverr.py
    login.py
    source_rules.py
  services/
    execution_calendar.py
    pipeline_reporting.py
    pipeline_service.py
    scheduler_service.py
    scoring_service.py
    source_classifier.py
    translation_service.py
  templates/
    boletin.html
```

## Criterio de organización

### `main.py`
Entrypoint fino del sistema:
- parsea CLI
- carga settings
- configura logging
- hace bootstrap
- delega ejecución

### `config/`
Configuración compartida:
- carga centralizada de entorno
- settings de runtime
- logging
- paths del proyecto

### `infrastructure/`
Implementaciones técnicas y adaptadores externos:
- acceso a PostgreSQL
- envío de email
- retry / backoff / alertas de error
- lock de proceso

### `runtime/`
Bootstrap del arranque:
- validación de conexión
- inicialización de infraestructura

### `scraping/`
Subsistema de scraping:
- orquestación
- login
- browser fallback
- extracción
- FlareSolverr
- reglas y constantes

### `services/`
Casos de uso y orquestación de aplicación:
- pipeline principal
- scheduler
- scoring
- traducción
- calendario de ejecución
- reporting

### `templates/`
Templates HTML del boletín.

## Cambios relevantes respecto de la estructura anterior

Se movieron fuera de la raíz:

- `db.py` → `infrastructure/db/facade.py`
- `emailer.py` → `infrastructure/notifications/emailer.py`
- `retrier.py` → `infrastructure/resilience/retrier.py`
- `runtime_bootstrap.py` → `runtime/bootstrap.py`
- `scorer.py` → `services/scoring_service.py`
- `translator.py` → `services/translation_service.py`
- `scraper.py` → `scraping/orchestrator.py`

## Nota arquitectónica

`infrastructure/db/facade.py` sigue existiendo como fachada de compatibilidad.
La implementación real de base de datos vive en `infrastructure/db/`.
