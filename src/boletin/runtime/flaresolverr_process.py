"""
Gestión del proceso FlareSolverr como dependencia de infraestructura.

Estado persistido en FLARESOLVERR_HEARTBEAT_FILE (JSON):
  {
    "pid":        13076,
    "status":     "running",          # starting | running | stuck | crashed | failed
    "detail":     "listo en 8s",
    "updated_at": "2026-04-28T11:39:00.123456"
  }

El heartbeat permite que al siguiente arranque se pueda:
  - Detectar un proceso vivo (PID alive + HTTP OK)  → reusar sin relanzar
  - Detectar un proceso pegado (PID alive + HTTP KO) → kill + restart
  - Detectar un proceso muerto (PID no existe)       → launch directo

Variables de entorno:
  FLARESOLVERR_EXE_PATH          ruta al ejecutable        (default: C:\\tools\\flaresolverr\\flaresolverr.exe)
  FLARESOLVERR_STARTUP_TIMEOUT_S segundos máx espera       (default: 30)
  FLARESOLVERR_URL               URL base de la API        (default: http://127.0.0.1:8191/v1)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from boletin.config.environment import get_flaresolverr_settings
from boletin.config.settings import FLARESOLVERR_HEARTBEAT_FILE

logger = logging.getLogger(__name__)

_HEALTH_POLL_INTERVAL_S = 2
_MAX_LAUNCH_ATTEMPTS    = 3


# ── Heartbeat JSON ────────────────────────────────────────────────────────────

def _write_heartbeat(
    pid: int | None,
    status: str,
    detail: str,
    path: Path = FLARESOLVERR_HEARTBEAT_FILE,
) -> None:
    payload = {
        "pid":        pid,
        "status":     status,
        "detail":     detail,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("FlareSolverr: no se pudo escribir heartbeat — %s", exc)


def _read_heartbeat(path: Path = FLARESOLVERR_HEARTBEAT_FILE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Process helpers ───────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    """
    Verifica si el PID sigue activo sin enviar señales que puedan afectarlo.
    os.kill(pid, 0) funciona en Windows y Unix — lanza ProcessLookupError si
    el proceso no existe, PermissionError si existe pero no tenemos permiso
    (en ambos casos el PID está ocupado → proceso vivo).
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe pero sin permisos de señal → sigue vivo
    except Exception:
        return False


def _health_url(api_url: str) -> str:
    """
    Deriva la URL de health check desde la URL de API configurada.

    FlareSolverr 3.x tiene dos rutas:
      GET  /    → health check — devuelve {"msg": "FlareSolverr is ready!", "version": "..."}
      POST /v1  → API de requests — solo acepta POST, GET devuelve 405

    Como FLARESOLVERR_URL apunta a /v1 (endpoint de uso), derivamos la raíz
    para el health check en lugar de usar la URL de API directamente.
    """
    parsed = urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _is_alive(api_url: str) -> bool:
    """
    Health check contra la raíz de FlareSolverr (GET /).
    Valida el body para no confundir con otro proceso en el mismo puerto.
    """
    try:
        r = httpx.get(_health_url(api_url), timeout=3)
        if r.status_code != 200:
            return False
        body = r.text.lower()
        return "flaresolverr" in body and ("ready" in body or "version" in body)
    except Exception:
        return False


def _chromedriver_cache_path() -> Path:
    """
    Retorna la ruta del chromedriver.exe cacheado por undetected_chromedriver.
    Este archivo queda bloqueado cuando FlareSolverr muere sin cerrar el driver.
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "undetected_chromedriver" / "chromedriver.exe"
    return Path.home() / ".local" / "share" / "undetected_chromedriver" / "chromedriver"


def _cleanup_before_launch(log: logging.Logger) -> None:
    """
    Limpia residuos de ejecuciones anteriores de FlareSolverr que bloquean el arranque.

    Problema: undetected_chromedriver intenta copiar chromedriver.exe al cache en AppData.
    Si una instancia previa murió sin cerrar el driver, el archivo queda bloqueado y
    FlareSolverr crashea con PermissionError [Errno 13] al siguiente intento de arranque.

    Solución:
      1. Matar procesos chromedriver.exe colgados (los que tienen el lock del archivo)
      2. Eliminar el archivo bloqueado del cache para que FlareSolverr lo regenere limpio
    """
    # ── 1. Matar procesos chromedriver colgados ───────────────────────────────
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
            capture_output=True, text=True,
        )
        # returncode 128 = "no se encontró el proceso" → no hay nada colgado, OK
        if result.returncode == 0:
            log.info("FlareSolverr cleanup: procesos chromedriver.exe terminados")
            time.sleep(1)   # dar tiempo al SO para liberar el file handle
    else:
        subprocess.run(["pkill", "-f", "chromedriver"], capture_output=True)
        time.sleep(1)

    # ── 2. Eliminar el archivo bloqueado del cache ────────────────────────────
    cache = _chromedriver_cache_path()
    if not cache.exists():
        return

    try:
        cache.unlink()
        log.info("FlareSolverr cleanup: chromedriver.exe eliminado del cache (%s)", cache)
    except PermissionError as exc:
        log.warning(
            "FlareSolverr cleanup: no se pudo eliminar %s — %s. "
            "Si el arranque falla con PermissionError, cerrá manualmente los procesos Chrome.",
            cache, exc,
        )
    except Exception as exc:
        log.warning("FlareSolverr cleanup: error inesperado eliminando cache — %s", exc)


def _launch_process(exe_path: Path, log: logging.Logger) -> subprocess.Popen | None:
    log.info("FlareSolverr: lanzando proceso desde %s", exe_path)
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin":  subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen([str(exe_path)], **kwargs)
    except Exception as exc:
        log.error("FlareSolverr: Popen falló — %s", exc)
        return None


def _kill(proc: subprocess.Popen | None, log: logging.Logger) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
            log.debug("FlareSolverr: proceso terminado (PID %s)", proc.pid)
    except Exception:
        pass


def _wait_until_ready(
    proc: subprocess.Popen,
    url: str,
    timeout_s: int,
    log: logging.Logger,
) -> bool:
    """
    Espera a que FlareSolverr responda HTTP, chequeando también si el proceso
    murió (proc.poll() != None) para no esperar hasta timeout innecesariamente.
    """
    elapsed = 0
    while elapsed < timeout_s:
        time.sleep(_HEALTH_POLL_INTERVAL_S)
        elapsed += _HEALTH_POLL_INTERVAL_S

        exit_code = proc.poll()
        if exit_code is not None:
            _write_heartbeat(proc.pid, "crashed", f"exit_code={exit_code} tras {elapsed}s")
            log.warning(
                "FlareSolverr: proceso terminó durante arranque "
                "(PID %s | exit_code=%s | elapsed=%ds)",
                proc.pid, exit_code, elapsed,
            )
            return False   # el banner de resultado lo pone el loop con contexto de intento

        if _is_alive(url):
            _write_heartbeat(proc.pid, "running", f"listo en {elapsed}s")
            log.info("─" * 60)
            log.info("[ FLARESOLVERR OK ] PID %s | respondió en %ds | %s", proc.pid, elapsed, url)
            log.info("─" * 60)
            return True

        log.debug(
            "FlareSolverr: esperando respuesta... (%ds / %ds | PID %s)",
            elapsed, timeout_s, proc.pid,
        )

    _write_heartbeat(proc.pid, "stuck", f"timeout {timeout_s}s sin respuesta HTTP")
    log.warning(
        "FlareSolverr: PID %s vivo pero sin respuesta HTTP tras %ds",
        proc.pid, timeout_s,
    )
    return False   # ídem — el banner lo pone el loop


# ── Entrada pública ───────────────────────────────────────────────────────────

def bootstrap_flaresolverr(log: logging.Logger | None = None) -> bool:
    """
    Garantiza que FlareSolverr esté operativo antes de que el pipeline arranque.

    Usa el heartbeat JSON para distinguir tres escenarios al iniciar:
      1. PID alive + HTTP OK   → ya estaba corriendo, nada que hacer
      2. PID alive + HTTP KO   → proceso pegado, kill + restart
      3. PID muerto / sin JSON → lanzar proceso nuevo

    Si el lanzamiento falla, reintenta hasta _MAX_LAUNCH_ATTEMPTS veces.

    Retorna True si FlareSolverr está listo, False si no se pudo iniciar.
    No hace sys.exit — es un servicio opcional del pipeline.
    """
    _log = log or logger
    cfg  = get_flaresolverr_settings()
    exe  = Path(cfg.exe_path)

    _log.info("─" * 60)
    _log.info("FLARESOLVERR — verificando estado del servicio")
    _log.info("  exe : %s", cfg.exe_path)
    _log.info("  url : %s", cfg.url)
    _log.info("─" * 60)

    # ── 1. Leer heartbeat previo ──────────────────────────────────────────────
    hb = _read_heartbeat()
    if hb:
        _log.info(
            "Heartbeat previo → pid=%-7s | status=%-10s | updated_at=%s",
            hb.get("pid", "-"), hb.get("status", "-"), hb.get("updated_at", "-"),
        )
        prev_pid = hb.get("pid")
        if prev_pid and _pid_alive(prev_pid):
            if _is_alive(cfg.url):
                _write_heartbeat(prev_pid, "running", "reutilizado al reiniciar pipeline")
                _log.info("─" * 60)
                _log.info("[ FLARESOLVERR OK ] proceso previo reutilizado | PID %s | %s", prev_pid, cfg.url)
                _log.info("─" * 60)
                return True
            _log.warning(
                "PID %s vivo pero NO responde HTTP → proceso pegado, terminando...", prev_pid,
            )
            try:
                os.kill(prev_pid, 9)
                time.sleep(1)
            except Exception:
                pass
            _write_heartbeat(prev_pid, "stuck", "terminado por no responder HTTP")
    else:
        _log.info("Sin heartbeat previo — primera ejecución o archivo eliminado.")

    # ── 2. Verificar que el ejecutable exista ─────────────────────────────────
    if not exe.exists():
        _write_heartbeat(None, "failed", f"ejecutable no encontrado: {exe}")
        _log.warning("─" * 60)
        _log.warning("[ FLARESOLVERR NO DISPONIBLE ] ejecutable no encontrado: %s", exe)
        _log.warning("Las fuentes que requieren bypass Cloudflare serán omitidas.")
        _log.warning("─" * 60)
        return False

    # ── 3. Lanzar con reintentos ──────────────────────────────────────────────
    _log.info("Iniciando proceso (máx %d intentos | timeout %ds c/u)...", _MAX_LAUNCH_ATTEMPTS, cfg.startup_timeout_s)

    proc: subprocess.Popen | None = None

    for attempt in range(1, _MAX_LAUNCH_ATTEMPTS + 1):
        _log.info("── Intento %d/%d ──────────────────────────────────────────", attempt, _MAX_LAUNCH_ATTEMPTS)
        _kill(proc, _log)
        _cleanup_before_launch(_log)

        proc = _launch_process(exe, _log)
        if proc is None:
            _write_heartbeat(None, "failed", f"Popen falló en intento {attempt}")
            _log.warning("Intento %d: Popen falló — no se pudo crear el proceso.", attempt)
            continue

        _log.info("Intento %d: proceso creado con PID %s — esperando respuesta HTTP...", attempt, proc.pid)
        _write_heartbeat(proc.pid, "starting", f"intento {attempt}/{_MAX_LAUNCH_ATTEMPTS}")

        if _wait_until_ready(proc, cfg.url, cfg.startup_timeout_s, _log):
            return True

        reason = (
            f"proceso crasheó (exit_code={proc.poll()})"
            if proc.poll() is not None
            else f"sin respuesta HTTP en {cfg.startup_timeout_s}s"
        )
        if attempt < _MAX_LAUNCH_ATTEMPTS:
            _log.warning(
                "── Intento %d/%d fallido: %s — reintentando... ──────────────",
                attempt, _MAX_LAUNCH_ATTEMPTS, reason,
            )
        else:
            _log.warning(
                "── Intento %d/%d fallido: %s ──────────────────────────────",
                attempt, _MAX_LAUNCH_ATTEMPTS, reason,
            )

    _kill(proc, _log)
    _write_heartbeat(
        proc.pid if proc else None,
        "failed",
        f"no respondió tras {_MAX_LAUNCH_ATTEMPTS} intentos",
    )
    _log.warning("─" * 60)
    _log.warning("[ FLARESOLVERR NO DISPONIBLE ] no pudo iniciarse tras %d intentos.", _MAX_LAUNCH_ATTEMPTS)
    _log.warning("Las fuentes que requieren bypass Cloudflare serán omitidas.")
    _log.warning("─" * 60)
    return False
