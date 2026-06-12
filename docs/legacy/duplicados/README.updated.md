# Documentacion del Sistema RPA Boletin Minero-Energetico

## Vision General

Sistema automatizado de recopilacion, priorizacion y distribucion de noticias para un boletin bilingue del sector minero-energetico. El pipeline corre diariamente, pero usa una fecha efectiva de negocio para garantizar dos ciclos semanales con compensacion y sin duplicados.

La documentacion de referencia para el schema detallado es `modelo_datos.md`. Este README resume la arquitectura, las reglas operativas y los flujos mas importantes.

## Arquitectura

### Componentes principales

1. **Scheduler**: ejecuta `run_pipeline()` todos los dias a la hora configurada.
2. **DB**: persiste configuracion, checkpoints por fuente, historial de noticias enviadas y staging de articulos pendientes.
3. **Scraper**: obtiene noticias desde RSS o scraping por selector.
4. **Scorer**: aplica pre-score local y scoring semantico con IA.
5. **Translator**: traduce titulo y resumen al ingles.
6. **Emailer**: construye el HTML bilingue y envia o genera preview.
7. **Retrier/ErrorCollector**: maneja reintentos, clasificacion y resumen de errores.

### Pipeline

```text
Scheduler diario
      |
      v
calcular_fecha_efectiva()
      |
      v
obtener_fuentes_pendientes(fecha_efectiva)
      |
      v
separar fuentes en:
- scraping + IA
- solo-IA
      |
      v
scraper.scrape_all(...)
      |
      v
db.get_articulos_pendientes(...) para fuentes solo-IA
      |
      v
db.filtrar_enviadas(...)
      |
      v
scorer.puntuar_y_seleccionar(...)
      |
      v
translator.traducir(...)
      |
      v
emailer.enviar(...) o preview_html(...)
      |
      v
db.marcar_enviadas(...)
db.registrar_envio(...)
db.limpiar_articulos_pendientes(...)
```

## Logica de Ejecucion

### Frecuencia y fecha efectiva

- El scheduler corre todos los dias.
- Los dias objetivo de negocio son martes y jueves.
- Miercoles compensa martes si la fecha efectiva aun no esta completa.
- Viernes, sabado, domingo y lunes compensan jueves si esa fecha efectiva aun no esta completa.

### Regla de decision

`calcular_fecha_efectiva()` determina la fecha efectiva candidata y consulta `db.fecha_fue_procesada_completamente(fecha)`.

- Si todas las fuentes activas tienen `scraping_ok=TRUE` e `ia_ok=TRUE` para esa fecha, el pipeline no corre.
- Si falta al menos una fuente por completar, el pipeline si corre.

### Idempotencia

La ejecucion es idempotente por combinacion de mecanismos:

- lock de proceso `.lock`
- checkpoints en `ejecucion_fuentes`
- historial `noticias_enviadas`
- `ON CONFLICT DO NOTHING` en `articulos_pendientes`

## Reglas Operativas Actuales

### Fuentes

- Las fuentes viven en DB, no en codigo.
- Solo se procesan fuentes activas.
- Una fuente puede ser `rss` o `scrape`.
- Para fuentes `scrape`, `scrape_selector` define la extraccion. Si falta, el sistema lo reporta en logs.

### Checkpoint por fuente

`ejecucion_fuentes` guarda el estado por `url_fuente + fecha_ejecucion`.

- `scraping_ok=TRUE` indica que la extraccion termino bien.
- `ia_ok=TRUE` indica que el lote de IA se proceso bien.
- Una fuente esta completa solo si ambos flags estan en `TRUE`.

### Modo solo-IA

Si una fuente ya tuvo scraping exitoso pero fallo la IA:

- queda con `scraping_ok=TRUE` e `ia_ok=FALSE`
- sus articulos quedan en `articulos_pendientes`
- la siguiente ejecucion del mismo dia efectivo reutiliza esos articulos sin volver a scrapear

Esto reduce costo, evita re-trabajo y protege contra doble cobro usando `ia_procesada`.

### Seleccion de noticias

El pipeline aplica:

1. pre-score local con reglas configuradas en DB
2. top 60 a IA para scoring semantico
3. seleccion final segun cuotas activas por pais

Las cuotas ya no estan fijas a tres paises. Se leen dinamicamente desde `paises WHERE activo = TRUE ORDER BY orden`.

### Cobertura geografica

- Los paises activos del boletin se definen en la tabla `paises`.
- Una noticia cuyo pais no pertenezca a ese conjunto se trata como `Internacional`.
- Si un pais no llena su cuota, el deficit se puede completar con noticias `Internacional`.

### Duplicados

- Ninguna URL enviada se vuelve a enviar.
- La deduplicacion usa SHA-256 de la URL en `noticias_enviadas.url_hash`.

### Distribucion

- El boletin se genera en espanol e ingles.
- En modo normal se envia por SMTP.
- En modo preview se genera `preview.html` sin envio.

### Registro de envios

Cada intento de envio se registra en `envios_log` con:

- `fecha` timestamp del intento
- `total_noticias`
- `por_pais` en JSON
- `ok`

No existe restriccion de una fila por fecha efectiva; se permiten multiples registros en caso de reintentos.

## Modelo de Datos Resumido

Tablas principales:

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

Para el detalle de columnas, indices y reglas de integridad, ver `modelo_datos.md`.

## Variables de Entorno

### Base de datos

```env
DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
```

### IA

```env
ANTHROPIC_API_KEY=
```

### Email

```env
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
FROM_EMAIL=
TO_EMAILS=correo1@empresa.com,correo2@empresa.com
```

### Sistema

```env
TIMEZONE=America/Santiago
HORA_ENVIO=9
BOLETIN_SUBTITULO_ES=Mineria y Energia
BOLETIN_SUBTITULO_EN=Mining & Energy
```

## Comandos de Ejecucion

```bash
python boletin/main.py --preview
python boletin/main.py --run-now
python boletin/main.py
```

## Monitoreo

### Logs

- Se genera un archivo diario en `logs/boletin_YYYY-MM-DD.log`.
- El logging usa timestamps UTC.

### Senales utiles

- fuentes activas sin selector
- fuentes en modo compensacion
- fuentes en modo solo-IA
- articulos recuperados desde staging
- cantidad de noticias filtradas por historial
- resumen final por fecha efectiva

### Errores

- los errores se clasifican por tipo en `retrier.TipoError`
- el `ErrorCollector` agrupa los errores y puede enviar un resumen al final del ciclo

## Referencias

- `modelo_datos.md`: schema real y diccionario de datos
- `reglas_negocio.md`: catalogo de reglas vigentes
- `modelo_relacional.md`: vista resumida del modelo

---

**Ultima actualizacion**: Abril 2026  
**Estado**: Alineado con el flujo actual del codigo (`main.py`, `db.py`, `scorer.py`, `emailer.py`)
