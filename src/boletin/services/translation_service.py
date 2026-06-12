"""
translator.py
-------------
Traduce las noticias seleccionadas preservando el idioma original correcto.

- Artículos en español (idioma_original != "en"):
    titulo/resumen = español (original) → traducir al inglés → titulo_en/resumen_en

- Artículos en inglés (idioma_original == "en"):
    titulo/resumen = inglés (original) → guardar como titulo_en/resumen_en
                                       → traducir al español → titulo/resumen
"""

import json
import logging

from boletin.infrastructure.resilience import retrier
from boletin.config.environment import get_anthropic_client
from boletin.infrastructure.resilience.retrier import TipoError

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-4-5"


def _call_claude(prompt: str, batch_label: str) -> list[dict] | None:
    def _invoke(p=prompt):
        resp = get_anthropic_client().messages.create(
            model=_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": p}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            partes = raw.split("```")
            raw = partes[1].lstrip("json").strip() if len(partes) > 1 else raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Translator: JSON inválido de Claude (%s, primeros 300): %s", batch_label, raw[:300])
            raise ValueError(f"Respuesta no es JSON válido: {e}") from e

    result, ok, _ = retrier.con_reintentos(
        fn=_invoke,
        tipo_error=TipoError.API_IA,
        fuente=batch_label,
        url="api.anthropic.com",
    )
    return result if ok else None


def _build_to_en_prompt(items: list[tuple[int, dict]]) -> str:
    lines = [
        f'{local_i}. TÍTULO: {n["titulo"]}\n   RESUMEN: {n["resumen"] or "(sin resumen)"}'
        for local_i, (_, n) in enumerate(items)
    ]
    return f"""Translate the following mining and energy news headlines and summaries from Spanish to English.
Maintain technical terminology. Keep translations concise and professional.

NEWS TO TRANSLATE:
{chr(10).join(lines)}

Respond ONLY with a JSON array in this exact format, no additional text:
[
  {{"indice": 0, "titulo_en": "...", "resumen_en": "..."}},
  {{"indice": 1, "titulo_en": "...", "resumen_en": "..."}},
  ...
]
"""


def _build_to_es_prompt(items: list[tuple[int, dict]]) -> str:
    lines = [
        f'{local_i}. TITLE: {n["titulo_en"]}\n   SUMMARY: {n["resumen_en"] or "(no summary)"}'
        for local_i, (_, n) in enumerate(items)
    ]
    return f"""Translate the following mining and energy news headlines and summaries from English to Spanish.
Maintain technical terminology. Keep translations concise and professional.

NEWS TO TRANSLATE:
{chr(10).join(lines)}

Respond ONLY with a JSON array in this exact format, no additional text:
[
  {{"indice": 0, "titulo_es": "...", "resumen_es": "..."}},
  {{"indice": 1, "titulo_es": "...", "resumen_es": "..."}},
  ...
]
"""


def traducir(noticias: list[dict], batch_size: int = 15) -> list[dict]:
    """
    Traduce título y resumen de cada noticia preservando el idioma original.
    - Noticias en español: genera titulo_en / resumen_en
    - Noticias en inglés: guarda el original como titulo_en / resumen_en,
      luego genera titulo / resumen en español
    """
    resultado = list(noticias)

    en_idx = [i for i, n in enumerate(resultado) if n.get("idioma_original") == "en"]
    es_idx = [i for i, n in enumerate(resultado) if n.get("idioma_original") != "en"]

    # ── Artículos en inglés: guardar original → traducir a español ────────────
    for i in en_idx:
        resultado[i]["titulo_en"]  = resultado[i]["titulo"]
        resultado[i]["resumen_en"] = resultado[i].get("resumen") or ""

    for batch_start in range(0, len(en_idx), batch_size):
        batch_globals = en_idx[batch_start: batch_start + batch_size]
        items = [(g, resultado[g]) for g in batch_globals]
        label = f"Claude traduccion EN→ES batch {batch_start // batch_size + 1}"
        traducciones = _call_claude(_build_to_es_prompt(items), label)
        if traducciones:
            for t in traducciones:
                g = batch_globals[t["indice"]]
                resultado[g]["titulo"]  = t.get("titulo_es", resultado[g]["titulo"])
                resultado[g]["resumen"] = t.get("resumen_es", resultado[g]["resumen"])
            logger.info("%s: %d artículos traducidos al español", label, len(batch_globals))
        else:
            logger.warning("%s: sin traducción — se mantiene título en inglés", label)

    # ── Artículos en español: traducir a inglés (comportamiento original) ─────
    for batch_start in range(0, len(es_idx), batch_size):
        batch_globals = es_idx[batch_start: batch_start + batch_size]
        items = [(g, resultado[g]) for g in batch_globals]
        label = f"Claude traduccion ES→EN batch {batch_start // batch_size + 1}"
        traducciones = _call_claude(_build_to_en_prompt(items), label)
        if traducciones:
            for t in traducciones:
                g = batch_globals[t["indice"]]
                resultado[g]["titulo_en"]  = t.get("titulo_en", "")
                resultado[g]["resumen_en"] = t.get("resumen_en", "")
            logger.info("%s: %d artículos traducidos al inglés", label, len(batch_globals))
        else:
            logger.warning("%s: sin traducción — se usará texto original", label)

    return resultado
