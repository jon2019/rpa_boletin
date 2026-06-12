# MEMORY — Boletín Minero-Energético

> Carga este archivo primero al abrir el proyecto en VS Code + Claude.
> Contiene el contexto esencial para retomar el trabajo sin releer todo el código.

---

## Contexto del cliente

**Empresa:** Marval Chile (referencia visual: RPA APSea–Argos)
**Objetivo:** Boletín automatizado de noticias minero-energéticas
**Frecuencia:** Martes y jueves, 9:00 AM hora Chile (America/Santiago)
**Países:** Configurables en la tabla `paises` de la DB (default: Chile 10 + Perú 10 + Argentina 10)
**Idiomas:** Español + inglés en un único correo bilingüe (layout dos columnas)
**Formato visual:** Dos columnas side-by-side (ES izquierda | EN derecha), cards por noticia

---

## Estructura en disco

```
rpa_boletin/                       ← raíz del proyecto
├── .env                           ← credenciales (mismo nivel que .venv y logs/)
├── .venv/                         ← entorno virtual Python
├── .lock                          ← lock de proceso único (se crea/borra automáticamente)
├── logs/                          ← logs diarios (un archivo por día)
│   └── boletin_YYYY-MM-DD.log
│
└── boletin/                       ← código fuente (aquí está este archivo)
    ├── main.py                    # orquestador + APScheduler + lock proceso único
    ├── scraper.py                 # RSS (feedparser) + scraping async (httpx + BS4)
    ├── scorer.py                  # scoring local + Claude IA + selección dinámica
    ├── translator.py              # traducción al inglés con Claude
    ├── emailer.py                 # HTML Jinja2 + envío SMTP con reintentos
    ├── db.py                      # PostgreSQL: todas las tablas y lógica
    ├── retrier.py                 # control de errores y reintentos centralizado
    ├── sources.py                 # OBSOLETO — referencia SQL, no lo lee ningún módulo
    ├── requirements.txt
    ├── .env.example               # plantilla completa de variables
    ├── .gitignore
    ├── README.md
    ├── SKILL.md                   ← leer primero al abrir en VS Code
    ├── MEMORY.md                  ← este archivo
    ├── Reglas_de_Negocio.docx     ← documento de reglas de negocio completo
    └── templates/
        └── boletin.html           # template Jinja2 — dos columnas ES|EN
```

**Log de hoy desde boletin/:** `tail -f ../logs/boletin_$(date +%Y-%m-%d).log`

---

## Estado actual del proyecto (todas las funcionalidades implementadas)

- [x] `scraper.py` — RSS (feedparser) + scraping async paralelo (httpx + BS4)
- [x] `db.py` — PostgreSQL con 9 tablas; fuentes, scoring y países en DB
- [x] `retrier.py` — Reintentos configurables + colector de errores + emails de alerta
- [x] `scorer.py` — Pre-score local + Claude semántico + selección dinámica por cuotas de país
- [x] `translator.py` — Traducción EN con Claude en batches con reintentos
- [x] `emailer.py` — HTML Jinja2 bilingüe + SMTP con reintentos
- [x] `main.py` — Orquestador + APScheduler + lock de proceso único (fcntl)
- [x] `templates/boletin.html` — Layout dos columnas ES|EN estilo APSea-Argos
- [x] Países dinámicos — tabla `paises` en DB, cuotas configurables por SQL
- [x] Control de concurrencia — `.lock` con fcntl garantiza proceso único
- [x] Logs diarios — un archivo por día en `rpa_boletin/logs/`
- [x] `.env` en raíz de `rpa_boletin/` al mismo nivel que `.venv/`

**Pendiente:**
- [ ] Verificar selectores CSS de fuentes sin RSS en producción (requiere internet)
- [ ] Probar con credenciales SMTP reales del cliente

---

## Módulo retrier.py — reglas clave

- **Hasta `REINTENTOS_MAX` intentos** (env, default 5, rango 1-10) por operación
- **Backoff exponencial:** `REINTENTOS_BACKOFF_BASE ^ intento` segundos (env, default 2)
- **4 tipos de error:** `URL_NO_DISPONIBLE` | `API_IA` | `BASE_DE_DATOS` | `ENVIO_EMAIL`
- **ErrorCollector:** acumula errores del flujo → un único email resumen al final
- **Error crítico de DB al arrancar** → email inmediato a `TO_EMAILS_ERRORES` + sys.exit(1)
- **`registrar_en_collector=False`** para errores de metadatos (checkpoint de DB) — no duplicar

---

## Control de concurrencia — proceso único

- Lock: `rpa_boletin/.lock` vía `fcntl.flock(LOCK_EX | LOCK_NB)`
- Si lock ocupado → `sys.exit(0)` inmediato (salida limpia, sin error)
- Lock liberado automáticamente al terminar (incluso ante excepciones)
- `--preview` no adquiere lock (permite revisión visual mientras scheduler corre)

---

## Tablas en PostgreSQL

| Tabla | Contenido |
|-------|-----------|
| `paises` | Países activos, cuotas, banderas, orden — dinámico |
| `fuentes` | 26 fuentes con URL, RSS, país, método, selector, activa |
| `score_reglas` | Pesos de scoring (+250, +150, +80, +60, +25) |
| `score_empresas` | Empresas → +80 en cualquier noticia |
| `score_empresas_conocidas` | Empresas → +150 al FIRMAR un contrato |
| `score_keywords` | Keywords de contratos/licitaciones |
| `noticias_enviadas` | Historial URLs enviadas — deduplicación |
| `ejecucion_fuentes` | Checkpoint por fuente+fecha (scraping_ok, ia_ok) |
| `envios_log` | Log de boletines enviados (JSONB flexible por país) |

---

## Checkpoint por fuente (ejecucion_fuentes)

Una fuente se omite hoy SOLO si `scraping_ok=TRUE AND ia_ok=TRUE`.

| scraping_ok | ia_ok | Acción en próxima ejecución |
|------------|-------|----------------------------|
| TRUE | TRUE | ✅ Omitir — ya completada |
| TRUE | FALSE | Reintentar solo IA |
| FALSE | FALSE | Reintentar todo |
| sin registro | — | Primera vez hoy |

---

## Dict de noticia normalizado

```python
{
    "titulo":     str,   # Título en español
    "titulo_en":  str,   # Título en inglés
    "url":        str,   # URL de la noticia
    "url_fuente": str,   # URL de la fuente en tabla `fuentes` (para registrar_ia)
    "resumen":    str,   # Resumen en español
    "resumen_en": str,   # Resumen en inglés
    "fecha":      str,   # ISO 8601 UTC
    "fuente":     str,   # Nombre del medio
    "pais":       str,   # Chile | Peru | Argentina | Internacional
    "pais_boletin": str, # País del slot (puede diferir si es relleno internacional)
    "score":      int,   # Puntuación final Claude
    "razon":      str,   # Explicación del score
}
```

---

## Variables de entorno (.env en rpa_boletin/)

```env
ANTHROPIC_API_KEY=sk-ant-...
DB_HOST=...  DB_PORT=5432  DB_NAME=...  DB_USER=...  DB_PASSWORD=...
SMTP_HOST=...  SMTP_PORT=587  SMTP_USER=...  SMTP_PASSWORD=...
FROM_EMAIL=...
TO_EMAILS=dest1@empresa.com,dest2@empresa.com
TO_EMAILS_ERRORES=admin@empresa.com          # errores críticos y resumen de fallos
EMPRESA_NOMBRE=Marval Chile
TIMEZONE=America/Santiago  HORA_ENVIO=9
REINTENTOS_MAX=5                             # intentos por operación (1-10)
REINTENTOS_BACKOFF_BASE=2                    # segundos base backoff exponencial
```

---

## Comandos del día a día

```bash
# Estando en rpa_boletin/boletin/
python main.py              # scheduler producción (mar/jue 9am)
python main.py --run-now    # ejecutar pipeline ahora (respeta lock)
python main.py --preview    # genera preview.html sin enviar (sin lock)

tail -f ../logs/boletin_$(date +%Y-%m-%d).log   # log de hoy
ls ../logs/                                        # todos los logs

# PostgreSQL útiles
psql -c "SELECT nombre, cuota, activo FROM paises ORDER BY orden;"
psql -c "SELECT nombre_fuente, scraping_ok, ia_ok FROM ejecucion_fuentes WHERE fecha_ejecucion = CURRENT_DATE;"
psql -c "SELECT fecha, total_noticias, por_pais FROM envios_log ORDER BY fecha DESC LIMIT 5;"
psql -c "DELETE FROM noticias_enviadas;"   # limpiar historial (solo desarrollo)
```

---

## Troubleshooting rápido

| Síntoma | Causa | Solución |
|---------|-------|----------|
| "PROCESO YA EN EJECUCIÓN" | Lock activo — pipeline corriendo | Esperar que termine o `rm ../. lock` si está colgado |
| Email de error crítico | DB no disponible al arrancar | Verificar PostgreSQL + variables DB_* en .env |
| Email resumen de errores | Alguna fuente falló tras 5 intentos | Ver detalle en el email; fuente se reintenta en próx. ejecución |
| 0 noticias nuevas | Todo ya enviado hoy | Normal si se ejecuta dos veces; revisar `noticias_enviadas` |
| Selector CSS retorna 0 | Sitio cambió estructura HTML | Actualizar `scrape_selector` en tabla `fuentes` de la DB |
| Error SMTP | Credenciales o puerto | Verificar SMTP_* en .env; Gmail requiere app password |

---

## Reglas de ejecución y control de fecha_ejecucion

- El pipeline se ejecuta automáticamente los martes y jueves (días autorizados).
- Cada ejecución registra en la base de datos la fecha de ejecución (`fecha_ejecucion`) para cada fuente procesada.
- La clave única es `(url_fuente, fecha_ejecucion)`, asegurando un solo registro por fuente y día.
- Si una fuente falla, su estado queda registrado como fallido para esa fecha.
- Si se reintenta el scraping fuera de los días autorizados, se debe usar la fecha del último día autorizado (martes o jueves), NO la fecha actual.
- Solo los días martes o jueves se debe usar la fecha actual como fecha_ejecucion.
- Cuando llega un nuevo día autorizado, se ejecutan todas las fuentes y se registra la nueva fecha.
- La función `calcular_fecha_efectiva` determina la fecha de referencia según el día actual:
  - Martes: usa el martes actual.
  - Jueves: usa el jueves actual.
  - Miércoles: usa el martes anterior.
  - Viernes, sábado, domingo, lunes: usa el jueves anterior.
- Si la fecha ya fue procesada completamente, el pipeline no se ejecuta.

### Resumen visual
| Día de ejecución | ¿Qué links se ejecutan?         | ¿Qué valor se guarda en fecha_ejecucion? |
|------------------|---------------------------------|------------------------------------------|
| Martes           | Todos                           | Martes                                   |
| Miércoles        | Solo fallidos del martes        | Martes                                   |
| Jueves           | Todos                           | Jueves                                   |
| Viernes          | Solo fallidos del jueves        | Jueves                                   |
| Sábado           | Solo fallidos del jueves        | Jueves                                   |
| Domingo          | Solo fallidos del jueves        | Jueves                                   |
| Lunes            | Solo fallidos del jueves        | Jueves                                   |
| Martes (nuevo)   | Todos                           | Martes                                   |

- Esta lógica garantiza reportes y trazabilidad correctos, y evita duplicidad o confusión en la base de datos.
- Si se requiere reintentar manualmente, siempre debe usarse la fecha_efectiva calculada, no la fecha actual del sistema.
