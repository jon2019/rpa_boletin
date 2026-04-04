"""
retrier.py
----------
Módulo centralizado de control de errores y reintentos.

Lógica:
  - Hasta MAX_INTENTOS intentos por operación (configurable con REINTENTOS_MAX en .env, default 5).
  - Backoff exponencial configurable con REINTENTOS_BACKOFF_BASE en .env (default 2s): 2s, 4s, 8s, 16s...
  - Clasifica cada fallo en uno de cuatro tipos:
      URL_NO_DISPONIBLE  — HTTP 4xx/5xx o timeout de red
      API_IA             — error en llamada a Claude (Anthropic)
      BASE_DE_DATOS      — fallo en operación PostgreSQL
      ENVIO_EMAIL        — fallo en SMTP

  - Errores dentro del flujo de fuentes se acumulan en un ErrorCollector
    y se envían en un único correo resumen al finalizar todas las iteraciones.
  - Errores críticos de DB al inicio del programa cierran el proceso y envían
    un correo a TO_EMAILS_ERRORES (variable de entorno separada).
"""

import logging
import os
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Configuración desde variables de entorno ──────────────────────────────────
# REINTENTOS_MAX:  cantidad de intentos antes de rendirse (default: 5, mín: 1, máx: 10)
# REINTENTOS_BACKOFF_BASE: segundos base del backoff exponencial (default: 2)
#   Ejemplo con MAX=5 y BASE=2: esperas de 2s, 4s, 8s, 16s entre intentos
MAX_INTENTOS  = max(1, min(10, int(os.getenv("REINTENTOS_MAX", 5))))
BACKOFF_BASE  = max(1, int(os.getenv("REINTENTOS_BACKOFF_BASE", 2)))

SMTP_HOST     = os.getenv("SMTP_HOST", "")
SMTP_PORT     = int(os.getenv("SMTP_PORT", 587))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL    = os.getenv("FROM_EMAIL", "")

# Destinatarios de errores críticos — variable separada de TO_EMAILS
TO_EMAILS_ERRORES = [
    e.strip() for e in os.getenv("TO_EMAILS_ERRORES", "").split(",") if e.strip()
]


# ── Tipos de error ────────────────────────────────────────────────────────────

class TipoError(str, Enum):
    URL_NO_DISPONIBLE = "URL / Página no disponible"
    API_IA            = "API Inteligencia Artificial"
    BASE_DE_DATOS     = "Base de datos"
    ENVIO_EMAIL       = "Envío de email"


@dataclass
class ErrorFuente:
    """Registro de un error ocurrido durante el procesamiento de una fuente."""
    fuente:     str
    url:        str
    tipo:       TipoError
    mensaje:    str
    intentos:   int
    timestamp:  str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


# ── Colector de errores ───────────────────────────────────────────────────────

class ErrorCollector:
    """
    Acumula errores durante las iteraciones de fuentes.
    Al finalizar todas las iteraciones, enviar_resumen() despacha
    un único email con todos los errores agrupados por tipo.
    """

    def __init__(self):
        self._errores: list[ErrorFuente] = []

    def registrar(self, fuente: str, url: str, tipo: TipoError,
                  mensaje: str, intentos: int) -> None:
        err = ErrorFuente(fuente=fuente, url=url, tipo=tipo,
                          mensaje=mensaje, intentos=intentos)
        self._errores.append(err)
        logger.warning(
            "[%s] %s — %s (después de %d intentos): %s",
            tipo.value, fuente, url, intentos, mensaje[:200],
        )

    def hay_errores(self) -> bool:
        return bool(self._errores)

    def total(self) -> int:
        return len(self._errores)

    def enviar_resumen(self) -> None:
        """Envía un único email con todos los errores agrupados por tipo."""
        if not self._errores:
            logger.info("Sin errores que reportar.")
            return
        if not TO_EMAILS_ERRORES:
            logger.warning("TO_EMAILS_ERRORES no configurado — resumen de errores no enviado.")
            return

        fecha = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        html  = _render_resumen_errores(self._errores, fecha)

        _enviar_email_raw(
            subject=f"⚠️ RPA Boletín — {len(self._errores)} error(es) detectado(s) — {fecha}",
            html_body=html,
            recipients=TO_EMAILS_ERRORES,
        )
        logger.info("Resumen de errores enviado a %s (%d errores)",
                    TO_EMAILS_ERRORES, len(self._errores))


# Instancia global — se resetea al inicio de cada pipeline
_collector = ErrorCollector()


def get_collector() -> ErrorCollector:
    return _collector


def reset_collector() -> None:
    global _collector
    _collector = ErrorCollector()


# ── Motor de reintentos ───────────────────────────────────────────────────────

def con_reintentos(
    fn: Callable[[], T],
    tipo_error: TipoError,
    fuente: str = "",
    url: str = "",
    max_intentos: int = MAX_INTENTOS,
    registrar_en_collector: bool = True,
) -> tuple[T | None, bool]:
    """
    Ejecuta fn() con hasta max_intentos reintentos y backoff exponencial.

    Retorna (resultado, exito):
      - Si algún intento tiene éxito → (resultado, True)
      - Si todos fallan → (None, False)
        y registra el error en el ErrorCollector si registrar_en_collector=True.

    Backoff: 2s → 4s → 8s → 16s entre intentos.
    """
    ultimo_error = ""
    for intento in range(1, max_intentos + 1):
        try:
            resultado = fn()
            if intento > 1:
                logger.info("✅ Éxito en intento %d/%d — %s", intento, max_intentos, fuente or url)
            return resultado, True

        except Exception as e:
            ultimo_error = str(e)
            if intento < max_intentos:
                espera = BACKOFF_BASE ** intento
                logger.warning(
                    "⚠️  Intento %d/%d fallido — %s: %s. Reintentando en %ds...",
                    intento, max_intentos, fuente or url, ultimo_error[:150], espera,
                )
                time.sleep(espera)
            else:
                logger.error(
                    "❌ Todos los intentos agotados (%d/%d) — %s: %s",
                    intento, max_intentos, fuente or url, ultimo_error[:200],
                )

    if registrar_en_collector:
        _collector.registrar(
            fuente=fuente,
            url=url,
            tipo=tipo_error,
            mensaje=ultimo_error,
            intentos=max_intentos,
        )

    return None, False


# ── Errores críticos de inicio ────────────────────────────────────────────────

def error_critico(mensaje: str, detalle: str = "") -> None:
    """
    Envía un email de error crítico a TO_EMAILS_ERRORES y cierra el proceso.
    Se usa exclusivamente para fallos en la conexión/inicialización de la DB
    al arrancar el programa — antes de que el pipeline pueda continuar.
    """
    logger.critical("ERROR CRÍTICO: %s — %s", mensaje, detalle)

    if TO_EMAILS_ERRORES:
        fecha = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        html  = _render_error_critico(mensaje, detalle, fecha)
        _enviar_email_raw(
            subject=f"🚨 RPA Boletín — ERROR CRÍTICO — {fecha}",
            html_body=html,
            recipients=TO_EMAILS_ERRORES,
        )
    else:
        logger.error("TO_EMAILS_ERRORES no configurado — email de error crítico no enviado.")


# ── Envío de email sin reintentos (para notificaciones de error) ──────────────

def _enviar_email_raw(subject: str, html_body: str, recipients: list[str]) -> bool:
    """
    Envío SMTP directo para emails de error.
    Usa 3 intentos (no 5, para no bloquear indefinidamente ante un fallo de SMTP).
    """
    if not recipients or not SMTP_HOST:
        logger.warning("SMTP no configurado o sin destinatarios de error.")
        return False

    for intento in range(1, 4):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = FROM_EMAIL
            msg["To"]      = ", ".join(recipients)
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, recipients, msg.as_string())

            logger.info("Email de error enviado a %s", recipients)
            return True

        except Exception as e:
            if intento < 3:
                time.sleep(BACKOFF_BASE ** intento)
            else:
                logger.error("No se pudo enviar email de error: %s", e)

    return False


# ── Templates HTML para emails de error ──────────────────────────────────────

def _render_error_critico(mensaje: str, detalle: str, fecha: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body{{font-family:Arial,sans-serif;background:#1a1a1a;color:#e0e0e0;padding:0;margin:0}}
  .wrap{{max-width:700px;margin:0 auto;background:#1e1e1e}}
  .header{{background:#B71C1C;padding:28px 32px}}
  .header h1{{color:#fff;font-size:22px;margin:0}}
  .header p{{color:#ffcdd2;font-size:13px;margin:6px 0 0}}
  .body{{padding:28px 32px}}
  .box{{background:#2a1a1a;border:1px solid #B71C1C;border-radius:6px;
        padding:16px 20px;margin:16px 0}}
  .label{{font-size:11px;text-transform:uppercase;letter-spacing:1px;
          color:#ef9a9a;font-weight:600;margin-bottom:6px}}
  .value{{font-size:14px;color:#ffcdd2;word-break:break-all}}
  .footer{{padding:20px 32px;border-top:1px solid #333;
           font-size:11px;color:#555}}
</style></head><body><div class="wrap">
  <div class="header">
    <h1>🚨 Error Crítico — RPA Boletín Minero-Energético</h1>
    <p>{fecha}</p>
  </div>
  <div class="body">
    <p style="color:#ef9a9a;font-size:15px;margin-bottom:20px">
      El sistema encontró un error crítico al iniciar y <strong>no puede continuar</strong>.
      Se requiere intervención manual.
    </p>
    <div class="box">
      <div class="label">Causa del error</div>
      <div class="value">{mensaje}</div>
    </div>
    {"" if not detalle else f'<div class="box"><div class="label">Detalle técnico</div><div class="value">{detalle}</div></div>'}
    <div class="box">
      <div class="label">Acciones recomendadas</div>
      <div class="value">
        1. Verificar que PostgreSQL está activo y accesible<br>
        2. Revisar las variables DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD en .env<br>
        3. Revisar el log del día: rpa_boletin/logs/boletin_YYYY-MM-DD.log<br>
        4. Reiniciar el servicio: sudo systemctl restart boletin
      </div>
    </div>
  </div>
  <div class="footer">RPA Boletín Minero-Energético · Sistema de alertas automáticas</div>
</div></body></html>"""


def _render_resumen_errores(errores: list[ErrorFuente], fecha: str) -> str:
    # Agrupa por tipo
    por_tipo: dict[TipoError, list[ErrorFuente]] = {}
    for e in errores:
        por_tipo.setdefault(e.tipo, []).append(e)

    colores = {
        TipoError.URL_NO_DISPONIBLE: ("#E65100", "#FBE9E7"),
        TipoError.API_IA:            ("#4A148C", "#F3E5F5"),
        TipoError.BASE_DE_DATOS:     ("#B71C1C", "#FFEBEE"),
        TipoError.ENVIO_EMAIL:       ("#1A237E", "#E8EAF6"),
    }

    secciones = ""
    for tipo, lista in por_tipo.items():
        color_header, color_bg = colores.get(tipo, ("#333", "#f5f5f5"))
        filas = ""
        for err in lista:
            filas += f"""
            <tr style="border-bottom:1px solid #ddd">
              <td style="padding:8px 12px;font-size:13px;color:#333">{err.fuente}</td>
              <td style="padding:8px 12px;font-size:12px;color:#666;word-break:break-all">{err.url}</td>
              <td style="padding:8px 12px;font-size:12px;color:#555">{err.mensaje[:200]}</td>
              <td style="padding:8px 12px;font-size:12px;color:#666;text-align:center">{err.intentos}</td>
              <td style="padding:8px 12px;font-size:11px;color:#888">{err.timestamp[11:19]} UTC</td>
            </tr>"""

        secciones += f"""
        <div style="margin-bottom:24px;border-radius:6px;overflow:hidden;
                    border:1px solid {color_header}">
          <div style="background:{color_header};padding:12px 16px">
            <span style="color:#fff;font-size:13px;font-weight:bold">
              {tipo.value} — {len(lista)} error(es)
            </span>
          </div>
          <table style="width:100%;border-collapse:collapse;background:#fff">
            <thead>
              <tr style="background:{color_bg}">
                <th style="padding:8px 12px;text-align:left;font-size:12px;color:#333">Fuente</th>
                <th style="padding:8px 12px;text-align:left;font-size:12px;color:#333">URL</th>
                <th style="padding:8px 12px;text-align:left;font-size:12px;color:#333">Detalle</th>
                <th style="padding:8px 12px;text-align:center;font-size:12px;color:#333">Intentos</th>
                <th style="padding:8px 12px;text-align:left;font-size:12px;color:#333">Hora</th>
              </tr>
            </thead>
            <tbody>{filas}</tbody>
          </table>
        </div>"""

    tipos_resumen = " · ".join(
        f"{t.value}: {len(l)}" for t, l in por_tipo.items()
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:0}}
  .wrap{{max-width:900px;margin:0 auto;background:#fff}}
  .header{{background:#263238;padding:28px 32px}}
  .header h1{{color:#fff;font-size:20px;margin:0}}
  .header p{{color:#90A4AE;font-size:13px;margin:6px 0 0}}
  .summary{{background:#37474F;padding:12px 32px;font-size:13px;color:#CFD8DC}}
  .body{{padding:28px 32px}}
  .footer{{padding:16px 32px;border-top:1px solid #eee;font-size:11px;color:#999}}
</style></head><body><div class="wrap">
  <div class="header">
    <h1>⚠️ Resumen de Errores — RPA Boletín Minero-Energético</h1>
    <p>{fecha}</p>
  </div>
  <div class="summary">
    Total: {len(errores)} error(es) · {tipos_resumen}
  </div>
  <div class="body">
    <p style="color:#555;margin-bottom:24px;font-size:14px">
      Los siguientes errores ocurrieron durante el procesamiento de fuentes.
      Cada uno agotó los {MAX_INTENTOS} intentos disponibles con backoff exponencial.
      Las fuentes fallidas serán reintentadas en la próxima ejecución del día.
    </p>
    {secciones}
  </div>
  <div class="footer">
    RPA Boletín Minero-Energético · Sistema de alertas automáticas · {fecha}
  </div>
</div></body></html>"""
