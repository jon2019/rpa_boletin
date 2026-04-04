"""
translator.py
-------------
Traduce las 30 noticias seleccionadas al inglés usando Claude.
Opera en un solo llamado por batch para minimizar latencia y costo.
"""

import json
import logging

import anthropic
import retrier
from retrier import TipoError

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()


def _build_translation_prompt(noticias: list[dict]) -> str:
    items = [
        f'{i}. TÍTULO: {n["titulo"]}\n   RESUMEN: {n["resumen"] or "(sin resumen)"}'
        for i, n in enumerate(noticias)
    ]
    return f"""Translate the following mining and energy news headlines and summaries from Spanish to English.
Maintain technical terminology. Keep translations concise and professional.

NEWS TO TRANSLATE:
{chr(10).join(items)}

Respond ONLY with a JSON array in this exact format, no additional text:
[
  {{"indice": 0, "titulo_en": "...", "resumen_en": "..."}},
  {{"indice": 1, "titulo_en": "...", "resumen_en": "..."}},
  ...
]
"""


def traducir(noticias: list[dict], batch_size: int = 15) -> list[dict]:
    """
    Traduce título y resumen de cada noticia al inglés.
    Modifica las noticias in-place y las retorna.
    """
    resultado = list(noticias)

    for i in range(0, len(noticias), batch_size):
        batch = noticias[i: i + batch_size]
        prompt = _build_translation_prompt(batch)

        fuente_rep = f"Claude traduccion batch {i//batch_size + 1}"

        def _traducir(p=prompt):
            resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": p}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)

        traducciones, ok = retrier.con_reintentos(
            fn=_traducir,
            tipo_error=TipoError.API_IA,
            fuente=fuente_rep,
            url="api.anthropic.com",
        )

        if ok and traducciones:
            for t in traducciones:
                idx_global = i + t["indice"]
                resultado[idx_global]["titulo_en"]  = t.get("titulo_en", "")
                resultado[idx_global]["resumen_en"] = t.get("resumen_en", "")
            logger.info("Batch %d-%d traducido OK", i, i + len(batch))
        else:
            logger.warning("Batch %d-%d sin traduccion — se usara texto original", i, i + len(batch))

    return resultado
