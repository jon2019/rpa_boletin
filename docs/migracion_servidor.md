# Plan de migración a disco/servidor nuevo

## En el disco/servidor ACTUAL (preparación)

1. **Confirmar que el código está commiteado** — ya hecho (385dfc8 reestructuración + 8147607 fix de dependencias). Si hiciste cambios después, commitealos antes de copiar.
2. **Dump de la base de datos** — ya generado: `backups/rpa_boletin_full_20260612_120815.sql`. Si pasa tiempo entre esto y la migración real, regenerá el dump justo antes de copiar (`python -m scripts.db.dump_full_db`) para llevarte los datos más recientes.

## Copiar al disco/servidor NUEVO

Copiar la carpeta del proyecto completa, **EXCLUYENDO**:

- `.venv/` (se recrea)
- `__pycache__/` (todas, incluidas las de `src/`)
- `logs/`
- `flaresolverr_heartbeat.json`
- `.claude/`

Copiar **SÍ o SÍ** (no están en git):

- `.env`
- `.scraper_profile/` (sesiones de login — sin esto hay que re-loguear Portal Minero / BNamericas App)
- `backups/rpa_boletin_full_*.sql` (el dump)
- `scripts/db/` (script de dump, está untracked en git)

## Configurar el entorno nuevo

### Recrear el venv

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### Instalar binarios de Playwright (no viajan con la copia de archivos)

```bash
playwright install chromium
```

### Instalar Google Chrome

Selenium y undetected-chromedriver (usados como fallback de scraping para Mining.com, Mining Digital, Mining Weekly y Rumbo Minero) requieren **Google Chrome instalado como navegador del sistema** — esto es distinto del Chromium que instala Playwright, que solo lo usa Playwright.

Descargar e instalar desde https://www.google.com/chrome/ con la instalación estándar (se detecta automáticamente en `C:\Program Files\Google\Chrome\Application\chrome.exe`).

Sin esto, esas 4 fuentes fallan con "browser required (JS rendering)" o caen en 403 Forbidden sin que el fallback funcione.

### Instalar FlareSolverr

El proyecto usa FlareSolverr como ejecutable standalone en Windows (no Docker), apuntado por `FLARESOLVERR_EXE_PATH` (default `C:\tools\flaresolverr\flaresolverr.exe`).

1. Descargar desde https://github.com/FlareSolverr/FlareSolverr/releases — el asset `flaresolverr_windows_x64.zip` (o el build más reciente para Windows x64) de la última versión.
2. Descomprimir en `C:\tools\flaresolverr\` en la máquina nueva.
3. Si en la máquina nueva se prefiere otra carpeta, setear `FLARESOLVERR_EXE_PATH` en el `.env` apuntando a la ruta correcta.

### Revisar .env

- Si hay rutas absolutas al disco/carpeta vieja, actualizarlas.
- `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` → apuntar a la Postgres del entorno nuevo (sea local o remota).
- `FLARESOLVERR_EXE_PATH` → confirmar que `C:\tools\flaresolverr\flaresolverr.exe` exista en la máquina nueva, o setear la ruta correcta en `.env`.

## Restaurar la base de datos

Crear la base vacía en la Postgres destino:

```bash
createdb -U <usuario> <nombre_db>
```

Restaurar el dump:

```bash
psql -U <usuario> -d <nombre_db> -f backups\rpa_boletin_full_20260612_120815.sql
```

## Verificación final

1. **Probar conexión a la BD** — corré `ejecutar_boletin.bat` y verificá en el log que diga "Conexión PostgreSQL OK" y "PostgreSQL schema OK".
2. **Verificar que `.scraper_profile/` funciona** — corré un scraping de una fuente con login (Portal Minero o BNamericas App) y confirmá que NO pide re-login.
3. **Verificar FlareSolverr** — confirmá que arranca correctamente (revisá que `flaresolverr_heartbeat.json` se regenera).
4. **Correr el pipeline completo una vez** (`--run-now`) y revisar el log del día completo, sin errores de imports ni de conexión.

## Diagnóstico: carpeta bloqueada por proceso colgado

Si al intentar eliminar o mover la carpeta del proyecto Windows dice "Carpeta en uso", usá `handle64.exe` (incluido en `handle\handle64.exe`) para identificar qué proceso la tiene tomada:

```bat
handle\handle64.exe "E:\productivo_python_rpa\rpa_boletin"
```

La salida muestra el nombre del proceso, su PID y el archivo bloqueado. Ejemplo:

```
flaresolverr.exe  pid: 26160  type: File  48: E:\productivo_python_rpa\rpa_boletin
```

Una vez identificado el PID, matá únicamente ese proceso:

```bat
taskkill /F /PID 26160
```

**No usar** `taskkill /IM chrome.exe` ni `taskkill /IM chromedriver.exe` sin PID específico — eso mata los Chrome de todos los otros RPAs activos en el servidor.

## Opcional

- Si había alguna tarea programada (Task Scheduler) apuntando al `.bat` del disco viejo, recrearla con la ruta nueva.
- Si vas a seguir usando el repo de GitHub desde la ubicación nueva, hacé `git push` antes de migrar para que el remoto tenga el último estado.
