# Guía de despliegue en producción — RPA Boletín Minero-Energético

## Índice

1. [Prerrequisitos de software](#1-prerrequisitos-de-software)
2. [Instalación del proyecto](#2-instalación-del-proyecto)
3. [Configuración del entorno (.env)](#3-configuración-del-entorno-env)
4. [Base de datos PostgreSQL](#4-base-de-datos-postgresql)
5. [FlareSolverr (opcional)](#5-flaresolverr-opcional)
6. [Primera ejecución y verificación](#6-primera-ejecución-y-verificación)
7. [Modos de ejecución](#7-modos-de-ejecución)
8. [Automatización en producción (Windows)](#8-automatización-en-producción-windows)
9. [Estructura de logs](#9-estructura-de-logs)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerrequisitos de software

### Obligatorios

| Software | Versión mínima | Para qué se usa |
|---|---|---|
| **Python** | 3.11 | Runtime del sistema |
| **PostgreSQL** | 14 | Base de datos principal |
| **Google Chrome** | cualquier versión estable reciente | Selenium + undetected-chromedriver para fuentes con anti-bot |
| **ChromeDriver** | compatible con la versión de Chrome instalada | Se instala automáticamente vía `webdriver-manager` |

### Opcionales

| Software | Para qué se usa |
|---|---|
| **FlareSolverr** | Bypass de protecciones Cloudflare en fuentes específicas (ej: Rumbo Minero) |
| **Docker** | Forma recomendada de correr FlareSolverr |

### Cuenta externa requerida

| Servicio | Variable de entorno |
|---|---|
| **Anthropic (Claude API)** | `ANTHROPIC_API_KEY` |
| **Servidor SMTP** | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` |

---

## 2. Instalación del proyecto

### 2.1 Clonar el repositorio

```bash
git clone <URL_DEL_REPOSITORIO>
cd rpa_boletin
```

### 2.2 Crear entorno virtual

```bash
python -m venv .venv
```

Activar en **Windows**:
```powershell
.venv\Scripts\activate
```

Activar en **Linux / macOS**:
```bash
source .venv/bin/activate
```

### 2.3 Instalar dependencias del proyecto

```bash
pip install -e .
```

Esto instala todas las dependencias declaradas en `pyproject.toml`:

| Librería | Versión | Propósito |
|---|---|---|
| `feedparser` | 6.0.11 | Parseo de feeds RSS |
| `httpx` | 0.27.0 | HTTP asíncrono para scraping |
| `beautifulsoup4` | 4.12.3 | Parsing HTML |
| `lxml` | 5.2.2 | Parser HTML rápido (backend de bs4) |
| `selenium` | 4.21.0 | Automatización de navegador |
| `webdriver-manager` | 4.0.1 | Gestión automática de ChromeDriver |
| `easyocr` | latest | OCR para resolución de captchas de imagen |
| `Pillow` | latest | Procesamiento de imágenes (captchas) |
| `numpy` | latest | Soporte numérico para EasyOCR |
| `psycopg2-binary` | 2.9.9 | Conector PostgreSQL |
| `anthropic` | 0.28.0 | Cliente Claude API (scoring + traducción) |
| `APScheduler` | 3.10.4 | Scheduler para ejecución automática martes/jueves |
| `Jinja2` | 3.1.4 | Renderizado de templates del boletín HTML |
| `python-dotenv` | 1.0.1 | Carga de variables de entorno desde `.env` |
| `portalocker` | latest | Lock de proceso único (evita doble ejecución) |
| `tabulate` | latest | Formateo de tablas en logs |

### 2.4 Instalar dependencias adicionales

Estas librerías se usan en el código pero no están declaradas en `pyproject.toml`:

```bash
pip install undetected-chromedriver playwright
```

Luego instalar los binarios de Playwright:

```bash
playwright install chromium
```

> **Por qué dos navegadores:** `undetected-chromedriver` evita detección anti-bot en la mayoría de sitios. Playwright entra como fallback en fuentes con login o cuando UC Chrome falla.

### 2.5 Verificar instalación

```bash
python -c "import boletin; print('OK')"
```

---

## 3. Configuración del entorno (.env)

Crear el archivo `.env` en la raíz del proyecto (mismo nivel que `pyproject.toml`).

```bash
# ── Base de datos PostgreSQL ──────────────────────────────────────────────────
DB_HOST=localhost
DB_PORT=5432
DB_NAME=boletin_db
DB_USER=boletin_user
DB_PASSWORD=tu_password_seguro

# ── Anthropic (Claude API) ────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── SMTP / Email ──────────────────────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=usuario@empresa.com
SMTP_PASSWORD=tu_app_password
FROM_EMAIL=boletin@empresa.com
TO_EMAILS=destinatario1@empresa.com,destinatario2@empresa.com
TO_EMAILS_ERRORES=admin@empresa.com

# ── Identidad del boletín ─────────────────────────────────────────────────────
EMPRESA_NOMBRE=Mi Empresa S.A.
BOLETIN_SUBTITULO_ES=Minería y Energía · Chile · Perú · Argentina
BOLETIN_SUBTITULO_EN=Mining & Energy · Chile · Peru · Argentina

# ── Scheduler ─────────────────────────────────────────────────────────────────
TIMEZONE=America/Santiago
HORA_ENVIO=9

# ── Reintentos y resiliencia ──────────────────────────────────────────────────
REINTENTOS_MAX=5
REINTENTOS_BACKOFF_BASE=2
API_IA_RATE_LIMIT_BASE=5
API_IA_RATE_LIMIT_MAX=60

# ── FlareSolverr (opcional) ───────────────────────────────────────────────────
FLARESOLVERR_URL=http://127.0.0.1:8191/v1
FLARESOLVERR_MAX_TIMEOUT_MS=180000
FLARESOLVERR_WAIT_SECONDS=8
```

### Variables obligatorias

Las siguientes variables deben estar presentes o el sistema no arranca:

- `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `ANTHROPIC_API_KEY`

### Variables con valor por defecto

El resto tiene defaults razonables y el sistema funciona sin definirlas explícitamente.

---

## 4. Base de datos PostgreSQL

### 4.1 Crear la base de datos y el usuario

Conectarse a PostgreSQL como superusuario:

```sql
CREATE DATABASE boletin_db ENCODING 'UTF8';
CREATE USER boletin_user WITH PASSWORD 'tu_password_seguro';
GRANT ALL PRIVILEGES ON DATABASE boletin_db TO boletin_user;
```

### 4.2 Inicializar schema y datos semilla

Las tablas y el seed inicial **se crean automáticamente** la primera vez que se ejecuta el sistema. No hay que correr migraciones manualmente.

Para inicializar la DB sin lanzar el pipeline completo:

**Windows PowerShell:**
```powershell
$env:PYTHONPATH = "$PWD\src"
python -c "from boletin.runtime.bootstrap import bootstrap_database; import logging; bootstrap_database(logging.getLogger())"
```

**Linux / macOS:**
```bash
PYTHONPATH=src python -c "from boletin.runtime.bootstrap import bootstrap_database; import logging; bootstrap_database(logging.getLogger())"
```

### 4.3 Tablas que se crean

| Tabla | Propósito |
|---|---|
| `fuentes` | Fuentes de noticias (URL, RSS, método, credenciales) |
| `paises` | Países activos con cuota de noticias por boletín |
| `score_reglas` | Pesos configurables del sistema de scoring |
| `score_empresas` | Empresas genéricas (+80 pts por mención) |
| `score_empresas_conocidas` | Empresas que firman contratos (+150 pts adicionales) |
| `score_empresa_tipo` | Scoring diferenciado por tipo de empresa (minera +200, EPC +180, energía +160…) |
| `score_keywords` | Keywords de contratos/licitaciones (+250 pts) |
| `ejecucion_fuentes` | Registro de estado diario por fuente |
| `articulos_pendientes` | Artículos scrapeados pendientes de scoring IA |
| `noticias_enviadas` | Historial de URLs enviadas (evita re-envíos) |
| `envios_log` | Registro de boletines enviados |
| `procesos_programados` | Control de procesos periódicos |

---

## 5. FlareSolverr (opcional)

FlareSolverr permite acceder a sitios protegidos por Cloudflare. Solo es necesario si fuentes como **Rumbo Minero** están habilitadas.

### Instalar con Docker

```bash
docker run -d \
  --name flaresolverr \
  -p 8191:8191 \
  -e LOG_LEVEL=info \
  --restart unless-stopped \
  ghcr.io/flaresolverr/flaresolverr:latest
```

### Verificar que funciona

```bash
curl -s http://localhost:8191/v1 | python -m json.tool
```

Debe devolver `{"status": "ok", ...}`.

Configurar en `.env`:
```
FLARESOLVERR_URL=http://127.0.0.1:8191/v1
```

Si no se configura FlareSolverr, las fuentes que lo requieren simplemente fallan con fallback a Selenium.

---

## 6. Primera ejecución y verificación

### 6.1 Ejecutar en modo preview

El modo preview corre el pipeline completo (scraping + scoring + traducción) **sin enviar el email**. Genera un HTML en `tests/output/previews/preview.html`.

**Windows PowerShell:**
```powershell
$env:PYTHONPATH = "$PWD\src"
python -m boletin.main --preview
```

**Linux / macOS:**
```bash
PYTHONPATH=src python -m boletin.main --preview
```

Abrir `tests/output/previews/preview.html` en el navegador para verificar el resultado.

### 6.2 Ejecutar una vez completo

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m boletin.main --run-now
```

O usando el batch de Windows:
```
ejecutar_boletin.bat
```

### 6.3 Verificar que todo funciona

Revisar el log del día en `logs/boletin_YYYY-MM-DD.log`. El pipeline exitoso termina con:

```
Pipeline finalizado en XX.X segundos
Boletín enviado exitosamente (N noticias)
```

---

## 7. Modos de ejecución

| Comando | Comportamiento |
|---|---|
| `python -m boletin.main` | Arranca el scheduler permanente. Ejecuta el pipeline automáticamente los **martes y jueves a las `HORA_ENVIO`:00**. Proceso bloqueante. |
| `python -m boletin.main --run-now` | Ejecuta el pipeline **una sola vez** inmediatamente. |
| `python -m boletin.main --preview` | Ejecuta el pipeline sin enviar email. Genera `tests/output/previews/preview.html`. |
| `ejecutar_boletin.bat` | Equivalente a `--run-now` en Windows, activa el venv automáticamente. |

### Lógica de fecha efectiva

El sistema no siempre procesa la fecha del día. Si se ejecuta fuera de martes o jueves, compensa hacia la última fecha de boletín:

- **Martes / Jueves** → procesa el día actual
- **Miércoles** → compensa el martes anterior
- **Lunes / Viernes / Sábado / Domingo** → compensa el jueves anterior

Si esa fecha ya fue procesada completamente, el pipeline no hace nada.

---

## 8. Automatización en producción (Windows)

La forma recomendada en Windows es usar el **Programador de tareas** para mantener el scheduler corriendo como proceso en background.

### Opción A — Scheduler permanente con Task Scheduler

Crear una tarea que arranque el proceso al inicio del sistema:

1. Abrir **Programador de tareas** → Crear tarea básica
2. **Nombre:** `Boletin RPA Scheduler`
3. **Desencadenador:** Al iniciar el sistema (o al iniciar sesión)
4. **Acción:** Iniciar un programa
   - **Programa:** `C:\ruta\al\proyecto\.venv\Scripts\python.exe`
   - **Argumentos:** `-m boletin.main`
   - **Iniciar en:** `C:\ruta\al\proyecto\`
5. **Configuración:**
   - Marcar "Ejecutar tanto si el usuario inició sesión como si no"
   - Marcar "Ejecutar con los privilegios más altos"

**Variables de entorno:** Como el `.env` se carga automáticamente, no es necesario configurarlas en la tarea.

**PYTHONPATH:** Agregar en las variables de entorno de la tarea:
```
PYTHONPATH = C:\ruta\al\proyecto\src
```

### Opción B — Ejecución puntual con Task Scheduler

Si preferís no mantener un proceso permanente, crear dos tareas (una para martes, otra para jueves):

- **Desencadenador:** Semanalmente → martes (o jueves) → a las `08:55` (5 minutos antes para dar margen)
- **Acción:** Ejecutar `ejecutar_boletin.bat`

---

## 9. Estructura de logs

Los logs se generan automáticamente en `logs/boletin_YYYY-MM-DD.log`.

```
logs/
└── boletin_2025-04-24.log
└── boletin_2025-04-22.log
└── ...
```

Cada línea tiene el formato:
```
2025-04-24 09:01:23,456 [INFO] pipeline_service — INICIO DEL PIPELINE
```

Los archivos de log se acumulan. Para limpiar logs viejos podés borrar manualmente los archivos con más de N días, o agregar una tarea programada para eso.

---

## 10. Troubleshooting

### La DB no conecta

```
Error de conexión PostgreSQL: ...
```

Verificar:
- Que PostgreSQL esté corriendo (`pg_isready -h localhost`)
- Que las variables `DB_*` en `.env` sean correctas
- Que el usuario tenga permisos sobre la base de datos

### Chrome no arranca (Selenium)

```
WebDriverException: 'chromedriver' executable needs to be in PATH
```

Verificar:
- Que Google Chrome esté instalado
- Ejecutar `pip install --upgrade webdriver-manager` para actualizar el driver

### Error de API de Anthropic

```
AuthenticationError: invalid_api_key
```

Verificar que `ANTHROPIC_API_KEY` en `.env` sea válida y tenga saldo disponible.

### El pipeline ya está corriendo (lock)

```
Pipeline ya está en ejecución. Saliendo sin error.
```

Esto es comportamiento normal si el proceso anterior no terminó. Si estás seguro de que no hay otro proceso corriendo:

```bash
# Windows
del .lock

# Linux/macOS
rm .lock
```

### No se envía el email

Verificar en el log que el paso 5 no tenga errores SMTP. Para Gmail, usar una **App Password** (no la contraseña de la cuenta). Habilitar autenticación de dos factores en la cuenta Google y generar la app password desde [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

### La tabla `score_empresa_tipo` no existe

Si la DB fue creada antes de esta tabla, ejecutar:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -c "from boletin.infrastructure.db import facade as db; db.init_db()"
```

El `CREATE TABLE IF NOT EXISTS` la crea automáticamente sin afectar las tablas existentes.
