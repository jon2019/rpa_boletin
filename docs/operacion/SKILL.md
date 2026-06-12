# SKILL: Boletín Minero-Energético Automatizado

## ¿Qué es este proyecto?
Sistema RPA con IA que recopila noticias de minería y energía de Latinoamérica,
las puntúa con Claude, las traduce al inglés y envía un boletín HTML bilingüe por email
**martes y jueves a las 9:00 AM (America/Santiago)**.

Completamente configurable desde PostgreSQL — sin modificar código para cambiar fuentes,
pesos de scoring, cuotas por país o agregar nuevos países.

---

## Módulos y responsabilidades

| Archivo | Responsabilidad |
|---------|----------------|
| `main.py` | Orquestador + APScheduler + lock proceso único (fcntl) |
| `retrier.py` | Reintentos configurables + ErrorCollector + emails de alerta |
| `scraper.py` | RSS (feedparser síncrono) + scraping async paralelo (httpx + BS4) |
| `scorer.py` | Pre-score local + Claude semántico en batches + selección dinámica |
| `translator.py` | Traducción EN con Claude (batches de 15) |
| `emailer.py` | Jinja2 HTML bilingüe + envío SMTP con reintentos |
| `db.py` | PostgreSQL: 9 tablas, seed automático, todas las operaciones con reintentos |
| `sources.py` | **OBSOLETO** — solo referencia SQL, no lo lee ningún módulo |
| `templates/boletin.html` | Layout dos columnas ES\|EN estilo APSea-Argos |

---

## Estructura en disco

```
rpa_boletin/                       ← raíz del proyecto
├── .env                           ← credenciales (mismo nivel que .venv y logs/)
├── .venv/                         ← entorno virtual Python
├── .lock                          ← lock automático (se crea/borra en cada ejecución)
├── logs/                          ← logs diarios (un archivo por día)
│   └── boletin_YYYY-MM-DD.log
│
└── boletin/                       ← código fuente
    ├── main.py
    ├── retrier.py                 # NUEVO — control de errores centralizado
    ├── scraper.py
    ├── scorer.py
    ├── translator.py
    ├── emailer.py
    ├── db.py
    ├── sources.py                 # OBSOLETO
    ├── requirements.txt
    ├── .env.example
    ├── .gitignore
    ├── README.md
    ├── SKILL.md                   # este archivo
    ├── MEMORY.md
    ├── Reglas_de_Negocio.docx
    └── templates/
        └── boletin.html
```

---

## Pipeline completo

```
APScheduler (mar/jue 9am) + lock fcntl
         │
         ▼
[1] retrier.reset_collector()              ← limpia errores del ciclo anterior
         │
         ▼
[2] db.get_fuentes_activas()               ← tabla: fuentes (activa=TRUE)
    db.fuentes_pendientes_hoy()            ← tabla: ejecucion_fuentes
         │  omite fuentes con scraping_ok AND ia_ok = TRUE hoy
         ▼
[3] scraper.fetch_all()                    ← RSS + scraping async
         │  hasta REINTENTOS_MAX intentos por fuente
         │  registra scraping_ok en ejecucion_fuentes
         ▼
[4] db.filtrar_enviadas()                  ← tabla: noticias_enviadas (hash SHA-256)
         │
         ▼
[5] scorer.puntuar_y_seleccionar()
    ├── _score_local()                     ← tablas: score_keywords, score_empresas,
    │                                                 score_empresas_conocidas, score_reglas
    ├── _score_con_claude() [batches 20]   ← Claude API con reintentos
    │   registra ia_ok en ejecucion_fuentes
    └── seleccionar_top_noticias()         ← tabla: paises (cuotas dinámicas)
         │
         ▼
[6] translator.traducir() [batches 15]     ← Claude API con reintentos
         │
         ▼
[7] emailer.enviar()                       ← SMTP con reintentos
         │
         ▼
[8] db.marcar_enviadas()                   ← tabla: noticias_enviadas
    db.registrar_envio()                   ← tabla: envios_log (JSONB por país)
         │
         ▼
[9] collector.enviar_resumen()             ← email agrupado de errores (si los hubo)
```

---

## retrier.py — módulo de control de errores

**Variables de entorno:**
- `REINTENTOS_MAX` — intentos por operación (default 5, rango 1-10)
- `REINTENTOS_BACKOFF_BASE` — segundos base backoff (default 2)
  - Ejemplo MAX=5, BASE=2: esperas 2s → 4s → 8s → 16s

**4 tipos de error clasificados:**
- `TipoError.URL_NO_DISPONIBLE` — HTTP errors, timeouts en scraping
- `TipoError.API_IA` — errores en llamadas a Claude (Anthropic)
- `TipoError.BASE_DE_DATOS` — fallos en operaciones PostgreSQL del flujo
- `TipoError.ENVIO_EMAIL` — fallos SMTP al enviar el boletín

**Dos flujos de notificación:**
1. **Error crítico de DB al arrancar** → `retrier.error_critico()` → email inmediato a `TO_EMAILS_ERRORES` → `sys.exit(1)`
2. **Errores durante el pipeline** → `ErrorCollector.registrar()` → al finalizar, `enviar_resumen()` envía **un único email** con todos los errores agrupados por tipo a `TO_EMAILS_ERRORES`

**Importante:** `registrar_en_collector=False` se usa en `db.py` para errores de escritura de metadatos (checkpoints) — evita duplicar errores en el resumen.

---

## Control de concurrencia

- Archivo: `rpa_boletin/.lock`
- Mecanismo: `fcntl.flock(LOCK_EX | LOCK_NB)` — bloqueo exclusivo no bloqueante
- Si lock ocupado → `sys.exit(0)` (salida limpia, sin error)
- `--preview` no adquiere lock
- El lock se libera siempre al terminar (context manager)

---

## Tablas en PostgreSQL

| Tabla | Reemplaza | Gestión |
|-------|-----------|---------|
| `paises` | Hardcoded Chile/Peru/Argentina | SQL: INSERT/UPDATE |
| `fuentes` | `sources.py → SOURCES` | SQL: INSERT/UPDATE activa |
| `score_reglas` | Constantes en código | SQL: UPDATE puntos |
| `score_empresas` | `HIGH_VALUE_COMPANIES` | SQL: INSERT/UPDATE activa |
| `score_empresas_conocidas` | NUEVO — lista separada | SQL: INSERT/UPDATE activa |
| `score_keywords` | `CONTRACT_KEYWORDS` | SQL: INSERT/UPDATE activa |
| `noticias_enviadas` | — | Automático |
| `ejecucion_fuentes` | — | Automático |
| `envios_log` | — | Automático (JSONB) |

**Seed automático:** `db.init_db()` crea tablas y pobla datos iniciales si están vacías.

---

## score_empresas vs score_empresas_conocidas

- **`score_empresas`** → `+80` cuando la empresa se **menciona** en cualquier noticia
- **`score_empresas_conocidas`** → `+150` cuando la empresa **firma** un contrato (requiere keyword de contrato en la misma noticia)
- Una empresa puede estar en ambas listas (puntajes se acumulan)

---

## Países dinámicos

```sql
-- Agregar país
INSERT INTO paises (nombre, nombre_en, codigo_iso, bandera, cuota, orden)
VALUES ('Colombia', 'Colombia', 'COL', '🇨🇴', 10, 4);

-- Cambiar cuota
UPDATE paises SET cuota = 15 WHERE nombre = 'Chile';

-- Desactivar país
UPDATE paises SET activo = FALSE WHERE nombre = 'Peru';
```

---

## Dict de noticia

```python
{
    "titulo": str, "titulo_en": str,
    "url": str,           # URL de la noticia
    "url_fuente": str,    # URL de la fuente en tabla `fuentes` (para registrar_ia)
    "resumen": str, "resumen_en": str,
    "fecha": str,         # ISO 8601 UTC
    "fuente": str,        # Nombre del medio
    "pais": str,          # Chile|Peru|Argentina|Internacional
    "pais_boletin": str,  # País del slot (puede diferir si es relleno)
    "score": int,
    "razon": str,
}
```

---

## Variables de entorno (.env en rpa_boletin/)

```env
ANTHROPIC_API_KEY, DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL
TO_EMAILS                  # destinatarios del boletín
TO_EMAILS_ERRORES          # destinatarios de alertas de error (separado)
EMPRESA_NOMBRE, TIMEZONE, HORA_ENVIO
REINTENTOS_MAX             # intentos por operación (1-10, default 5)
REINTENTOS_BACKOFF_BASE    # segundos base backoff (mín 1, default 2)
```

---

## Patrones importantes

- `feedparser` es **síncrono** — nunca dentro de async
- `httpx` scraping es **async** — siempre `asyncio.gather` para paralelismo
- Claude en **batches**: 20 para scoring, 15 para traducción
- `url_fuente` en el dict de noticia ≠ `url` (URL de la noticia individual)
- `pais_boletin` puede diferir de `pais` cuando se rellena con internacionales
- `registrar_en_collector=False` para errores de checkpoints de DB
- El `.lock` vive en `PROJECT_ROOT` (rpa_boletin/), no en boletin/

---

## Comandos rápidos

```bash
python main.py              # producción (scheduler)
python main.py --run-now    # test (respeta lock)
python main.py --preview    # preview.html sin enviar (sin lock)
tail -f ../logs/boletin_$(date +%Y-%m-%d).log
sudo systemctl status boletin
sudo systemctl restart boletin
```
