"""
emailer.py
----------
Construye el HTML del boletín con Jinja2 y lo envía por email.
Soporta SMTP genérico (Gmail, Outlook, cualquier proveedor).
Envía dos correos: uno en español y uno en inglés.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from boletin.infrastructure.db import facade as db
from boletin.infrastructure.resilience import retrier
from boletin.config.environment import (
    get_bulletin_identity_settings,
    get_smtp_settings,
)
from boletin.config.settings import TEMPLATES_DIR
from boletin.infrastructure.resilience.retrier import TipoError

logger = logging.getLogger(__name__)

TEMPLATE_DIR = TEMPLATES_DIR


def _fmt_fecha(iso_str: str) -> str:
    """
    Formatea una fecha ISO 8601 a formato legible: "15 Dic 2023".
    Si falla el parsing, retorna la cadena original.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y")
    except Exception:
        return iso_str


def _es_contrato(noticia: dict) -> bool:
    """
    Detecta si la noticia es sobre un contrato del sector minero-energético.
    Prioriza la decisión explícita de Claude (campo es_contrato en _criterios).
    Solo cae al keyword match si la noticia no fue evaluada por Claude.
    """
    criterios = noticia.get("_criterios", {})
    if criterios.get("fuente") == "claude":
        return bool(noticia.get("es_contrato", False))

    # Fallback para artículos que no llegaron al top-60 de Claude
    cfg = db.get_score_config()
    keywords        = cfg.get("keywords", [])
    sector_contexto = cfg.get("sector_contexto", [])
    texto = ((noticia.get("titulo") or "") + " " + (noticia.get("resumen") or "")).lower()
    return (
        any(kw.lower() in texto for kw in keywords)
        and any(term.lower() in texto for term in sector_contexto)
    )


def _agrupar_por_pais(noticias: list[dict]) -> dict:
    """
    Organiza noticias en secciones por país para el template.
    Lee los países activos y su orden desde la DB de forma dinámica.
    """
    paises = db.get_paises_activos()

    grupos = {
        pais["nombre"]: {
            "flag": pais["bandera"],
            "nombre_en": pais["nombre_en"],
            "noticias": [],
        }
        for pais in paises
    }

    for noticia in noticias:
        pais = noticia.get("pais_boletin", noticia["pais"])
        if pais not in grupos:
            pais = paises[0]["nombre"] if paises else list(grupos.keys())[0]

        entrada = dict(noticia)
        entrada["fecha_fmt"] = _fmt_fecha(noticia["fecha"])
        entrada["es_contrato"] = _es_contrato(noticia)
        grupos[pais]["noticias"].append(entrada)

    return grupos


def _render_html(noticias: list[dict], failed_sources: list[dict], resumen=None) -> str:
    """Renderiza el boletín HTML bilingüe (ES + EN en dos columnas)."""
    identidad = get_bulletin_identity_settings()
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("boletin.html")

    ahora = datetime.now(tz=timezone.utc)
    fecha_hoy = ahora.strftime("%d/%m/%Y")

    fechas_validas = []
    for noticia in noticias:
        try:
            fechas_validas.append(datetime.fromisoformat(noticia["fecha"]))
        except Exception:
            pass
    fecha_desde = min(fechas_validas).strftime("%d/%m/%Y") if fechas_validas else fecha_hoy

    return template.render(
        empresa=identidad.empresa_nombre,
        subtitulo_es=identidad.subtitulo_es,
        subtitulo_en=identidad.subtitulo_en,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hoy,
        total_noticias=len(noticias),
        paises=_agrupar_por_pais(noticias),
        failed_sources=failed_sources,
        resumen=resumen,
    )


def _send_smtp(subject: str, html_body: str, recipients: list[str]) -> bool:
    """Envía un email con reintentos y backoff controlados por `retrier`."""
    smtp = get_smtp_settings()
    if not recipients:
        logger.error("No hay destinatarios configurados (TO_EMAILS vacío).")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp.from_email
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw_msg = msg.as_string()

    def _enviar():
        with smtplib.SMTP(smtp.host, smtp.port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp.user, smtp.password)
            server.sendmail(smtp.from_email, recipients, raw_msg)

    _, ok, _ = retrier.con_reintentos(
        fn=_enviar,
        tipo_error=TipoError.ENVIO_EMAIL,
        fuente="SMTP",
        url=smtp.host,
    )
    if ok:
        logger.info("Email enviado a %s", recipients)
    else:
        logger.error("No se pudo enviar email a %s después de todos los reintentos", recipients)
    return ok


def enviar(
    noticias: list[dict],
    failed_sources: list[dict] | None = None,
    fecha_efectiva=None,
) -> bool:
    """
    Envía el boletín bilingüe a los destinatarios configurados.
    Retorna True si el envío fue exitoso.
    """
    smtp = get_smtp_settings()
    ahora = datetime.now(tz=timezone.utc)
    fecha = ahora.strftime("%d/%m/%Y")

    resumen = None
    if fecha_efectiva is not None:
        try:
            resumen = db.resumen_ejecucion_hoy(fecha_efectiva)
        except Exception as exc:
            logger.warning("No se pudo obtener resumen de ejecución para el email: %s", exc)

    html = _render_html(noticias, failed_sources or [], resumen=resumen)
    return _send_smtp(
        subject=f"Boletín Minero-Energético · Mining & Energy — {fecha}",
        html_body=html,
        recipients=smtp.to_emails,
    )


def preview_html(
    noticias: list[dict],
    output_path: str = "tests/output/previews/preview.html",
) -> None:
    """Guarda el HTML del boletín en disco para previsualización local."""
    html = _render_html(noticias, [])
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    logger.info("Preview guardado en %s", output_path)


def enviar_alerta_fuentes_operativas(problemas: list[dict], fecha_efectiva=None) -> bool:
    """
    Envía un correo operativo con fuentes activas que quedaron fuera del ciclo
    por configuración incompleta. Prioriza TO_EMAILS_ERRORES y luego TO_EMAILS.
    """
    smtp = get_smtp_settings()
    if not problemas:
        logger.info("Sin problemas operativos de fuentes para reportar por email.")
        return True

    recipients = smtp.error_to_emails or smtp.to_emails
    if not recipients:
        logger.warning("No hay destinatarios configurados para alertas operativas de fuentes.")
        return False

    fecha_envio = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    fecha_ref = str(fecha_efectiva) if fecha_efectiva is not None else "sin fecha efectiva"

    rows = []
    for idx, problema in enumerate(problemas, start=1):
        bg = "#ffffff" if idx % 2 else "#f5f5f5"
        rows.append(
            f"""
            <tr style="background: {bg}; color: #1a1a1a;">
              <td style="padding: 8px; border: 1px solid #dcdcdc;">{problema.get('country', '-')}</td>
              <td style="padding: 8px; border: 1px solid #dcdcdc;">{problema.get('name', '-')}</td>
              <td style="padding: 8px; border: 1px solid #dcdcdc;">
                <a href="{problema.get('url', '#')}" style="color:#1a5c2a;" target="_blank">{problema.get('url', '-')}</a>
              </td>
              <td style="padding: 8px; border: 1px solid #dcdcdc;">{problema.get('problema', 'Problema no informado')}</td>
            </tr>
            """
        )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background:#f3f3f3; color:#1a1a1a; padding:24px;">
        <div style="max-width:960px; margin:0 auto; background:#ffffff; border:1px solid #d8d8d8; border-radius:8px; overflow:hidden;">
          <div style="background:#8a1f11; color:#ffffff; padding:16px 20px;">
            <h2 style="margin:0; font-size:20px;">Fuentes activas con problema operativo</h2>
            <div style="margin-top:6px; font-size:12px;">Fecha efectiva: {fecha_ref} · Generado: {fecha_envio}</div>
          </div>
          <div style="padding:20px;">
            <p style="margin:0 0 14px 0; font-size:14px;">
              Se detectaron <strong>{len(problemas)}</strong> fuente(s) activas que hoy no pueden entrar al ciclo operativo.
            </p>
            <table style="width:100%; border-collapse:collapse; font-size:12px;">
              <thead>
                <tr style="background:#1a5c2a; color:#ffffff;">
                  <th style="padding:8px; border:1px solid #dcdcdc; text-align:left;">País</th>
                  <th style="padding:8px; border:1px solid #dcdcdc; text-align:left;">Fuente</th>
                  <th style="padding:8px; border:1px solid #dcdcdc; text-align:left;">URL</th>
                  <th style="padding:8px; border:1px solid #dcdcdc; text-align:left;">Problema encontrado</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>
        </div>
      </body>
    </html>
    """
    return _send_smtp(
        subject=f"RPA Boletín — Fuentes con problema operativo — {fecha_envio}",
        html_body=html,
        recipients=recipients,
    )
