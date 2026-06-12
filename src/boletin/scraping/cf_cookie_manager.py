"""
Gestión automática de cookies cf_clearance para sitios protegidos por Cloudflare.

Cadena de resolución (en orden):
  1. Caché local (.scraper_profile/cf_cookies.json) — sin costo, instantáneo.
  2. CapSolver AntiCloudflareTask — requiere CAPSOLVER_API_KEY + CAPSOLVER_PROXY.
  3. Chrome real con perfil persistente — fallback sin costo externo.

Variables de entorno requeridas para CapSolver:
  CAPSOLVER_API_KEY   — API key de capsolver.com
  CAPSOLVER_PROXY     — Proxy estático o sticky (ej: http://user:pass@host:port)
                        REQUERIDO para AntiCloudflareTask. El mismo proxy se usa
                        en curl_cffi para que la cookie cf_clearance sea válida.

Retorno de get_cf_clearance: tuple (cf_clearance, user_agent, proxy) | None
  - proxy puede ser "" si se resolvió sin proxy (fallback Chrome).
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from boletin.config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROFILE_DIR = PROJECT_ROOT / ".scraper_profile"
_CACHE_FILE = _PROFILE_DIR / "cf_cookies.json"
_COOKIE_TTL_SECONDS = 43_200  # 12 horas

_CHALLENGE_TOKENS = ("un momento", "just a moment", "checking your browser", "cloudflare")
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_runtime_cache: dict[str, dict] = {}


# ── API pública ───────────────────────────────────────────────────────────────

def get_cf_clearance(url: str) -> tuple[str, str, str] | None:
    """
    Retorna (cf_clearance, user_agent, proxy) o None.
    proxy es "" si se resolvió sin proxy (Chrome fallback).
    El proxy retornado debe usarse en curl_cffi para que la cookie sea válida.
    """
    domain = _domain(url)

    cached = _load_cache(domain)
    if cached:
        logger.info("[cf_cookie_manager] cf_clearance en caché para %s", domain)
        return cached["cf_clearance"], cached["user_agent"], cached.get("proxy", "")

    result = _fetch_via_capsolver(url)
    if result:
        _save_cache(domain, *result)
        return result

    logger.info("[cf_cookie_manager] CapSolver no disponible — abriendo Chrome para %s", domain)
    result = _fetch_via_chrome(url)
    if result:
        cf_clearance, user_agent = result
        _save_cache(domain, cf_clearance, user_agent, "")
        return cf_clearance, user_agent, ""

    return None


def invalidate(url: str) -> None:
    """Invalida la cookie en caché para forzar renovación en la próxima llamada."""
    domain = _domain(url)
    _runtime_cache.pop(domain, None)
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            data.pop(domain, None)
            _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Caché ─────────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.replace("www.", "") or url


def _load_cache(domain: str) -> dict | None:
    if domain in _runtime_cache:
        entry = _runtime_cache[domain]
        if entry.get("expires", 0) > time.time():
            return entry
        _runtime_cache.pop(domain)

    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        entry = data.get(domain)
        if entry and entry.get("expires", 0) > time.time() and entry.get("cf_clearance"):
            _runtime_cache[domain] = entry
            return entry
    except Exception:
        pass
    return None


def _save_cache(domain: str, cf_clearance: str, user_agent: str, proxy: str) -> None:
    entry = {
        "cf_clearance": cf_clearance,
        "user_agent": user_agent,
        "proxy": proxy,
        "expires": time.time() + _COOKIE_TTL_SECONDS,
    }
    _runtime_cache[domain] = entry
    try:
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8")) if _CACHE_FILE.exists() else {}
        data[domain] = entry
        _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[cf_cookie_manager] cf_clearance guardada en caché para %s (TTL: 12h)", domain)
    except Exception as exc:
        logger.warning("[cf_cookie_manager] No se pudo guardar caché: %s", exc)


# ── CapSolver AntiCloudflareTask ──────────────────────────────────────────────

def _fetch_via_capsolver(url: str) -> tuple[str, str, str] | None:
    try:
        import capsolver as _capsolver
    except ImportError:
        logger.debug("[cf_cookie_manager] capsolver no instalado")
        return None

    from boletin.config.environment import get_capsolver_settings
    cfg = get_capsolver_settings()

    if not cfg.api_key:
        logger.debug("[cf_cookie_manager] CAPSOLVER_API_KEY no configurada")
        return None

    if not cfg.proxy:
        logger.warning(
            "[cf_cookie_manager] AntiCloudflareTask requiere proxy. "
            "Configurá CAPSOLVER_PROXY en .env (proxy estático o sticky)."
        )
        return None

    # Obtener HTML del 403 para ayudar a CapSolver a resolver mejor
    html_403 = _get_challenge_html(url)

    task: dict = {
        "type": "AntiCloudflareTask",
        "websiteURL": url,
        "proxy": cfg.proxy,
    }
    if html_403:
        task["html"] = html_403

    logger.info("[cf_cookie_manager] CapSolver: AntiCloudflareTask para %s...", url)

    try:
        _capsolver.api_key = cfg.api_key
        solution = _capsolver.solve(task)

        cf_clearance = solution.get("cf_clearance", "")
        user_agent = solution.get("userAgent") or _DEFAULT_UA

        if not cf_clearance:
            logger.warning("[cf_cookie_manager] CapSolver: sin cf_clearance en respuesta: %s", solution)
            return None

        logger.info("[cf_cookie_manager] CapSolver: cf_clearance obtenida con proxy %s", cfg.proxy.split("@")[-1])
        return cf_clearance, user_agent, cfg.proxy

    except Exception as exc:
        logger.warning("[cf_cookie_manager] CapSolver falló: %s", exc)
        return None


def _get_challenge_html(url: str) -> str:
    """Hace una request inicial para obtener el HTML del challenge (403/503)."""
    try:
        from curl_cffi import requests as curl_requests
        r = curl_requests.get(url, impersonate="chrome120", timeout=15, allow_redirects=True)
        if r.status_code in (403, 503):
            logger.debug("[cf_cookie_manager] HTML 403 obtenido para CapSolver (%d chars)", len(r.text))
            return r.text
    except Exception as exc:
        logger.debug("[cf_cookie_manager] No se pudo obtener HTML del challenge: %s", exc)
    return ""


# ── Chrome con perfil persistente (fallback sin costo) ────────────────────────

def _fetch_via_chrome(url: str) -> tuple[str, str] | None:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError as exc:
        logger.warning("[cf_cookie_manager] Playwright/stealth no disponible: %s", exc)
        return None

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(_PROFILE_DIR),
                channel="chrome",
                headless=False,
                args=["--no-sandbox", "--start-maximized"],
            )
        except Exception as exc:
            logger.warning("[cf_cookie_manager] No se pudo lanzar Chrome: %s", exc)
            return None

        try:
            page = ctx.new_page()
            Stealth().use_sync(page)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass

            started = time.time()
            _clicked = False

            while time.time() - started < 60:
                title = (page.title() or "").lower()

                if any(t in title for t in _CHALLENGE_TOKENS):
                    logger.info(
                        "[cf_cookie_manager] Chrome: challenge activo '%s' (%ds)",
                        title, int(time.time() - started),
                    )
                    if not _clicked:
                        _clicked = _try_click_turnstile(page)
                        if _clicked:
                            time.sleep(4)
                            continue
                else:
                    cookies = {c["name"]: c["value"] for c in ctx.cookies([url])}
                    cf = cookies.get("cf_clearance")
                    if cf:
                        ua = page.evaluate("navigator.userAgent")
                        logger.info("[cf_cookie_manager] Chrome: cf_clearance obtenida")
                        return cf, ua

                time.sleep(2)

            logger.warning("[cf_cookie_manager] Chrome: timeout — no se obtuvo cf_clearance en 60s")
            return None
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _try_click_turnstile(page) -> bool:
    try:
        cf_frame = page.frame_locator(
            "iframe[src*='challenges.cloudflare.com'], iframe[src*='cloudflare.com']"
        ).first
        cb = cf_frame.locator("input[type='checkbox'], .mark").first
        cb.wait_for(timeout=2_000)
        page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        time.sleep(random.uniform(0.3, 0.7))
        cb.click()
        logger.info("[cf_cookie_manager] Turnstile: click ejecutado")
        return True
    except Exception:
        return False
