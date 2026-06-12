"""Carga compartida de variables de entorno del proyecto."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os

import anthropic
from dotenv import load_dotenv

from boletin.config.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def load_project_env() -> None:
    """Carga `.env` una sola vez para todo el proceso."""
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class RetrySettings:
    max_intentos: int
    backoff_base: int
    api_ia_rate_limit_base: int
    api_ia_rate_limit_max: int


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    from_email: str
    to_emails: list[str]
    error_to_emails: list[str]


@dataclass(frozen=True)
class BulletinIdentitySettings:
    empresa_nombre: str
    subtitulo_es: str
    subtitulo_en: str


@dataclass(frozen=True)
class FlareSolverrSettings:
    url: str
    max_timeout_ms: int
    wait_seconds: int
    exe_path: str
    startup_timeout_s: int


@lru_cache(maxsize=1)
def get_retry_settings() -> RetrySettings:
    """Obtiene configuración de reintentos desde variables de entorno."""
    load_project_env()

    backoff_base = max(1, int(os.getenv("REINTENTOS_BACKOFF_BASE", 2)))
    api_ia_rate_limit_base = max(backoff_base, int(os.getenv("API_IA_RATE_LIMIT_BASE", 5)))

    return RetrySettings(
        max_intentos=max(1, min(10, int(os.getenv("REINTENTOS_MAX", 5)))),
        backoff_base=backoff_base,
        api_ia_rate_limit_base=api_ia_rate_limit_base,
        api_ia_rate_limit_max=max(
            api_ia_rate_limit_base,
            int(os.getenv("API_IA_RATE_LIMIT_MAX", 60)),
        ),
    )


@lru_cache(maxsize=1)
def get_smtp_settings() -> SmtpSettings:
    """Obtiene configuración de SMTP y destinatarios desde variables de entorno."""
    load_project_env()

    return SmtpSettings(
        host=os.getenv("SMTP_HOST", ""),
        port=int(os.getenv("SMTP_PORT", 587)),
        user=os.getenv("SMTP_USER", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        from_email=os.getenv("FROM_EMAIL", ""),
        to_emails=[email.strip() for email in os.getenv("TO_EMAILS", "").split(",") if email.strip()],
        error_to_emails=[
            email.strip()
            for email in os.getenv("TO_EMAILS_ERRORES", "").split(",")
            if email.strip()
        ],
    )


@lru_cache(maxsize=1)
def get_bulletin_identity_settings() -> BulletinIdentitySettings:
    """Obtiene textos configurables de identidad del boletín."""
    load_project_env()

    return BulletinIdentitySettings(
        empresa_nombre=os.getenv("EMPRESA_NOMBRE", "Empresa"),
        subtitulo_es=os.getenv(
            "BOLETIN_SUBTITULO_ES",
            "Minería y Energía · Chile · Perú · Argentina",
        ),
        subtitulo_en=os.getenv(
            "BOLETIN_SUBTITULO_EN",
            "Mining & Energy · Chile · Peru · Argentina",
        ),
    )


@lru_cache(maxsize=1)
def get_flaresolverr_settings() -> FlareSolverrSettings:
    """Obtiene configuración de FlareSolverr desde variables de entorno."""
    load_project_env()

    return FlareSolverrSettings(
        url=os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191/v1"),
        max_timeout_ms=int(os.getenv("FLARESOLVERR_MAX_TIMEOUT_MS", "180000")),
        wait_seconds=int(os.getenv("FLARESOLVERR_WAIT_SECONDS", "8")),
        exe_path=os.getenv("FLARESOLVERR_EXE_PATH", r"C:\tools\flaresolverr\flaresolverr.exe"),
        startup_timeout_s=max(5, int(os.getenv("FLARESOLVERR_STARTUP_TIMEOUT_S", "30"))),
    )


@dataclass(frozen=True)
class CapsolverSettings:
    api_key: str
    proxy: str


@lru_cache(maxsize=1)
def get_capsolver_settings() -> CapsolverSettings:
    load_project_env()
    return CapsolverSettings(
        api_key=os.getenv("CAPSOLVER_API_KEY", ""),
        proxy=os.getenv("CAPSOLVER_PROXY", ""),
    )


@dataclass(frozen=True)
class CloudflareCookieSettings:
    cf_clearance: str
    user_agent: str


@lru_cache(maxsize=None)
def get_cloudflare_cookies(domain: str) -> CloudflareCookieSettings | None:
    """
    Lee cookies manuales de Cloudflare desde variables de entorno.
    Formato: CF_CLEARANCE__<DOMINIO> y CF_UA__<DOMINIO>
    donde <DOMINIO> es el dominio en mayúsculas con puntos reemplazados por _.
    Ejemplo: CF_CLEARANCE__RUMBOMINERO_COM
    """
    load_project_env()
    key = domain.upper().replace(".", "_").replace("-", "_")
    cf_clearance = os.getenv(f"CF_CLEARANCE__{key}", "")
    user_agent = os.getenv(
        f"CF_UA__{key}",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    if not cf_clearance:
        return None
    return CloudflareCookieSettings(cf_clearance=cf_clearance, user_agent=user_agent)


@dataclass(frozen=True)
class ScrapingSettings:
    rss_resumen_max_chars: int
    max_elementos_por_selector: int
    max_candidatos_totales: int


@lru_cache(maxsize=1)
def get_scraping_settings() -> ScrapingSettings:
    """Obtiene configuración de scraping desde variables de entorno."""
    load_project_env()
    return ScrapingSettings(
        rss_resumen_max_chars=max(100, int(os.getenv("RSS_RESUMEN_MAX_CHARS", 1000))),
        max_elementos_por_selector=max(10, int(os.getenv("SCRAPING_MAX_ELEMENTOS_POR_SELECTOR", 120))),
        max_candidatos_totales=max(10, int(os.getenv("SCRAPING_MAX_CANDIDATOS_TOTALES", 120))),
    )


@lru_cache(maxsize=1)
def get_anthropic_client() -> anthropic.Anthropic:
    """Crea y cachea el cliente de Anthropic con el entorno ya cargado."""
    load_project_env()
    return anthropic.Anthropic()


@dataclass(frozen=True)
class BnamericasAppSettings:
    filter_path: str
    login_url: str


@lru_cache(maxsize=1)
def get_bnamericas_app_settings() -> BnamericasAppSettings:
    """Configuración de BNamericas App (app.bnamericas.com).

    BNAMERICAS_APP_FILTER_PATH: path relativo del filtro de artículos, sin dominio.
        Ejemplo: article/filter/xg9st1226-de-chile-o-4-mas-acerca-de-energia-electrica-o-4-mas
        La URL completa se construye como https://app.bnamericas.com/<path>.
    BNAMERICAS_APP_LOGIN_URL:   URL de login (por defecto https://login.bnamericas.com/?application=app).
    """
    load_project_env()
    return BnamericasAppSettings(
        filter_path=os.getenv(
            "BNAMERICAS_APP_FILTER_PATH",
            "article/filter/xg9st1226-de-chile-o-4-mas-acerca-de-energia-electrica-o-4-mas",
        ),
        login_url=os.getenv(
            "BNAMERICAS_APP_LOGIN_URL",
            "https://login.bnamericas.com/?application=app",
        ),
    )


@dataclass(frozen=True)
class ScoringSettings:
    contrato:                int
    empresa_conocida:        int
    empresa_noticia:         int
    concepto_sectorial:      int
    reciente_3dias:          int
    reciente_hoy:            int
    entrevista_penalizacion:     int
    filtro_politico_educacional: int
    encuentro_proveedores:       int
    max_candidatas_ia:           int
    max_noticias_por_batch:      int


@lru_cache(maxsize=1)
def get_scoring_settings() -> ScoringSettings:
    """
    Valores por defecto del sistema de scoring.
    Actúan como fallback cuando la tabla score_reglas de la DB no tiene el código.
    La DB siempre tiene prioridad: reglas.get("contrato", settings.contrato).
    """
    load_project_env()
    return ScoringSettings(
        contrato=int(os.getenv("SCORE_CONTRATO", 250)),
        empresa_conocida=int(os.getenv("SCORE_EMPRESA_CONOCIDA", 150)),
        empresa_noticia=int(os.getenv("SCORE_EMPRESA_NOTICIA", 80)),
        concepto_sectorial=int(os.getenv("SCORE_CONCEPTO_SECTORIAL", 80)),
        reciente_3dias=int(os.getenv("SCORE_RECIENTE_3DIAS", 60)),
        reciente_hoy=int(os.getenv("SCORE_RECIENTE_HOY", 25)),
        entrevista_penalizacion=int(os.getenv("SCORE_ENTREVISTA_PENALIZACION", -40)),
        filtro_politico_educacional=int(os.getenv("SCORE_FILTRO_POLITICO_EDUCACIONAL", -1000)),
        encuentro_proveedores=int(os.getenv("SCORE_ENCUENTRO_PROVEEDORES", 110)),
        max_candidatas_ia=int(os.getenv("SCORE_MAX_CANDIDATAS_IA", 60)),
        max_noticias_por_batch=int(os.getenv("SCORE_MAX_NOTICIAS_POR_BATCH", 20)),
    )
