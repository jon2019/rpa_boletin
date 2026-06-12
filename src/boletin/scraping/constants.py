"""Constantes compartidas del subsistema de scraping."""

from __future__ import annotations

from boletin.config.environment import get_flaresolverr_settings

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
}

TIMEOUT = 30
_FLARESOLVERR = get_flaresolverr_settings()
FLARESOLVERR_URL = _FLARESOLVERR.url
FLARESOLVERR_MAX_TIMEOUT_MS = _FLARESOLVERR.max_timeout_ms
FLARESOLVERR_WAIT_SECONDS = _FLARESOLVERR.wait_seconds

GENERIC_LINK_SELECTORS = [
    "main article a",
    "article h1 a",
    "article h2 a",
    "article h3 a",
    ".entry-title a",
    ".post-title a",
    ".article-title a",
    ".news-title a",
    ".jeg_post_title a",
    "h2 a",
    "h3 a",
    "h4 a",
    "h5 a",
    "h6 a",
]
MINERIAYDESARROLLO_EXTRA_SELECTORS = [
    "article.noticia-destacada a[href*='/noticias/']",
    "article.noticia-simple a[href*='/noticias/']",
    "a[href*='/noticias/'][aria-label]",
]
MININGWEEKLY_EXTRA_SELECTORS = [
    "a.card-title[href*='/article/']",
    "a.headline-link[href*='/article/']",
    "a.headline-link-projects[href*='/article/']",
    "a.image-link[href*='/article/']",
    "a.image-link-projects[href*='/article/']",
    ".entry a[href*='/article/']",
    ".card-body a[href*='/article/']",
]
MINING_COM_EXTRA_SELECTORS = [
    "h1.entry-title a",
    "h2.entry-title a",
    "h3.entry-title a",
    ".entry-title a",
    ".article-title a",
    ".td_module_flex a.entry-title-link",
    ".td-module-title a",
    "a.jeg_post_title",
    "h3.jeg_post_title a",
    ".media-heading a",
    "article .title a",
]
MININGDIGITAL_EXTRA_SELECTORS = [
    "a[href*='/mining/']",
    "a[href*='/technology/']",
    "a[href*='/sustainability/']",
    "a[href*='/company/']",
    "a[href*='/projects/']",
    ".article-card a",
    "[class*='card'] h3 a",
    "[class*='card'] h2 a",
    "[class*='article'] a",
    "[class*='headline'] a",
]
BNAMERICAS_EXTRA_SELECTORS = [
    "a[href*='/article/content/']",
    "a[href*='/news']",
    "a[href*='/projects']",
    "a[href*='/companies']",
    "a[href*='/updates']",
    "[class*='feed'] a",
    "[class*='content'] a",
    "[class*='update'] a",
    "[class*='card'] a",
    "[class*='list'] a",
]
ENERGIASRENOVABLES_EXTRA_SELECTORS = [
    "a[href*='/fotovoltaica/']",
    "a[href*='/eolica/']",
    "a[href*='/hidrogeno/']",
    "a[href*='/panorama/']",
    "a[href*='/movilidad/']",
    "a[href*='/autoconsumo/']",
    "a[href*='/almacenamiento/']",
    "a[href*='/bioenergia/']",
    "a[href*='/hidraulica/']",
    "a[href*='/geotermica/']",
    "a[href*='/energias_del_mar/']",
    "a[href*='/nuclear/']",
    "a[href*='/biomasa/']",
    "a[href*='/termosolar/']",
    "a[href*='/maremotriz/']",
]
LOGIN_SPA_WAIT_SELECTORS = [
    ".article-title a",
    "h3.article-title a",
    "[class*='article'] a",
    "[class*='news'] a",
    "main article a",
    "article h2 a",
    "article h3 a",
    "h2 a",
    "h3 a",
]
CAPTCHA_TEXT_PATTERNS = (
    "captcha",
    "please enter the word in the captcha image",
    "validate your login by entering the word below",
    "you are required to validate your login",
)
EXCLUDE_URL_PATTERNS = (
    "/tag/",
    "/author/",
    "/category/",
    "/wp-json/",
    "/comments/",
    "/feed",
    "javascript:",
    "#",
)
EXCLUDE_TITLE_PATTERNS = (
    "leer más",
    "read more",
    "ver más",
    "inicio",
    "home",
    "contacto",
    "suscríb",
    "suscrib",
)
BNAMERICAS_EXCLUDE_TITLE_EXACT = {
    "noticias",
    "news",
    "proyectos",
    "projects",
    "compañías",
    "companias",
    "companies",
    "actualizaciones",
    "updates",
}
BNAMERICAS_EXCLUDE_HREF_TOKENS = (
    "/article/section/all",
    "source=sidebar",
    "listtype=section",
    "contenttype=article",
)
BNAMERICAS_HOME_CONTENT_PATH_TOKENS = (
    "/article/content/",
    "/article/section/all/content/",
    "/project/content/",
    "/company/content/",
)
BNAMERICAS_HOME_SECTION_HEADINGS = (
    "feed de noticias y cambios",
    "reportes",
    "lo más visto",
)
FLARESOLVERR_CHALLENGE_TOKENS = (
    "verificaciÃ³n de seguridad en curso",
    "verifique que es un ser humano",
    "checking your browser",
    "cloudflare",
    "verify you are human",
    "un momento",
)
BROWSER_CHALLENGE_TITLES = {"un momento", "just a moment", "please wait", "checking your browser"}
BROWSER_CHALLENGE_BODY_TOKENS = (
    "checking your browser",
    "verify you are human",
    "ray id",
    "cloudflare",
)
