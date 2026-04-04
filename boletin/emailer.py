"""
emailer.py
----------
Construye el HTML del boletín con Jinja2 y lo envía por email.
Soporta SMTP genérico (Gmail, Outlook, cualquier proveedor) y SendGrid.
Envía dos correos: uno en español y uno en inglés.
"""

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# CONTRACT_KEYWORDS se carga desde la DB via db.get_score_config()

import db
import retrier
from retrier import TipoError
from dotenv import load_dotenv

# .env vive en rpa_boletin/ — dos niveles arriba de emailer.py
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
# Las banderas ahora vienen del campo `bandera` en la tabla `paises` de la DB

# ── Config desde variables de entorno — NINGÚN valor hardcodeado ─────────────
SMTP_HOST     = os.environ["SMTP_HOST"]
SMTP_PORT     = int(os.environ["SMTP_PORT"])
SMTP_USER     = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
FROM_EMAIL    = os.environ["FROM_EMAIL"]
TO_EMAILS     = [e.strip() for e in os.environ["TO_EMAILS"].split(",") if e.strip()]

# Datos de identidad del boletín (configurables por entorno)
EMPRESA_NOMBRE = os.getenv("EMPRESA_NOMBRE", "Empresa")

# Destinatarios de errores — viene de retrier (TO_EMAILS_ERRORES en .env)
TO_EMAILS_ERRORES = retrier.TO_EMAILS_ERRORES
BOLETIN_SUBTITULO_ES = os.getenv("BOLETIN_SUBTITULO_ES", "Minería y Energía · Chile · Perú · Argentina")
BOLETIN_SUBTITULO_EN = os.getenv("BOLETIN_SUBTITULO_EN", "Mining & Energy · Chile · Peru · Argentina")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_fecha(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y")
    except Exception:
        return iso_str


def _es_contrato(noticia: dict) -> bool:
    """Detecta si la noticia es sobre un contrato usando keywords de la DB."""
    cfg = db.get_score_config()
    keywords = cfg.get("keywords", [])
    texto = (noticia["titulo"] + " " + noticia["resumen"]).lower()
    return any(kw.lower() in texto for kw in keywords)


def _agrupar_por_pais(noticias: list[dict]) -> dict:
    """
    Organiza noticias en secciones por país para el template.
    Lee los países activos y su orden desde la DB — completamente dinámico.
    Si se agrega un país en la tabla `paises`, aparece automáticamente en el boletín.
    """
    paises = db.get_paises_activos()
    nombres = {p["nombre"] for p in paises}

    grupos = {
        p["nombre"]: {
            "flag":     p["bandera"],
            "nombre_en": p["nombre_en"],
            "noticias": [],
        }
        for p in paises
    }

    for n in noticias:
        pais = n.get("pais_boletin", n["pais"])
        if pais not in grupos:
            # Fuente internacional asignada al primer país activo como fallback
            pais = paises[0]["nombre"] if paises else list(grupos.keys())[0]

        entrada = dict(n)
        entrada["fecha_fmt"]   = _fmt_fecha(n["fecha"])
        entrada["es_contrato"] = _es_contrato(n)
        grupos[pais]["noticias"].append(entrada)

    return grupos


def _render_html(noticias: list[dict]) -> str:
    """Renderiza el boletín HTML bilingüe (ES + EN en dos columnas)."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("boletin.html")

    ahora     = datetime.now(tz=timezone.utc)
    fecha_hoy = ahora.strftime("%d/%m/%Y")

    fechas_validas = []
    for n in noticias:
        try:
            fechas_validas.append(datetime.fromisoformat(n["fecha"]))
        except Exception:
            pass
    fecha_desde = min(fechas_validas).strftime("%d/%m/%Y") if fechas_validas else fecha_hoy

    return template.render(
        empresa=EMPRESA_NOMBRE,
        subtitulo_es=BOLETIN_SUBTITULO_ES,
        subtitulo_en=BOLETIN_SUBTITULO_EN,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hoy,
        total_noticias=len(noticias),
        paises=_agrupar_por_pais(noticias),
    )


# ── Envío SMTP ─────────────────────────────────────────────────────────────────

def _send_smtp(subject: str, html_body: str, recipients: list[str]) -> bool:
    """Envía un email con hasta MAX_INTENTOS reintentos y backoff exponencial."""
    if not recipients:
        logger.error("No hay destinatarios configurados (TO_EMAILS vacio)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = FROM_EMAIL
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw_msg = msg.as_string()

    def _enviar():
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, recipients, raw_msg)

    _, ok = retrier.con_reintentos(
        fn=_enviar,
        tipo_error=TipoError.ENVIO_EMAIL,
        fuente="SMTP",
        url=SMTP_HOST,
    )
    if ok:
        logger.info("Email enviado a %s", recipients)
    else:
        logger.error("No se pudo enviar email a %s despues de todos los reintentos", recipients)
    return ok


# ── Entrada pública ───────────────────────────────────────────────────────────

def enviar(noticias: list[dict]) -> bool:
    """
    Envía dos correos:
      1. Boletín en español
      2. Boletín en inglés (copia traducida)
    Retorna True si ambos envíos fueron exitosos.
    """
    ahora   = datetime.now(tz=timezone.utc)
    fecha   = ahora.strftime("%d/%m/%Y")

    # Un único HTML bilingüe (dos columnas ES + EN)
    html = _render_html(noticias)
    ok   = _send_smtp(
        subject=f"Boletín Minero-Energético · Mining & Energy — {fecha}",
        html_body=html,
        recipients=TO_EMAILS,
    )
    return ok


def preview_html(noticias: list[dict], output_path: str = "preview.html") -> None:
    """Guarda el HTML del boletín en disco para previsualización local."""
    html = _render_html(noticias)
    Path(output_path).write_text(html, encoding="utf-8")
    logger.info("Preview guardado en %s", output_path)
