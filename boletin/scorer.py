"""
scorer.py
---------
Usa Claude para puntuar cada noticia según las prioridades del cliente:
  +250 pts — contrato / licitación / adjudicación
  +150 pts — contrato de empresa conocida (lista HIGH_VALUE_COMPANIES)
   +80 pts — noticia de empresa importante
   +60 pts — noticia de hace 3 días o menos
   +25 pts — noticia de hoy

Luego selecciona el top 30 respetando la cuota por país (10 Chile, 10 Perú, 10 Argentina).
Las noticias internacionales se distribuyen si sobra cupo.
"""

import json
import logging
from datetime import datetime, timezone

import anthropic

import db
import retrier
from retrier import TipoError

logger = logging.getLogger(__name__)

client = anthropic.Anthropic()  # Lee ANTHROPIC_API_KEY del entorno

MAX_NOTICIAS_POR_BATCH = 20   # Para no superar contexto en un solo llamado


# ── Caché de configuración (se carga una vez por ejecución) ─────────────────
_SCORE_CONFIG: dict | None = None

def _get_score_config() -> dict:
    """
    Carga la configuración de scoring desde la DB una sola vez por ejecución.
    Cachea el resultado para no hacer múltiples queries innecesarias.
    """
    global _SCORE_CONFIG
    if _SCORE_CONFIG is None:
        _SCORE_CONFIG = db.get_score_config()
    return _SCORE_CONFIG


# ── Scoring local (rápido, sin API) ──────────────────────────────────────────

def _score_local(noticia: dict) -> int:
    """
    Pre-scoring local usando las tres tablas de la DB:

      score_empresas           → empresa_noticia   (+80): empresa mencionada en cualquier contexto
      score_empresas_conocidas → empresa_conocida  (+150): empresa que FIRMA el contrato
      score_keywords           → contrato          (+250): detecta licitaciones/adjudicaciones
      score_reglas             → pesos configurables para cada criterio

    Una empresa puede estar en ambas tablas. Si una noticia menciona a Bechtel
    en el contexto de un contrato y Bechtel está en ambas listas:
      +250 (contrato) + +80 (empresa_noticia) + +150 (empresa_conocida) = +480
    """
    cfg                = _get_score_config()
    reglas             = cfg["reglas"]
    empresas           = cfg["empresas"]
    empresas_conocidas = cfg["empresas_conocidas"]
    keywords           = cfg["keywords"]

    score = 0
    texto = (noticia["titulo"] + " " + noticia["resumen"]).lower()

    # ── Contratos / licitaciones ──────────────────────────────────────────────
    # Fuente: score_keywords (tabla DB) — puntaje: score_reglas.contrato
    tiene_contrato = any(kw.lower() in texto for kw in keywords)
    if tiene_contrato:
        score += reglas.get("contrato", 250)

    # ── Empresa importante mencionada ─────────────────────────────────────────
    # Fuente: score_empresas (tabla DB) — puntaje: score_reglas.empresa_noticia
    # Aplica a cualquier noticia que mencione la empresa, con o sin contrato
    tiene_empresa = any(e.lower() in texto for e in empresas)
    if tiene_empresa:
        score += reglas.get("empresa_noticia", 80)

    # ── Empresa conocida que firma el contrato ────────────────────────────────
    # Fuente: score_empresas_conocidas (tabla DB) — puntaje: score_reglas.empresa_conocida
    # Solo aplica cuando hay un contrato detectado Y la empresa está en esta lista
    if tiene_contrato:
        tiene_empresa_conocida = any(e.lower() in texto for e in empresas_conocidas)
        if tiene_empresa_conocida:
            score += reglas.get("empresa_conocida", 150)

    # ── Antigüedad ────────────────────────────────────────────────────────────
    # Fuente: score_reglas.reciente_hoy y reciente_3dias
    try:
        fecha = datetime.fromisoformat(noticia["fecha"])
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - fecha
        if delta.days == 0:
            score += reglas.get("reciente_hoy", 25) + reglas.get("reciente_3dias", 60)
        elif delta.days <= 3:
            score += reglas.get("reciente_3dias", 60)
    except Exception:
        pass

    return score


# ── Scoring con Claude (semántico) ───────────────────────────────────────────

def _build_prompt(noticias: list[dict]) -> str:
    """Construye el prompt para Claude usando los pesos desde la DB."""
    cfg                = _get_score_config()
    reglas             = cfg["reglas"]
    empresas_conocidas = cfg["empresas_conocidas"]

    items = []
    for i, n in enumerate(noticias):
        items.append(
            f'{i}. TÍTULO: {n["titulo"]}\n'
            f'   RESUMEN: {n["resumen"] or "(sin resumen)"}\n'
            f'   FECHA: {n["fecha"]}\n'
            f'   FUENTE: {n["fuente"]} ({n["pais"]})')

    p_contrato  = reglas.get("contrato", 250)
    p_emp_cont  = reglas.get("empresa_conocida", 150)
    p_empresa   = reglas.get("empresa_noticia", 80)
    p_3dias     = reglas.get("reciente_3dias", 60)
    p_hoy       = reglas.get("reciente_hoy", 25)

    # Lista de empresas conocidas para el prompt (hasta 20 para no saturar el contexto)
    lista_emp = ", ".join(empresas_conocidas[:20])
    if len(empresas_conocidas) > 20:
        lista_emp += f" y {len(empresas_conocidas) - 20} más"

    return f"""Eres un analista experto en minería y energía de Latinoamérica.
Tu tarea es puntuar estas noticias para un boletín ejecutivo sobre minería y energía en Chile, Perú y Argentina.

SISTEMA DE PUNTUACIÓN (acumulativo, pesos configurados desde base de datos):
- +{p_contrato} pts: Noticia sobre contrato firmado, licitación adjudicada o concesión otorgada
- +{p_emp_cont} pts: El contrato involucra una empresa conocida del sector. Lista actual: {lista_emp}
- +{p_empresa} pts:  Noticia relevante sobre empresa importante del sector (sin ser contrato)
- +{p_3dias} pts:  Noticia reciente (3 días o menos desde hoy)
- +{p_hoy} pts:  Noticia de hoy
- 0 pts:    Noticia irrelevante, muy general, o sin relación con minería/energía

NOTICIAS A EVALUAR:
{chr(10).join(items)}

Responde ÚNICAMENTE con un JSON array con este formato exacto, sin texto adicional:
[
  {{"indice": 0, "score": 330, "razon": "Contrato EPC adjudicado a Bechtel en proyecto minero"}},
  {{"indice": 1, "score": 0,   "razon": "Noticia genérica sin relevancia directa"}},
  ...
]
"""


def _score_con_claude(noticias: list[dict]) -> list[dict]:
    """
    Llama a Claude en batches para puntuar semánticamente las noticias.
    Registra ia_ok=TRUE por fuente solo cuando Claude responde con JSON valido
    (confirmando que hubo cobro real). Si falla, ia_ok queda FALSE
    y se reintentara en la proxima ejecucion del mismo dia.
    """
    resultado = list(noticias)
    fuentes_ia_ok: set[str] = set()
    fuentes_ia_fail: set[str] = set()

    for i in range(0, len(noticias), MAX_NOTICIAS_POR_BATCH):
        batch        = noticias[i: i + MAX_NOTICIAS_POR_BATCH]
        prompt       = _build_prompt(batch)
        # fuentes_batch: URLs de fuentes (campo url_fuente) para registrar_ia en DB
        fuentes_batch = {n.get("url_fuente", n["fuente"]) for n in batch}
        fuente_rep   = f"Claude scorer batch {i//MAX_NOTICIAS_POR_BATCH + 1}"

        def _llamar_claude(p=prompt):
            resp = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": p}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)  # lanza JSONDecodeError si respuesta inválida

        scores_data, ok = retrier.con_reintentos(
            fn=_llamar_claude,
            tipo_error=TipoError.API_IA,
            fuente=fuente_rep,
            url="api.anthropic.com",
        )

        if ok and scores_data:
            for item in scores_data:
                idx_global = i + item["indice"]
                resultado[idx_global]["score"] = item.get("score", 0)
                resultado[idx_global]["razon"] = item.get("razon", "")
            fuentes_ia_ok.update(fuentes_batch)
        else:
            fuentes_ia_fail.update(fuentes_batch)

    # Registra resultado IA por fuente (url_fuente = URL de la fuente en DB, no de la noticia)
    for url_fuente in fuentes_ia_ok - fuentes_ia_fail:
        n_ok = sum(1 for n in noticias if n.get("url_fuente", "") == url_fuente and n.get("score", 0) > 0)
        db.registrar_ia(url_fuente=url_fuente, ok=True, noticias_enviadas=n_ok)
    for url_fuente in fuentes_ia_fail:
        db.registrar_ia(url_fuente=url_fuente, ok=False, error="Error en scoring Claude")

    return resultado


# ── Selección del top 30 ──────────────────────────────────────────────────────

def seleccionar_top_noticias(noticias: list[dict]) -> list[dict]:
    """
    Selecciona las mejores noticias respetando la cuota por país definida en la DB.
    Carga los países activos desde `paises` — completamente dinámico.
    Si se agrega un país nuevo en la DB, el próximo boletín ya lo incluye.

    Estrategia:
      1. Carga cuotas desde db.get_paises_activos()
      2. Ordena por score desc dentro de cada país activo
      3. Toma hasta cuota por país
      4. Completa con fuentes Internacionales si hay déficit
    """
    paises = db.get_paises_activos()
    nombres_paises = {p["nombre"] for p in paises}
    cuotas = {p["nombre"]: p["cuota"] for p in paises}
    total_cuota = sum(cuotas.values())

    # Agrupa noticias por país
    por_pais: dict[str, list] = {p["nombre"]: [] for p in paises}
    por_pais["Internacional"] = []

    for n in noticias:
        pais = n["pais"] if n["pais"] in nombres_paises else "Internacional"
        por_pais[pais].append(n)

    # Ordena por score descendente dentro de cada grupo
    for p in por_pais:
        por_pais[p].sort(key=lambda x: x["score"], reverse=True)

    seleccionadas = []
    sobrantes_int = list(por_pais["Internacional"])

    for pais_cfg in paises:
        nombre = pais_cfg["nombre"]
        cuota  = pais_cfg["cuota"]

        top      = por_pais[nombre][:cuota]
        seleccionadas.extend(top)
        faltantes = cuota - len(top)

        # Rellena con internacionales si hay déficit en este país
        if faltantes > 0 and sobrantes_int:
            relleno = sobrantes_int[:faltantes]
            for r in relleno:
                r = dict(r)
                r["pais_boletin"] = nombre
            seleccionadas.extend(relleno)
            sobrantes_int = sobrantes_int[faltantes:]

    logger.info(
        "Selección final: %d noticias (cuota total: %d) — %s",
        len(seleccionadas), total_cuota,
        " | ".join(f"{p['nombre']}:{min(cuotas[p['nombre']], len(por_pais[p['nombre']]))+0}" for p in paises)
    )
    return seleccionadas[:total_cuota]


# ── Entrada pública ───────────────────────────────────────────────────────────

def puntuar_y_seleccionar(noticias: list[dict]) -> list[dict]:
    """
    Pipeline completo:
      1. Pre-scoring local (rápido)
      2. Filtra las top 60 para enviar a Claude (ahorra tokens)
      3. Scoring semántico con Claude
      4. Selección final top 30 con cuotas por país
    """
    logger.info("Iniciando scoring de %d noticias...", len(noticias))

    # Pre-score local
    for n in noticias:
        n["score"] = _score_local(n)

    # Toma las 60 mejores para análisis con IA (reduce costo de tokens)
    candidatas = sorted(noticias, key=lambda x: x["score"], reverse=True)[:60]
    logger.info("Candidatas para Claude: %d", len(candidatas))

    # Scoring semántico
    candidatas = _score_con_claude(candidatas)

    # Selección final (cuotas dinámicas desde DB)
    top30 = seleccionar_top_noticias(candidatas)
    return top30
