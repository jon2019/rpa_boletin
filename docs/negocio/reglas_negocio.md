# Reglas de negocio — RPA Boletín Minero-Energético

## 1. Frecuencia y temporalidad

### RN-FRE-001: Scheduler diario con decisión por fecha efectiva
**Enunciado**: El sistema puede ejecutarse diariamente, pero decide procesar o no según una fecha efectiva de negocio.  
**Implementación**: `calcular_fecha_efectiva()` + `db.fecha_fue_procesada_completamente()`.  
**Validación**: solo corre cuando la fecha efectiva aún no está completa.

### RN-FRE-002: Días objetivo del negocio
**Enunciado**: Los días objetivo del boletín son martes y jueves.  
**Implementación**: martes y jueves usan la fecha actual como fecha efectiva.  
**Validación**: las fechas efectivas base siempre corresponden a martes o jueves.

### RN-FRE-003: Compensación automática
**Enunciado**: Si falla un martes o jueves, días posteriores pueden compensar hasta completar esa fecha efectiva.  
**Implementación**:
- miércoles compensa martes
- viernes a lunes compensan jueves
- la DB evita duplicados al verificar completitud
**Validación**: una fecha incompleta puede retomarse en días posteriores.

### RN-FRE-004: Idempotencia por fecha efectiva
**Enunciado**: Repetir la ejecución del mismo día efectivo no debe generar doble envío ni reprocesamiento innecesario.  
**Implementación**: lock de proceso + checkpoints + historial de URLs + staging con `ON CONFLICT`.  
**Validación**: una misma noticia no se reenvía y una fuente completa no se reprocesa.

## 2. Contenido y selección

### RN-CON-001: Cobertura dinámica por país
**Enunciado**: Los países del boletín no están hardcodeados; se definen en la tabla `paises`.  
**Implementación**: `db.get_paises_activos()`.  
**Validación**: agregar o desactivar un país en DB cambia la selección sin modificar código.

### RN-CON-002: Cuotas por país
**Enunciado**: Cada país activo tiene una cuota máxima de noticias en el boletín final.  
**Implementación**: campo `paises.cuota` + `seleccionar_top_noticias()`.  
**Validación**: el total por país del boletín no supera la cuota configurada.

### RN-CON-003: Relleno con Internacional
**Enunciado**: Si un país no cubre su cuota, el déficit puede completarse con noticias `Internacional`.  
**Implementación**: noticias fuera de países activos se agrupan como `Internacional` y rellenan faltantes.  
**Validación**: el boletín puede completar cupos sin depender solo de noticias locales.

### RN-CON-004: Selección por relevancia
**Enunciado**: Las noticias se seleccionan por score local más score semántico con IA.  
**Implementación**: `_score_local()` -> candidatas -> `_score_con_claude()` por batches -> cuotas por país.  
**Validación**: la selección final sale de noticias previamente puntuadas.

### RN-CON-005: No reenvío histórico
**Enunciado**: Una noticia enviada anteriormente no puede volver a enviarse.  
**Implementación**: hash SHA-256 de URL en `noticias_enviadas`.  
**Validación**: `db.filtrar_enviadas()` descarta URLs ya registradas.

### RN-CON-006: Formato bilingüe
**Enunciado**: El boletín se distribuye con contenido en español e inglés.  
**Implementación**: `translator.traducir()` + template HTML bilingüe.  
**Validación**: el HTML final incluye ambas columnas.

## 3. Calidad y scoring

### RN-CAL-001: Pre-score local configurable
**Enunciado**: Toda noticia recibe un score local antes de usar IA.  
**Implementación**: `score_reglas`, `score_keywords`, `score_empresas`, `score_empresas_conocidas`.  
**Validación**: cada noticia candidata tiene un score base calculable.

### RN-CAL-002: Pesos de scoring en DB
**Enunciado**: Los pesos del scoring se administran desde base de datos.  
**Implementación**: `score_reglas(codigo, puntos, activa)`.  
**Validación**: un cambio de puntos en DB impacta la siguiente ejecución.

### RN-CAL-003: Dos clases de empresa prioritaria
**Enunciado**: Hay una lista de empresas relevantes para cualquier noticia y otra para bonus de contrato.  
**Implementación**:
- `score_empresas` -> bonus `empresa_noticia`
- `score_empresas_conocidas` -> bonus `empresa_conocida`
**Validación**: una noticia de contrato puede acumular ambos bonos si corresponde.

### RN-CAL-004: Análisis semántico solo para candidatas
**Enunciado**: La IA solo analiza las noticias mejor posicionadas en el pre-score.  
**Implementación**: `puntuar_y_seleccionar()` reduce el universo antes de invocar `_score_con_claude()`.  
**Validación**: no se envían todas las noticias a IA.

### RN-CAL-005: Lotes de IA por batch
**Enunciado**: El scoring semántico se ejecuta en batches para controlar contexto y costo.  
**Implementación**: `MAX_NOTICIAS_POR_BATCH = 20`.  
**Validación**: las llamadas a IA se fragmentan por lotes.

## 4. Fuentes y extracción

### RN-FUE-001: Fuentes administradas en DB
**Enunciado**: Las fuentes no viven en constantes del código.  
**Implementación**: tabla `fuentes`.  
**Validación**: `db.get_fuentes_activas()` lee la configuración activa.

### RN-FUE-002: Métodos de extracción mixtos
**Enunciado**: Una fuente puede procesarse por RSS, scraping o login autenticado.  
**Implementación**: `fuentes.url_rss`, `fuentes.scrape_selector`, `fuentes.usuario`, `fuentes.clave`, `fuentes.login_url`, `fuentes.post_login_url`.  
**Validación**: el pipeline clasifica fuentes operativas a partir de esos campos.

### RN-FUE-003: Checkpoint por fuente y fecha
**Enunciado**: El estado se guarda por combinación de fuente y fecha efectiva.  
**Implementación**: `ejecucion_fuentes`.  
**Validación**: cada fuente conserva su propio avance dentro del mismo ciclo.

### RN-FUE-004: Fuente completa solo con scraping e IA OK
**Enunciado**: Una fuente se considera completa solo si `scraping_ok=TRUE` e `ia_ok=TRUE`.  
**Implementación**: consulta de completitud sobre `ejecucion_fuentes`.  
**Validación**: `fecha_fue_procesada_completamente()` exige ambos flags.

### RN-FUE-005: Reintento solo-IA
**Enunciado**: Si scraping ya fue exitoso pero IA falló, no se debe re-scrapear.  
**Implementación**: `articulos_pendientes` + `get_fuentes_solo_ia()` + `get_articulos_pendientes()`.  
**Validación**: la siguiente ejecución recupera artículos desde DB y reintenta solo IA.

### RN-FUE-006: Protección contra doble cobro de IA
**Enunciado**: Un artículo ya procesado por IA no debe volver a cobrarse si falla la limpieza posterior.  
**Implementación**: `articulos_pendientes.ia_procesada=TRUE` inmediatamente después del scoring exitoso.  
**Validación**: `get_articulos_pendientes()` solo recupera `ia_procesada=FALSE`.

### RN-FUE-007: Visibilidad de fuentes con problemas operativos
**Enunciado**: Las fuentes activas sin configuración operativa válida deben quedar visibles para revisión.  
**Implementación**: `db.get_fuentes_con_problemas_operativos()` + tabla de warning en logs + alerta operativa por email fuera de preview.  
**Validación**: el pipeline las reporta al iniciar y puede disparar correo operativo.

## 5. Distribución y comunicación

### RN-DIS-001: Destinatarios configurables
**Enunciado**: Los destinatarios se definen por configuración y no en código.  
**Implementación**: `TO_EMAILS`.  
**Validación**: el envío usa la lista del entorno.

### RN-DIS-002: HTML bilingüe
**Enunciado**: El boletín se genera en HTML con columna en español e inglés.  
**Implementación**: template Jinja + noticias traducidas.  
**Validación**: el preview y el email comparten el mismo render.

### RN-DIS-003: Preview sin envío
**Enunciado**: Debe existir un modo de revisión que no dispare email.  
**Implementación**: `--preview` genera `tests/output/previews/preview.html`.  
**Validación**: se obtiene render local sin SMTP.

### RN-DIS-004: Registro de cada intento de envío
**Enunciado**: Cada intento de envío se registra, incluyendo reintentos y errores.  
**Implementación**: `envios_log(fecha, total_noticias, por_pais, ok)`.  
**Validación**: puede haber múltiples filas para la misma fecha efectiva de negocio.

## 6. Confiabilidad y monitoreo

### RN-CONF-001: Proceso único
**Enunciado**: Solo puede haber una instancia activa del pipeline, salvo preview.  
**Implementación**: lock file `.lock` con `portalocker`; preview no toma lock.  
**Validación**: una segunda instancia termina sin ejecutar.

### RN-CONF-002: Reintentos con backoff
**Enunciado**: Los fallos transitorios deben reintentarse.  
**Implementación**: `retrier.con_reintentos()`.  
**Validación**: DB, red, scraping y API IA usan el mecanismo común.

### RN-CONF-003: Clasificación de errores
**Enunciado**: Los errores deben clasificarse por tipo para tratamiento diferenciado.  
**Implementación**: `retrier.TipoError`.  
**Validación**: los errores se registran por categoría.

### RN-CONF-004: Resumen agrupado de errores
**Enunciado**: Los errores acumulados en un ciclo se notifican de forma consolidada.  
**Implementación**: `ErrorCollector`.  
**Validación**: al final del pipeline se revisa y envía resumen si corresponde.

### RN-CONF-005: Logging diario estructurado
**Enunciado**: La operación debe quedar trazable por archivo y timestamp.  
**Implementación**: log diario en `logs/boletin_YYYY-MM-DD.log`.  
**Validación**: existe un archivo por día con eventos del ciclo.

## 7. Seguridad y configuración

### RN-SEG-001: Credenciales externas
**Enunciado**: Las credenciales de DB, SMTP e IA se cargan por variables de entorno.  
**Implementación**: `.env` + `load_dotenv()`.  
**Validación**: no se requieren secretos hardcodeados.

### RN-SEG-002: Validación temprana de configuración crítica
**Enunciado**: La configuración inválida debe fallar al inicio.  
**Implementación**: validación de `HORA_ENVIO` y health check de DB.  
**Validación**: el proceso no arranca con configuración esencial inválida.

### RN-SEG-003: Timeouts y límites en integraciones
**Enunciado**: Las integraciones externas deben operar con límites razonables.  
**Implementación**: `connect_timeout` en DB, timeouts de scraping/SMTP y batches acotados de IA.  
**Validación**: la ejecución evita bloqueos indefinidos por llamadas externas.

## 8. Mantenimiento y evolución

### RN-MAN-001: Configuración DB-driven
**Enunciado**: La lógica operativa debe cambiar principalmente vía DB y no vía despliegue.  
**Implementación**: fuentes, países y scoring viven en tablas.  
**Validación**: cambios de catálogo o puntajes impactan sin tocar código.

### RN-MAN-002: Schema autocreable desde código
**Enunciado**: El sistema debe poder inicializar su schema base.  
**Implementación**: `db.init_db()` -> `schema_repository.init_db()`.  
**Validación**: una base nueva puede crear tablas e insertar seeds.

### RN-MAN-003: Documentación versionada junto al código
**Enunciado**: La documentación del negocio y del modelo debe vivir en el repo.  
**Implementación**: carpeta `docs/`, separando documentación vigente de material legacy.  
**Validación**: los markdown se actualizan junto con cambios funcionales.

## Matriz resumida de implementación

| Regla | main.py | pipeline_service.py | db.py / repositorios | scraper.py | scorer.py | translator.py | emailer.py |
|-------|---------|---------------------|----------------------|------------|-----------|---------------|------------|
| RN-FRE-001 a RN-FRE-004 | X | X | X |  |  |  |  |
| RN-CON-001 a RN-CON-006 |  | X | X | X | X | X | X |
| RN-CAL-001 a RN-CAL-005 |  |  | X |  | X |  |  |
| RN-FUE-001 a RN-FUE-007 |  | X | X | X | X |  | X |
| RN-DIS-001 a RN-DIS-004 | X | X | X |  |  |  | X |
| RN-CONF-001 a RN-CONF-005 | X | X | X | X | X | X | X |
| RN-SEG-001 a RN-SEG-003 | X |  | X | X | X |  | X |
| RN-MAN-001 a RN-MAN-003 | X |  | X |  |  |  |  |

---

**Estado**: validado contra `main.py`, `pipeline_service.py`, repositorios DB, `scraper.py`, `scorer.py`, `translator.py` y `emailer.py`.
