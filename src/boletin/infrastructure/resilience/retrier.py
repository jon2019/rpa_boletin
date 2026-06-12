"""
retrier.py
----------
Módulo centralizado de control de errores y reintentos.

Lógica:
  - Hasta MAX_INTENTOS intentos por operación.
  - Backoff exponencial configurable.
  - Clasifica cada fallo en uno de cuatro tipos:
      URL_NO_DISPONIBLE — HTTP 4xx/5xx o timeout de red
      API_IA            — error en llamada a Claude (Anthropic)
      BASE_DE_DATOS     — fallo en operación PostgreSQL
      ENVIO_EMAIL       — fallo en SMTP

  - Errores dentro del flujo de fuentes se acumulan en un ErrorCollector
    y se envían en un único correo resumen al finalizar todas las iteraciones.
  - Errores críticos de DB al inicio del programa cierran el proceso y envían
    un correo a TO_EMAILS_ERRORES.
"""

from __future__ import annotations

import logging
import random
import smtplib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Callable, TypeVar

from boletin.config.environment import get_retry_settings, get_smtp_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TipoError(str, Enum):
    URL_NO_DISPONIBLE = "URL / Página no disponible"
    API_IA = "API Inteligencia Artificial"
    BASE_DE_DATOS = "Base de datos"
    ENVIO_EMAIL = "Envío de email"


@dataclass
class ErrorFuente:
    """Registro de un error ocurrido durante el procesamiento de una fuente."""

    fuente: str
    url: str
    tipo: TipoError
    mensaje: str
    intentos: int
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    tecnologias: str = "RSS"


class ErrorCollector:
    """
    Acumula errores durante las iteraciones de fuentes.
    Al finalizar todas las iteraciones, `enviar_resumen()` despacha
    un único email con todos los errores agrupados por tipo.
    """

    def __init__(self):
        self._errores: list[ErrorFuente] = []

    def registrar(
        self,
        fuente: str,
        url: str,
        tipo: TipoError,
        mensaje: str,
        intentos: int,
        tecnologias: str = "RSS",
    ) -> None:
        err = ErrorFuente(
            fuente=fuente,
            url=url,
            tipo=tipo,
            mensaje=mensaje,
            intentos=intentos,
            tecnologias=tecnologias,
        )
        self._errores.append(err)
        logger.warning(
            "[%s] %s — %s (después de %d intentos): %s",
            tipo.value,
            fuente,
            url,
            intentos,
            mensaje[:200],
        )

    def hay_errores(self) -> bool:
        """Retorna True si hay errores acumulados."""
        return bool(self._errores)

    def total(self) -> int:
        """Retorna el número total de errores acumulados."""
        return len(self._errores)

    def enviar_resumen(self) -> None:
        """Envía un único email con todos los errores agrupados por tipo."""
        smtp = get_smtp_settings()
        if not self._errores:
            logger.info("Sin errores que reportar.")
            return
        if not smtp.error_to_emails:
            logger.warning("TO_EMAILS_ERRORES no configurado — resumen de errores no enviado.")
            return

        fecha = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        html = _render_resumen_errores(self._errores, fecha)

        _enviar_email_raw(
            subject=f"RPA Boletín — {len(self._errores)} error(es) detectado(s) — {fecha}",
            html_body=html,
            recipients=smtp.error_to_emails,
        )
        logger.info(
            "Resumen de errores enviado a %s (%d errores)",
            smtp.error_to_emails,
            len(self._errores),
        )


_collector = ErrorCollector()


def get_collector() -> ErrorCollector:
    """Retorna la instancia global del ErrorCollector."""
    return _collector


def reset_collector() -> None:
    """Resetea el ErrorCollector global al inicio de cada pipeline."""
    global _collector
    _collector = ErrorCollector()


def _is_rate_limit_error(exc: Exception, tipo_error: TipoError) -> bool:
    """Detecta si el error corresponde a rate limit (429) o sobrecarga (529)."""
    if tipo_error != TipoError.API_IA:
        return False

    message = str(exc).lower()
    if (
        "429" in message
        or "529" in message
        or "too many requests" in message
        or "rate limit" in message
        or "overloaded" in message
    ):
        return True

    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 529):
        return True

    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) in (429, 529):
        return True

    return False


def _leer_retry_after(exc: Exception) -> float | None:
    """
    Extrae el valor del header `retry-after` de la excepción de Anthropic.
    Retorna los segundos a esperar, o None si no está disponible.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None

    headers = getattr(response, "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None

    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _calcular_espera(intento: int, tipo_error: TipoError, exc: Exception) -> tuple[float, str]:
    """
    Calcula la espera entre reintentos.
    - General: backoff exponencial clásico.
    - API_IA + 429: usa `retry-after` si está disponible;
      si no, usa backoff conservador + jitter.
    """
    retry_settings = get_retry_settings()
    if _is_rate_limit_error(exc, tipo_error):
        retry_after = _leer_retry_after(exc)
        if retry_after is not None:
            espera = round(retry_after + random.uniform(0.5, 2.0), 2)
            return espera, f"rate-limit-retry-after({retry_after}s)"

        base_wait = min(
            retry_settings.api_ia_rate_limit_max,
            retry_settings.api_ia_rate_limit_base * (2 ** (intento - 1)),
        )
        jitter = random.uniform(0.0, 1.5)
        return round(base_wait + jitter, 2), "rate-limit-429"

    return float(retry_settings.backoff_base**intento), "standard"


def con_reintentos(
    fn: Callable[[], T],
    tipo_error: TipoError,
    fuente: str = "",
    url: str = "",
    max_intentos: int | None = None,
    registrar_en_collector: bool = True,
    tecnologias: str = "RSS",
) -> tuple[T | None, bool, str]:
    """
    Ejecuta `fn()` con reintentos y backoff exponencial.

    Retorna `(resultado, exito, ultimo_error)`.
    """
    retry_settings = get_retry_settings()
    intentos_maximos = max_intentos or retry_settings.max_intentos
    ultimo_error = ""

    for intento in range(1, intentos_maximos + 1):
        try:
            resultado = fn()
            if intento > 1:
                logger.info("Exito en intento %d/%d - %s", intento, intentos_maximos, fuente or url)
            return resultado, True, ""

        except Exception as exc:
            ultimo_error = str(exc)
            if intento < intentos_maximos:
                espera, motivo_espera = _calcular_espera(intento, tipo_error, exc)
                if motivo_espera.startswith("rate-limit"):
                    logger.warning(
                        "Intento %d/%d fallido - %s: %s. Reintentando en %.2fs...",
                        intento,
                        intentos_maximos,
                        motivo_espera,
                        fuente or url,
                        ultimo_error[:150],
                        espera,
                    )
                else:
                    logger.warning(
                        "Intento %d/%d fallido - %s: %s. Reintentando en %.2fs...",
                        intento,
                        intentos_maximos,
                        fuente or url,
                        ultimo_error[:150],
                        espera,
                    )
                time.sleep(espera)
            else:
                logger.error(
                    "Todos los intentos agotados (%d/%d) - %s: %s",
                    intento,
                    intentos_maximos,
                    fuente or url,
                    ultimo_error[:200],
                )

    if registrar_en_collector:
        _collector.registrar(
            fuente=fuente,
            url=url,
            tipo=tipo_error,
            mensaje=ultimo_error,
            intentos=intentos_maximos,
            tecnologias=tecnologias,
        )

    return None, False, ultimo_error


def error_critico(mensaje: str, detalle: str = "") -> None:
    """
    Envía un email de error crítico a TO_EMAILS_ERRORES y deja evidencia en logs.
    Se usa para fallos de inicialización que impiden continuar.
    """
    smtp = get_smtp_settings()
    logger.critical("ERROR CRÍTICO: %s — %s", mensaje, detalle)

    if smtp.error_to_emails:
        fecha = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        html = _render_error_critico(mensaje, detalle, fecha)
        _enviar_email_raw(
            subject=f"RPA Boletín — ERROR CRÍTICO — {fecha}",
            html_body=html,
            recipients=smtp.error_to_emails,
        )
    else:
        logger.error("TO_EMAILS_ERRORES no configurado — email de error crítico no enviado.")


def _enviar_email_raw(subject: str, html_body: str, recipients: list[str]) -> bool:
    """
    Envío SMTP directo para emails de error.
    Usa 3 intentos para no bloquear indefinidamente ante un fallo SMTP.
    """
    smtp = get_smtp_settings()
    retry_settings = get_retry_settings()

    if not recipients or not smtp.host:
        logger.warning("SMTP no configurado o sin destinatarios de error.")
        return False

    for intento in range(1, 4):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp.from_email
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(smtp.host, smtp.port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(smtp.user, smtp.password)
                server.sendmail(smtp.from_email, recipients, msg.as_string())

            logger.info("Email de error enviado a %s", recipients)
            return True

        except Exception as exc:
            if intento < 3:
                time.sleep(retry_settings.backoff_base**intento)
            else:
                logger.error("No se pudo enviar email de error: %s", exc)

    return False


def _render_error_critico(mensaje: str, detalle: str, fecha: str) -> str:
    """Renderiza el HTML para el email de error crítico."""
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
    <h1>Error crítico — RPA Boletín Minero-Energético</h1>
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
        4. Reiniciar el servicio correspondiente
      </div>
    </div>
  </div>
  <div class="footer">RPA Boletín Minero-Energético · Sistema de alertas automáticas</div>
</div></body></html>"""


def _render_resumen_errores(errores: list[ErrorFuente], fecha: str) -> str:
    """Renderiza el HTML para el email de resumen de errores agrupados por tipo."""
    retry_settings = get_retry_settings()

    por_tipo: dict[TipoError, list[ErrorFuente]] = {}
    for error in errores:
        por_tipo.setdefault(error.tipo, []).append(error)

    colores = {
        TipoError.URL_NO_DISPONIBLE: ("#E65100", "#FBE9E7"),
        TipoError.API_IA: ("#4A148C", "#F3E5F5"),
        TipoError.BASE_DE_DATOS: ("#B71C1C", "#FFEBEE"),
        TipoError.ENVIO_EMAIL: ("#1A237E", "#E8EAF6"),
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
              <td style="padding:8px 12px;font-size:12px;color:#1565c0;font-weight:600">{err.tecnologias}</td>
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
                <th style="padding:8px 12px;text-align:left;font-size:12px;color:#333">Tecnología</th>
              </tr>
            </thead>
            <tbody>{filas}</tbody>
          </table>
        </div>"""

    tipos_resumen = " · ".join(f"{tipo.value}: {len(lista)}" for tipo, lista in por_tipo.items())

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
    <h1>Resumen de errores — RPA Boletín Minero-Energético</h1>
    <p>{fecha}</p>
  </div>
  <div class="summary">
    Total: {len(errores)} error(es) · {tipos_resumen}
  </div>
  <div class="body">
    <p style="color:#555;margin-bottom:24px;font-size:14px">
      Los siguientes errores ocurrieron durante el procesamiento de fuentes.
      Cada uno agotó los {retry_settings.max_intentos} intentos disponibles con backoff exponencial.
      Las fuentes fallidas serán reintentadas en la próxima ejecución del día.
    </p>
    {secciones}
  </div>
  <div class="footer">
    RPA Boletín Minero-Energético · Sistema de alertas automáticas · {fecha}
  </div>
</div></body></html>"""
