# Boletín Minero-Energético Automatizado 🗞️⛏️

Sistema RPA con IA que recopila, puntúa y distribuye noticias de minería y energía
de Chile, Perú y Argentina dos veces por semana.

---

## Estructura del proyecto

```
rpa_boletin/                       ← raíz del proyecto
├── .venv/                         ← entorno virtual Python
├── logs/                          ← logs diarios (un archivo por día)
│   ├── boletin_2026-04-01.log
│   ├── boletin_2026-04-03.log
│   └── boletin_2026-04-05.log
├── .env                       ← credenciales (mismo nivel que .venv)
│
└── boletin/                       ← código fuente
    ├── main.py                    # entrada: orquestador + APScheduler
    ├── scraper.py                 # RSS (feedparser) + scraping async (httpx + BS4)
    ├── scorer.py                  # scoring local + Claude IA + selección top 30
    ├── translator.py              # traducción al inglés con Claude
    ├── emailer.py                 # construcción HTML Jinja2 + envío SMTP
    ├── db.py                      # PostgreSQL: fuentes, scoring, historial, checkpoints
    ├── sources.py                 # OBSOLETO — referencia SQL, no lo lee ningún módulo
    ├── requirements.txt           # dependencias pip
    ├── .env.example               # plantilla de variables de entorno
    ├── .gitignore
    ├── README.md                  # este archivo
    ├── SKILL.md                   # documentación técnica para Claude en VS Code
    ├── MEMORY.md                  # contexto rápido para retomar el proyecto
    └── templates/
        └── boletin.html           # template Jinja2 — layout dos columnas ES|EN
```

> **Nota:** `.venv/`, `logs/` y `.env` viven en `rpa_boletin/`, al mismo nivel entre sí.
> El código fuente vive en `rpa_boletin/boletin/`.

---

## Arquitectura del pipeline

```
APScheduler (mar/jue 9am)
        │
        ▼
[1] db.get_fuentes_activas()          ← tabla: fuentes
        │
        ▼
[2] db.fuentes_pendientes_hoy()       ← tabla: ejecucion_fuentes
        │  filtra las ya completadas hoy
        ▼
[3] scraper.fetch_all()               ← RSS + scraping async
        │  registra scraping_ok por fuente
        ▼
[4] db.filtrar_enviadas()             ← tabla: noticias_enviadas
        │  descarta URLs ya incluidas
        ▼
[5] scorer.puntuar_y_seleccionar()    ← tablas: score_reglas
        │  pre-score local                       score_empresas
        │  scoring semántico Claude              score_empresas_conocidas
        │  selección top 30 (10×país)            score_keywords
        │  registra ia_ok por fuente
        ▼
[6] translator.traducir()             ← Claude API
        │  batch de 15 noticias
        ▼
[7] emailer.enviar()                  ← SMTP
        │  HTML bilingüe dos columnas
        ▼
[8] db.marcar_enviadas()              ← tabla: noticias_enviadas
    db.registrar_envio()              ← tabla: envios_log
```

---

## Tablas en PostgreSQL

| Tabla | Contenido |
|-------|-----------|
| `fuentes` | 26 fuentes activas/inactivas — reemplaza `sources.py` |
| `score_reglas` | Pesos del scoring (+250 contrato, +150 empresa conocida, etc.) |
| `score_empresas` | Empresas que suman +80 al ser mencionadas en cualquier noticia |
| `score_empresas_conocidas` | Empresas que suman +150 al **firmar** un contrato |
| `score_keywords` | Keywords de contratos/licitaciones para pre-scoring |
| `noticias_enviadas` | Historial de URLs enviadas — evita repetición |
| `ejecucion_fuentes` | Checkpoint por fuente+fecha — controla reintentos del día |
| `envios_log` | Log de cada boletín enviado con conteo por país |

---

## Instalación

```bash
# 1. Crear la carpeta raíz del proyecto
mkdir rpa_boletin && cd rpa_boletin

# 2. Clonar / copiar el código dentro de boletin/
git clone <repo> boletin
# o copiar la carpeta manualmente

# 3. Crear el entorno virtual al mismo nivel que boletin/
python3 -m venv .venv
source .venv/bin/activate       # Linux/Mac
# .venv\Scripts\activate        # Windows

# 4. Instalar dependencias
pip install -r boletin/requirements.txt

# 5. Configurar variables de entorno (en rpa_boletin/, al mismo nivel que .venv)
cp boletin/.env.example .env
nano .env

# 6. Ejecutar
cd boletin/
python main.py --preview    # preview HTML sin enviar
python main.py --run-now    # pipeline completo una vez
python main.py              # scheduler producción (mar/jue 9am)
```

---

## Variables de entorno (rpa_boletin/.env)

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# PostgreSQL
DB_HOST=192.168.1.100
DB_PORT=5432
DB_NAME=boletin_db
DB_USER=boletin_user
DB_PASSWORD=...

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=email@empresa.com
SMTP_PASSWORD=app_password
FROM_EMAIL=email@empresa.com
TO_EMAILS=dest1@empresa.com,dest2@empresa.com

# Boletín
EMPRESA_NOMBRE=Marval Chile
TIMEZONE=America/Santiago
HORA_ENVIO=9
```

---

## Comandos del día a día

```bash
# Estando dentro de rpa_boletin/boletin/

# Ver log de hoy
cat ../logs/boletin_$(date +%Y-%m-%d).log

# Seguir logs en tiempo real
tail -f ../logs/boletin_$(date +%Y-%m-%d).log

# Estado de fuentes procesadas hoy
psql $DB_NAME -c "SELECT nombre_fuente, scraping_ok, ia_ok, noticias_obtenidas
                  FROM ejecucion_fuentes WHERE fecha_ejecucion = CURRENT_DATE;"

# Limpiar historial (solo desarrollo)
psql $DB_NAME -c "DELETE FROM noticias_enviadas;"

# Últimos boletines enviados
psql $DB_NAME -c "SELECT fecha, total_noticias, chile, peru, argentina, ok
                  FROM envios_log ORDER BY fecha DESC LIMIT 10;"
```

---

## Despliegue con systemd

```ini
# /etc/systemd/system/boletin.service
[Unit]
Description=Boletín Minero-Energético
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/rpa_boletin/boletin
ExecStart=/home/ubuntu/rpa_boletin/.venv/bin/python main.py
Restart=always
RestartSec=30
EnvironmentFile=/home/ubuntu/rpa_boletin/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable boletin
sudo systemctl start boletin
sudo journalctl -u boletin -f
```
