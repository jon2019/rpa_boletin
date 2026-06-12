# Reglas de Negocio - RPA Boletin Minero-Energetico

## 1. Frecuencia y Temporalidad

### RN-FRE-001: Scheduler diario con decision por fecha efectiva
**Enunciado**: El sistema puede ejecutarse diariamente, pero decide procesar o no segun una fecha efectiva de negocio.  
**Implementacion**: `calcular_fecha_efectiva()` + `db.fecha_fue_procesada_completamente()`.  
**Validacion**: solo corre cuando la fecha efectiva aun no esta completa.

### RN-FRE-002: Dias objetivo del negocio
**Enunciado**: Los dias objetivo del boletin son martes y jueves.  
**Implementacion**: martes y jueves usan la fecha actual como fecha efectiva.  
**Validacion**: las fechas efectivas base siempre corresponden a martes o jueves.

### RN-FRE-003: Compensacion automatica
**Enunciado**: Si falla un martes o jueves, dias posteriores pueden compensar hasta completar esa fecha efectiva.  
**Implementacion**:
- miercoles compensa martes
- viernes a lunes compensan jueves
- la DB evita duplicados al verificar completitud
**Validacion**: una fecha incompleta puede retomarse en dias posteriores.

### RN-FRE-004: Idempotencia por fecha efectiva
**Enunciado**: Repetir la ejecucion del mismo dia efectivo no debe generar doble envio ni reprocesamiento innecesario.  
**Implementacion**: lock de proceso + checkpoints + historial de URLs + staging con `ON CONFLICT`.  
**Validacion**: una misma noticia no se reenvia y una fuente completa no se reprocesa.

## 2. Contenido y Seleccion

### RN-CON-001: Cobertura dinamica por pais
**Enunciado**: Los paises del boletin no estan hardcodeados; se definen en la tabla `paises`.  
**Implementacion**: `db.get_paises_activos()`.  
**Validacion**: agregar o desactivar un pais en DB cambia la seleccion sin modificar codigo.

### RN-CON-002: Cuotas por pais
**Enunciado**: Cada pais activo tiene una cuota maxima de noticias en el boletin final.  
**Implementacion**: campo `paises.cuota` + `seleccionar_top_noticias()`.  
**Validacion**: el total por pais del boletin no supera la cuota configurada.

### RN-CON-003: Relleno con Internacional
**Enunciado**: Si un pais no cubre su cuota, el deficit puede completarse con noticias `Internacional`.  
**Implementacion**: noticias fuera de paises activos se agrupan como `Internacional` y rellenan faltantes.  
**Validacion**: el boletin puede completar cupos sin depender solo de noticias locales.

### RN-CON-004: Seleccion por relevancia
**Enunciado**: Las noticias se seleccionan por score local mas score semantico con IA.  
**Implementacion**: `_score_local()` -> top 60 -> `_score_con_claude()` -> cuotas por pais.  
**Validacion**: la seleccion final sale de noticias previamente puntuadas.

### RN-CON-005: No reenvio historico
**Enunciado**: Una noticia enviada anteriormente no puede volver a enviarse.  
**Implementacion**: hash SHA-256 de URL en `noticias_enviadas`.  
**Validacion**: `db.filtrar_enviadas()` descarta URLs ya registradas.

### RN-CON-006: Formato bilingue
**Enunciado**: El boletin se distribuye con contenido en espanol e ingles.  
**Implementacion**: `translator.traducir()` + template HTML bilingue.  
**Validacion**: el HTML final incluye ambas columnas.

## 3. Calidad y Scoring

### RN-CAL-001: Pre-score local configurable
**Enunciado**: Toda noticia recibe un score local antes de usar IA.  
**Implementacion**: `score_reglas`, `score_keywords`, `score_empresas`, `score_empresas_conocidas`.  
**Validacion**: cada noticia candidata tiene un score base calculable.

### RN-CAL-002: Pesos de scoring en DB
**Enunciado**: Los pesos del scoring se administran desde base de datos.  
**Implementacion**: `score_reglas(codigo, puntos, activa)`.  
**Validacion**: un cambio de puntos en DB impacta la siguiente ejecucion.

### RN-CAL-003: Dos clases de empresa prioritaria
**Enunciado**: Hay una lista de empresas relevantes para cualquier noticia y otra para bonus de contrato.  
**Implementacion**:
- `score_empresas` -> bonus `empresa_noticia`
- `score_empresas_conocidas` -> bonus `empresa_conocida`
**Validacion**: una noticia de contrato puede acumular ambos bonos si corresponde.

### RN-CAL-004: Analisis semantico solo para candidatas
**Enunciado**: La IA solo analiza las noticias mejor posicionadas en el pre-score.  
**Implementacion**: top 60 por score local.  
**Validacion**: no se envian todas las noticias a IA.

### RN-CAL-005: Lotes de IA por batch
**Enunciado**: El scoring semantico se ejecuta en batches para controlar contexto y costo.  
**Implementacion**: `MAX_NOTICIAS_POR_BATCH = 20`.  
**Validacion**: las llamadas a IA se fragmentan por lotes.

## 4. Fuentes y Extraccion

### RN-FUE-001: Fuentes administradas en DB
**Enunciado**: Las fuentes no viven en constantes del codigo.  
**Implementacion**: tabla `fuentes`.  
**Validacion**: `db.get_fuentes_activas()` lee la configuracion activa.

### RN-FUE-002: Metodos de extraccion mixtos
**Enunciado**: Una fuente puede procesarse por RSS o por scraping.  
**Implementacion**: `fuentes.metodo`, `fuentes.url_rss`, `fuentes.scrape_selector`.  
**Validacion**: el pipeline separa fuentes RSS y scraping.

### RN-FUE-003: Checkpoint por fuente y fecha
**Enunciado**: El estado se guarda por combinacion de fuente y fecha efectiva.  
**Implementacion**: `ejecucion_fuentes`.  
**Validacion**: cada fuente conserva su propio avance dentro del mismo ciclo.

### RN-FUE-004: Fuente completa solo con scraping e IA OK
**Enunciado**: Una fuente se considera completa solo si `scraping_ok=TRUE` e `ia_ok=TRUE`.  
**Implementacion**: consulta de completitud sobre `ejecucion_fuentes`.  
**Validacion**: `fecha_fue_procesada_completamente()` exige ambos flags.

### RN-FUE-005: Reintento solo-IA
**Enunciado**: Si scraping ya fue exitoso pero IA fallo, no se debe re-scrapear.  
**Implementacion**: `articulos_pendientes` + `get_fuentes_solo_ia()` + `get_articulos_pendientes()`.  
**Validacion**: la siguiente ejecucion recupera articulos desde DB y reintenta solo IA.

### RN-FUE-006: Proteccion contra doble cobro de IA
**Enunciado**: Un articulo ya procesado por IA no debe volver a cobrarse si falla la limpieza posterior.  
**Implementacion**: `articulos_pendientes.ia_procesada=TRUE` inmediatamente despues del scoring exitoso.  
**Validacion**: `get_articulos_pendientes()` solo recupera `ia_procesada=FALSE`.

### RN-FUE-007: Visibilidad de fuentes sin selector
**Enunciado**: Las fuentes activas que requieren scraping y no tienen selector deben quedar visibles para revision.  
**Implementacion**: `db.get_fuentes_sin_selector()` + warning en logs.  
**Validacion**: el pipeline las reporta al iniciar.

## 5. Distribucion y Comunicacion

### RN-DIS-001: Destinatarios configurables
**Enunciado**: Los destinatarios se definen por configuracion y no en codigo.  
**Implementacion**: `TO_EMAILS`.  
**Validacion**: el envio usa la lista del entorno.

### RN-DIS-002: HTML bilingue
**Enunciado**: El boletin se genera en HTML con columna en espanol e ingles.  
**Implementacion**: template Jinja + noticias traducidas.  
**Validacion**: el preview y el email comparten el mismo render.

### RN-DIS-003: Preview sin envio
**Enunciado**: Debe existir un modo de revision que no dispare email.  
**Implementacion**: `--preview` genera `preview.html`.  
**Validacion**: se obtiene render local sin SMTP.

### RN-DIS-004: Registro de cada intento de envio
**Enunciado**: Cada intento de envio se registra, incluyendo reintentos y errores.  
**Implementacion**: `envios_log(fecha, total_noticias, por_pais, ok)`.  
**Validacion**: puede haber multiples filas para la misma fecha efectiva de negocio.

## 6. Confiabilidad y Monitoreo

### RN-CONF-001: Proceso unico
**Enunciado**: Solo puede haber una instancia activa del pipeline, salvo preview.  
**Implementacion**: lock file `.lock` con `portalocker`.  
**Validacion**: una segunda instancia termina sin ejecutar.

### RN-CONF-002: Reintentos con backoff
**Enunciado**: Los fallos transitorios deben reintentarse.  
**Implementacion**: `retrier.con_reintentos()`.  
**Validacion**: DB, red y API IA usan el mecanismo comun.

### RN-CONF-003: Clasificacion de errores
**Enunciado**: Los errores deben clasificarse por tipo para tratamiento diferenciado.  
**Implementacion**: `retrier.TipoError`.  
**Validacion**: los errores se registran por categoria.

### RN-CONF-004: Resumen agrupado de errores
**Enunciado**: Los errores acumulados en un ciclo se notifican de forma consolidada.  
**Implementacion**: `ErrorCollector`.  
**Validacion**: al final del pipeline se revisa y envia resumen si corresponde.

### RN-CONF-005: Logging diario estructurado
**Enunciado**: La operacion debe quedar trazable por archivo y timestamp.  
**Implementacion**: log diario en `logs/`.  
**Validacion**: existe un archivo por dia con eventos del ciclo.

## 7. Seguridad y Configuracion

### RN-SEG-001: Credenciales externas
**Enunciado**: Las credenciales de DB, SMTP e IA se cargan por variables de entorno.  
**Implementacion**: `.env` + `load_dotenv()`.  
**Validacion**: no se requieren secretos hardcodeados.

### RN-SEG-002: Validacion temprana de configuracion critica
**Enunciado**: La configuracion invalida debe fallar al inicio.  
**Implementacion**: validacion de `HORA_ENVIO` y health check de DB.  
**Validacion**: el proceso no arranca con configuracion esencial invalida.

### RN-SEG-003: Timeouts y limites en integraciones
**Enunciado**: Las integraciones externas deben operar con limites razonables.  
**Implementacion**: timeouts de conexion DB y batch size en IA; el scraper aplica estrategias acotadas por fuente.  
**Validacion**: la ejecucion evita bloqueos indefinidos por llamadas externas.

## 8. Mantenimiento y Evolucion

### RN-MAN-001: Configuracion DB-driven
**Enunciado**: La logica operativa debe cambiar principalmente via DB y no via despliegue.  
**Implementacion**: fuentes, paises y scoring viven en tablas.  
**Validacion**: cambios de catalogo o puntajes impactan sin tocar codigo.

### RN-MAN-002: Schema autocreable desde codigo
**Enunciado**: El sistema debe poder inicializar su schema base.  
**Implementacion**: `db.init_db()`.  
**Validacion**: una base nueva puede crear tablas e insertar seeds.

### RN-MAN-003: Documentacion versionada junto al codigo
**Enunciado**: La documentacion del negocio y del modelo debe vivir en el repo.  
**Implementacion**: carpeta `doc/`.  
**Validacion**: los markdown se actualizan junto con cambios funcionales.

## Matriz Resumida de Implementacion

| Regla | main.py | db.py | scraper.py | scorer.py | translator.py | emailer.py |
|-------|---------|-------|------------|-----------|---------------|------------|
| RN-FRE-001 a RN-FRE-004 | X | X |  |  |  |  |
| RN-CON-001 a RN-CON-006 |  | X | X | X | X | X |
| RN-CAL-001 a RN-CAL-005 |  | X |  | X |  |  |
| RN-FUE-001 a RN-FUE-007 | X | X | X | X |  |  |
| RN-DIS-001 a RN-DIS-004 | X | X |  |  |  | X |
| RN-CONF-001 a RN-CONF-005 | X | X | X | X | X | X |
| RN-SEG-001 a RN-SEG-003 | X | X | X | X |  | X |
| RN-MAN-001 a RN-MAN-003 | X | X |  |  |  |  |

---

**Ultima actualizacion**: Abril 2026  
**Estado**: alineado con el flujo real implementado en `main.py`, `db.py` y `scorer.py`
