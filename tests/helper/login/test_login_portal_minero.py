from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
Diagnóstico de login + CAPTCHA para Portal Minero.

Ejecutar desde boletin/:
    python test_login_portal_minero.py

Genera screenshots en tests/output/login/debug_portal_minero_*.png
"""
import base64
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from PIL import Image
from playwright.sync_api import sync_playwright

# ── Setup ─────────────────────────────────────────────────────────────────────
load_dotenv(ROOT_DIR / '.env')
log = logging.getLogger("test_portal_minero")

SCREENSHOTS_DIR = ROOT_DIR / "tests" / "output" / "login"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTADO_JSON_PATH = SCREENSHOTS_DIR / "test_login_portal_minero_resultado.json"

LOGIN_URL      = "https://www.portalminero.com/login.action?os_destination=%2Fdisplay%2Facce"
POST_LOGIN_URL = "https://www.portalminero.com/display/acce"


def setup_logging() -> Path:
    """
    Configura logging solo cuando se ejecuta el diagnóstico real,
    evitando truncar el archivo al importar este módulo desde tests.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
        for h in root.handlers
    ):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    log_file = SCREENSHOTS_DIR / "test_login_portal_minero.log"
    existing_file_handler = next(
        (
            h for h in root.handlers
            if isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == log_file
        ),
        None,
    )
    if existing_file_handler is None:
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8", mode="w")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return log_file


def _guardar_resultado_json(resultado: dict) -> None:
    RESULTADO_JSON_PATH.write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Resultado JSON guardado: %s", RESULTADO_JSON_PATH)

# Lee credenciales de la DB o pide por input si no están
def _get_credenciales() -> tuple[str, str]:
    try:
        from boletin import db
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT usuario, clave, login_url, post_login_url
                    FROM fuentes
                    WHERE lower(nombre) LIKE '%portal minero%'
                      AND usuario IS NOT NULL
                    LIMIT 1
                """)
                row = cur.fetchone()
        if row:
            usuario, clave, login_db, post_db = row
            log.info("Credenciales leídas desde DB — usuario: %s | login_url: %s | post_login_url: %s",
                     usuario, login_db, post_db)
            return usuario, clave, login_db or LOGIN_URL, post_db or POST_LOGIN_URL
    except Exception as exc:
        log.warning("No se pudo leer credenciales desde DB: %s", exc)

    usuario  = input("Usuario Portal Minero: ").strip()
    clave    = input("Clave: ").strip()
    return usuario, clave, LOGIN_URL, POST_LOGIN_URL


def _screenshot(page, nombre: str) -> None:
    path = SCREENSHOTS_DIR / f"debug_portal_minero_{nombre}.png"
    page.screenshot(path=str(path), full_page=False)
    log.info("Screenshot guardado: %s", path)


_easyocr_reader = None

_CAPTCHA_MIN_CONFIDENCE = 0.70   # confianza mínima por token
_CAPTCHA_ONLY_LETTERS   = True    # rechazar si hay dígitos o símbolos


def _try_ocr(img_bytes: bytes) -> str | None:
    """
    Intenta leer el CAPTCHA con EasyOCR.
    Retorna None si el resultado tiene baja confianza o caracteres inválidos,
    para forzar el fallback a Claude Vision.
    """
    global _easyocr_reader
    try:
        import easyocr
        if _easyocr_reader is None:
            log.info("Inicializando EasyOCR (primera vez puede demorar)...")
            _easyocr_reader = easyocr.Reader(["en"], verbose=False)

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        log.info("Imagen CAPTCHA: %dx%d → upscale a %dx%d", w, h, w*3, h*3)

        result = _easyocr_reader.readtext(np.array(img))
        log.info("EasyOCR resultado RAW: %s", [(r[1], round(float(r[2]), 3)) for r in result])

        if not result:
            log.info("EasyOCR: sin resultado")
            return None

        # Verificar confianza mínima en cada token
        confianzas = [float(r[2]) for r in result]
        min_conf = min(confianzas)
        avg_conf = sum(confianzas) / len(confianzas)
        if min_conf < _CAPTCHA_MIN_CONFIDENCE:
            log.info("EasyOCR: confianza baja (min=%.3f avg=%.3f) → fallback a Claude", min_conf, avg_conf)
            return None

        texto = "".join(r[1] for r in result).strip().replace(" ", "")

        # Verificar que sea solo letras (sin dígitos ni símbolos)
        if _CAPTCHA_ONLY_LETTERS and not texto.isalpha():
            log.info("EasyOCR: resultado '%s' contiene no-letras → fallback a Claude", texto)
            return None

        log.info("EasyOCR: resultado aceptado '%s' (min_conf=%.3f)", texto, min_conf)
        return texto

    except Exception as exc:
        log.warning("EasyOCR falló: %s", exc, exc_info=True)
        return None


# Prompts para los 2 intentos con Claude Vision
_CLAUDE_PROMPTS = [
    (
        "This is a CAPTCHA image. The characters are spaced apart and may include "
        "both letters (a-z) and digits (0-9). "
        "Read each character carefully left to right. "
        "Watch out for these common confusions in this font: "
        "'1' vs 'l' vs 'i', '0' vs 'o', 'v' vs 'y', 'rn' vs 'm'. "
        "Pay attention to repeated characters — some letters appear twice. "
        "Return ONLY the exact characters with no spaces or explanation."
    ),
    (
        "Look at each character in this CAPTCHA individually, left to right. "
        "The text may contain digits (1, 7, 0, etc.) mixed with letters. "
        "Do not confuse: '1'/'l'/'i', 'v'/'y', '0'/'o', 'rn'/'m'. "
        "Do not skip duplicate characters. "
        "Return ONLY the characters, nothing else."
    ),
]


def _upscale_png(img_bytes: bytes, scale: int = 3) -> bytes:
    """
    Upscalea la imagen, aumenta contraste y la binariza para facilitar la lectura OCR.
    """
    from PIL import ImageEnhance, ImageFilter
    img = Image.open(io.BytesIO(img_bytes)).convert("L")   # escala de grises
    # Aumentar contraste
    img = ImageEnhance.Contrast(img).enhance(2.5)
    # Sharpening
    img = img.filter(ImageFilter.SHARPEN)
    # Upscale
    w, h = img.size
    img = img.resize((w * scale, h * scale), Image.LANCZOS)
    # Convertir a RGB para PNG
    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _try_claude_vision(img_bytes: bytes, intento: int = 1) -> str | None:
    """
    Envía la imagen del CAPTCHA a la API de Claude y devuelve el texto leído.
    La imagen se upscalea 3x antes de enviar para mejorar la precisión.
    intento: 1 o 2 — usa prompts distintos para maximizar la precisión.
    """
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        if not api_key:
            log.warning("Claude Vision: ANTHROPIC_API_KEY no encontrada en .env")
            return None

        client = anthropic.Anthropic(api_key=api_key)
        prompt_idx = min(intento - 1, len(_CLAUDE_PROMPTS) - 1)
        prompt = _CLAUDE_PROMPTS[prompt_idx]

        img_upscaled = _upscale_png(img_bytes, scale=3)
        # Guardar imagen procesada para inspección
        proc_path = SCREENSHOTS_DIR / f"debug_portal_minero_captcha_proc_{intento}.png"
        proc_path.write_bytes(img_upscaled)
        log.info("Imagen procesada guardada: %s", proc_path)

        img_b64 = base64.standard_b64encode(img_upscaled).decode("utf-8")
        log.info("Claude Vision intento %d — enviando imagen procesada (%d bytes)...", intento, len(img_upscaled))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=32,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        texto = response.content[0].text.strip().replace(" ", "")
        log.info("Claude Vision intento %d → '%s'", intento, texto)
        return texto if texto else None

    except Exception as exc:
        log.warning("Claude Vision falló (intento %d): %s", intento, exc, exc_info=True)
        return None


def _detectar_y_resolver_captcha(page, nombre_fuente: str) -> tuple[bool, str | None]:
    """
    Retorna (tiene_captcha, texto_resuelto_o_None).
    Detecta por selector DOM (más fiable que buscar en el HTML crudo).
    """
    # Detección por DOM — no depende del tamaño del HTML
    captcha_img_selectors = [
        "img.captcha-image",           # Portal Minero: class="captcha-image"
        "img[src*='jcaptcha']",        # Portal Minero: src="/jcaptcha"
        "img[src*='captcha' i]",
        "img[id*='captcha' i]",
        "img[class*='captcha' i]",
        "#captchaImg",
        "img[alt*='captcha' i]",
        ".captcha img",
        "td:has(input[name='captchaResponse']) img",
        "td:has(input[name='os_captcha']) img",
    ]
    captcha_input_selectors = [
        "input[name='captchaResponse']",   # Portal Minero
        "input[id='captcha-response']",
        "input[name='os_captcha']",
        "input[name='captcha']",
        "input[id='captcha']",
        "input[id*='captcha' i]",
        "input[name*='captcha' i]",
    ]

    # ¿Hay campo de CAPTCHA en el DOM?
    tiene_campo = any(
        page.locator(s).count() > 0 for s in captcha_input_selectors
    )
    # ¿Hay imagen de CAPTCHA en el DOM?
    tiene_imagen_dom = any(
        page.locator(s).count() > 0 for s in captcha_img_selectors
    )
    # Fallback: buscar en el HTML (primer tramo)
    html_chunk = page.content()[:60000].lower()
    tiene_en_html = any(t in html_chunk for t in (
        "captcha", "validate your login", "you are required to validate",
    ))

    tiene_captcha = tiene_campo or tiene_imagen_dom or tiene_en_html
    if not tiene_captcha:
        return False, None

    log.info("[%s] CAPTCHA detectado (campo=%s, img_dom=%s, html=%s)",
             nombre_fuente, tiene_campo, tiene_imagen_dom, tiene_en_html)

    for sel in captcha_img_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                log.info("[%s] Imagen CAPTCHA encontrada: %s", nombre_fuente, sel)
                img_bytes = loc.screenshot()
                captcha_path = SCREENSHOTS_DIR / "debug_portal_minero_captcha.png"
                captcha_path.write_bytes(img_bytes)
                log.info("Imagen CAPTCHA guardada: %s", captcha_path)

                texto = _try_ocr(img_bytes)
                log.info("[%s] EasyOCR resultado: '%s'", nombre_fuente, texto)

                # Fallback: Claude Vision (hasta 2 intentos) si EasyOCR no leyó nada
                if not texto:
                    log.info("[%s] EasyOCR sin resultado — probando Claude Vision...", nombre_fuente)
                    for claude_intento in range(1, 3):
                        texto = _try_claude_vision(img_bytes, intento=claude_intento)
                        if texto:
                            log.info("[%s] Claude Vision resolvió CAPTCHA (intento %d): '%s'",
                                     nombre_fuente, claude_intento, texto)
                            break
                    else:
                        log.warning("[%s] Claude Vision tampoco resolvió el CAPTCHA", nombre_fuente)

                log.info("[%s] Texto final para CAPTCHA: '%s'", nombre_fuente, texto)
                return True, texto
        except Exception as e:
            log.debug("Selector '%s' error: %s", sel, e)

    log.warning("[%s] CAPTCHA detectado pero no se encontró imagen — listando:", nombre_fuente)
    _listar_imagenes(page)
    return True, None


def _listar_campos_formulario(page) -> None:
    """Log de todos los inputs visibles en la página."""
    campos = page.eval_on_selector_all(
        "input",
        """inputs => inputs.map(i => ({
            type: i.type,
            name: i.name,
            id: i.id,
            placeholder: i.placeholder,
            visible: i.offsetParent !== null
        }))"""
    )
    log.info("Campos en formulario (%d total):", len(campos))
    for c in campos:
        log.info("  type=%-12s name=%-20s id=%-20s visible=%s  placeholder=%s",
                 c.get("type",""), c.get("name",""), c.get("id",""),
                 c.get("visible",""), c.get("placeholder",""))


def _listar_imagenes(page) -> None:
    """Log de TODAS las <img> en la página — útil para encontrar el selector del CAPTCHA."""
    imgs = page.eval_on_selector_all(
        "img",
        """imgs => imgs.map(i => ({
            src: i.src,
            id: i.id,
            cls: i.className,
            alt: i.alt,
            w: i.naturalWidth,
            h: i.naturalHeight
        }))"""
    )
    log.info("=== IMÁGENES EN PÁGINA (%d) ===", len(imgs))
    for i in imgs:
        log.info("  src=%-60s id=%-15s cls=%-20s alt=%-15s size=%sx%s",
                 (i.get("src") or "")[:60],
                 i.get("id",""), i.get("cls","")[:20],
                 i.get("alt",""), i.get("w","?"), i.get("h","?"))


def run_diagnostico():
    setup_logging()
    usuario, clave, login_url, post_login_url = _get_credenciales()
    resultado = {
        "timestamp_inicio": time.strftime("%Y-%m-%d %H:%M:%S"),
        "login_url": login_url,
        "post_login_url": post_login_url,
        "usuario_input": usuario,
        "login_exitoso": False,
        "remote_user": "",
        "url_final": "",
        "titulo_final": "",
        "captcha_detectado": False,
        "captcha_resuelto": False,
        "intentos_login": 0,
        "selector_ganador": None,
        "noticias_encontradas": 0,
        "noticias_preview": [],
        "estado": "iniciado",
        "error": None,
    }
    log.info("=== INICIO DIAGNOSTICO PORTAL MINERO ===")
    log.info("LOGIN URL      : %s", login_url)
    log.info("POST LOGIN URL : %s", post_login_url)
    log.info("Usuario        : %s", usuario)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        try:
            log.info("--- Paso 1: Navegar a login ---")
            page.goto(login_url, timeout=30_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            log.info("URL actual: %s | Titulo: %s", page.url, page.title())
            _screenshot(page, "1_login_page")
            _listar_campos_formulario(page)

            log.info("--- Paso 2: Detectar CAPTCHA ---")
            _listar_imagenes(page)
            tiene_captcha, texto_captcha = _detectar_y_resolver_captcha(page, "PortalMinero")
            resultado["captcha_detectado"] = bool(tiene_captcha)
            resultado["captcha_resuelto"] = bool(texto_captcha)

            log.info("--- Paso 3: Rellenar credenciales ---")
            user_selectors = [
                "input[name='os_username']",
                "input[type='email']", "input[name='email']",
                "input[name='username']", "input[name='user']",
                "input[id='os_username']", "input[id='email']",
                "input[id='username']",
            ]
            campo_usuario_ok = False
            for sel in user_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.fill(usuario)
                    campo_usuario_ok = True
                    log.info("Campo usuario rellenado con selector: %s", sel)
                    break
            if not campo_usuario_ok:
                log.error("No se encontr? campo de usuario")

            loc_pass = page.locator("input[type='password']").first
            if loc_pass.count() > 0:
                loc_pass.fill(clave)
                log.info("Campo contrase?a rellenado")
            else:
                log.error("No se encontr? campo de contrase?a")

            if tiene_captcha:
                if texto_captcha:
                    captcha_selectors = [
                        "input[name='captchaResponse']",
                        "input[id='captcha-response']",
                        "input[name='os_captcha']",
                        "input[name='captcha']",
                        "input[id='captcha']",
                        "input[id*='captcha' i]",
                        "input[name*='captcha' i]",
                    ]
                    captcha_filled = False
                    for sel in captcha_selectors:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            loc.fill(texto_captcha)
                            captcha_filled = True
                            log.info("Campo CAPTCHA rellenado (%s) con '%s'", sel, texto_captcha)
                            break
                    if not captcha_filled:
                        log.error("No se encontr? campo de entrada del CAPTCHA")
                else:
                    log.error("OCR no resolvi? el CAPTCHA ? el submit probablemente fallar?")

            _screenshot(page, "2_form_filled")

            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "input[name='login']",
                "button:has-text('Autenticarse')", "button:has-text('Ingresar')",
                "button:has-text('Login')", "button:has-text('Log in')",
                "button:has-text('Sign in')", "button:has-text('Acceder')",
            ]
            captcha_input_selectors = [
                "input[name='captchaResponse']",
                "input[id='captcha-response']",
                "input[name='os_captcha']",
                "input[name='captcha']",
                "input[id='captcha']",
                "input[id*='captcha' i]",
                "input[name*='captcha' i]",
            ]
            max_intentos = 5
            url_post_submit = page.url
            remote_user = ""

            for intento in range(1, max_intentos + 1):
                resultado["intentos_login"] = intento
                log.info("--- Submit intento %d/%d | url=%s ---", intento, max_intentos, page.url)

                if intento > 1:
                    log.info("Reintento: re-llenando formulario en la misma p?gina (con CAPTCHA visible)")
                    for sel in user_selectors:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            try:
                                loc.clear()
                            except Exception:
                                pass
                            loc.fill(usuario)
                            log.info("Campo usuario re-llenado (%s)", sel)
                            break
                    loc_p2 = page.locator("input[type='password']").first
                    if loc_p2.count() > 0:
                        try:
                            loc_p2.clear()
                        except Exception:
                            pass
                        loc_p2.fill(clave)
                        log.info("Campo contrase?a re-llenado")

                tiene_cap_retry, texto_cap_retry = _detectar_y_resolver_captcha(page, "PortalMinero")
                resultado["captcha_detectado"] = bool(resultado["captcha_detectado"] or tiene_cap_retry)
                resultado["captcha_resuelto"] = bool(resultado["captcha_resuelto"] or texto_cap_retry)
                if tiene_cap_retry:
                    if texto_cap_retry:
                        for sel in captcha_input_selectors:
                            loc = page.locator(sel).first
                            if loc.count() > 0:
                                loc.fill(texto_cap_retry)
                                log.info("Campo CAPTCHA rellenado (%s) con '%s'", sel, texto_cap_retry)
                                break
                    else:
                        log.warning("OCR no resolvi? CAPTCHA en intento %d ? igual intentamos submit", intento)

                submitted = False
                for sel in submit_selectors:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        log.info("Bot?n submit: %s", sel)
                        loc.click()
                        submitted = True
                        break
                if not submitted:
                    log.warning("No se encontr? bot?n submit ? presionando Enter")
                    page.keyboard.press("Enter")

                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                time.sleep(2)

                url_post_submit = page.url
                _screenshot(page, f"submit_intento_{intento}")
                log.info("URL tras submit: %s | Titulo: %s", url_post_submit, page.title())

                still_captcha = (
                    page.locator("img.captcha-image").count() > 0
                    or page.locator("img[src*='jcaptcha']").count() > 0
                    or page.locator("input[name='captchaResponse']").count() > 0
                    or page.locator("input[name='os_captcha']").count() > 0
                )
                still_on_login = any(s in url_post_submit.lower() for s in (
                    "login.action", "dologin.action",
                ))
                needs_retry = still_captcha or still_on_login

                try:
                    remote_user = page.get_attribute('meta[name="ajs-remote-user"]', 'content') or ""
                except Exception:
                    remote_user = ""

                log.info("still_captcha=%s | still_on_login=%s | remote_user='%s'",
                         still_captcha, still_on_login, remote_user)

                if remote_user:
                    resultado["login_exitoso"] = True
                    resultado["remote_user"] = remote_user
                    log.info("LOGIN EXITOSO en intento %d | usuario: %s", intento, remote_user)
                    break

                if needs_retry:
                    log.warning("Reintento necesario en intento %d (captcha=%s, en_login=%s)",
                                intento, still_captcha, still_on_login)
                    _listar_imagenes(page)
                    _listar_campos_formulario(page)
                    if intento < max_intentos:
                        continue
                    log.error("Login no exitoso tras %d intentos", max_intentos)
                    break

                log.error("Credenciales inv?lidas o estado inesperado (intento %d)", intento)
                break

            log.info("=== RESULTADO FINAL ===")
            log.info("URL final         : %s", url_post_submit)
            log.info("ajs-remote-user   : '%s' (%s)", remote_user,
                     "AUTENTICADO" if remote_user else "ANONIMO")
            resultado["url_final"] = url_post_submit
            resultado["remote_user"] = remote_user
            try:
                resultado["titulo_final"] = page.title()
            except Exception:
                resultado["titulo_final"] = ""

            if remote_user:
                log.info("--- Paso 7: Navegar a post_login_url ---")
                if post_login_url.rstrip("/") != url_post_submit.rstrip("/"):
                    page.goto(post_login_url, timeout=30_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except Exception:
                        pass
                    time.sleep(2)
                log.info("URL en post_login_url: %s | Titulo: %s", page.url, page.title())
                resultado["url_final"] = page.url
                resultado["titulo_final"] = page.title()
                _screenshot(page, "4_post_login_content")

                article_selectors = [
                    "article h2 a", "article h3 a", ".article-title a",
                    "h2 a", "h3 a", "main article a",
                ]
                noticias_preview = []
                for sel in article_selectors:
                    count = page.locator(sel).count()
                    if count:
                        resultado["selector_ganador"] = sel
                        resultado["noticias_encontradas"] = count
                        log.info("Selector '%s' encontr? %d links", sel, count)
                        for i in range(min(5, count)):
                            loc = page.locator(sel).nth(i)
                            titulo = (loc.text_content() or "").strip()[:80]
                            href = loc.get_attribute("href") or ""
                            noticias_preview.append({"titulo": titulo, "url": href})
                            log.info("  [%d] %s -> %s", i + 1, titulo, href)
                        resultado["noticias_preview"] = noticias_preview
                        break
                else:
                    log.warning("Ning?n selector encontr? noticias en: %s", page.url)
            else:
                resultado["selector_ganador"] = None
                resultado["noticias_encontradas"] = 0
                resultado["noticias_preview"] = []

            resultado["estado"] = "ok"

        except Exception as exc:
            resultado["estado"] = "error"
            resultado["error"] = str(exc)
            log.exception("Error inesperado: %s", exc)
            _screenshot(page, "error")
        finally:
            try:
                if not resultado.get("url_final"):
                    resultado["url_final"] = page.url
                if not resultado.get("titulo_final"):
                    resultado["titulo_final"] = page.title()
            except Exception:
                pass
            resultado["timestamp_fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _guardar_resultado_json(resultado)
            input("\\nPresiona Enter para cerrar el browser...")
            browser.close()

    log.info("=== FIN DIAGNOSTICO ===")
    log.info("Screenshots en: %s", SCREENSHOTS_DIR)
    return resultado


if __name__ == "__main__":
    run_diagnostico()
