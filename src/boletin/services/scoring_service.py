"""
scorer.py
---------
Usa Claude para puntuar cada noticia con filtro geográfico y de calidad obligatorio.

Pipeline:
  1. Pre-scoring local (keywords + empresas + antigüedad)
  2. Pre-filtro geográfico: internacionales sin mención de CL/PE/AR + sector → score 0
  3. Top 60 candidatas → Claude (semántico, con regla geográfica y filtro de calidad en el prompt)
  4. Selección final respetando cuota por país (10 Chile, 10 Perú, 10 Argentina)
     Internacionales con score > 0 pueden rellenar déficit; sin score > 0 se omiten.
"""

import json
import logging
import re
import threading
from datetime import datetime, timezone, date

from boletin.infrastructure.db import facade as db
from boletin.infrastructure.resilience import retrier
from boletin.config.environment import get_anthropic_client, get_scoring_settings
from boletin.infrastructure.resilience.retrier import TipoError

logger = logging.getLogger(__name__)

# Leídos una sola vez — los valores reales vienen de .env (fallback si DB no los tiene)
_SC = get_scoring_settings()
MAX_NOTICIAS_POR_BATCH = _SC.max_noticias_por_batch
MAX_CANDIDATAS_IA      = _SC.max_candidatas_ia

# ── Filtro geográfico para fuentes internacionales ───────────────────────────
_PAISES_OBJETIVO = frozenset({"chile", "perú", "peru", "argentina"})
_TOPICOS_INTERES = frozenset({
    "miner", "energ", "contrato", "licitac", "adjudic", "epc",
    "proyecto", "capex", "suministro", "construc",
})
# Mapeo ordenado: variante en texto → nombre canónico en la DB (orden = prioridad en caso de empate)
_PAISES_VARIANTES: list[tuple[str, str]] = [
    ("chile",     "Chile"),
    ("perú",      "Peru"),
    ("peru",      "Peru"),
    ("argentina", "Argentina"),
]


# ── Deduplicación cross-batch ────────────────────────────────────────────────

_STOPWORDS_ES = frozenset({
    "de", "del", "la", "el", "los", "las", "en", "y", "a", "con", "por", "que",
    "un", "una", "su", "sus", "al", "e", "o", "u", "se", "es", "como", "para",
})


def _titulos_son_duplicados(t1: str, t2: str) -> bool:
    """
    True si dos títulos tratan el mismo hecho concreto.
    Detecta: títulos idénticos, truncamiento con "...", y alta superposición léxica.
    """
    def _normalizar(t: str) -> str:
        t = t.lower().rstrip(". ")
        t = re.sub(r"[^\w\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    n1, n2 = _normalizar(t1), _normalizar(t2)
    if not n1 or not n2:
        return False

    if n1 == n2:
        return True

    shorter, longer = (n1, n2) if len(n1) <= len(n2) else (n2, n1)
    if len(shorter) >= 25 and longer.startswith(shorter):
        return True

    w1 = {w for w in n1.split() if w not in _STOPWORDS_ES and len(w) > 2}
    w2 = {w for w in n2.split() if w not in _STOPWORDS_ES and len(w) > 2}
    if len(w1) < 4 or len(w2) < 4:
        return False
    return len(w1 & w2) / min(len(w1), len(w2)) >= 0.80


def _deduplicar_cross_batch(candidatas: list[dict]) -> list[dict]:
    """
    Deduplicación post-Claude sobre TODAS las candidatas juntas.

    La regla ANTI-DUPLICADOS del prompt de Claude solo actúa dentro de un batch.
    Si dos artículos del mismo hecho caen en batches distintos, Claude no puede
    compararlos. Esta función cubre ese caso comparando todos contra todos.

    Solo actúa sobre artículos con score > 0 (los ya descartados se ignoran).
    El artículo con mayor score gana; los demás quedan en score = 0.
    """
    n = len(candidatas)
    duplicado_de: dict[int, int] = {}  # idx_perdedor → idx_ganador

    for i in range(n):
        if candidatas[i].get("score", 0) == 0 or i in duplicado_de:
            continue
        for j in range(i + 1, n):
            if candidatas[j].get("score", 0) == 0 or j in duplicado_de:
                continue
            if not _titulos_son_duplicados(
                candidatas[i].get("titulo", ""),
                candidatas[j].get("titulo", ""),
            ):
                continue
            si = candidatas[i].get("score", 0)
            sj = candidatas[j].get("score", 0)
            perdedor, ganador = (i, j) if sj >= si else (j, i)
            duplicado_de[perdedor] = ganador

    for dup_idx, orig_idx in duplicado_de.items():
        nd = candidatas[dup_idx]
        no = candidatas[orig_idx]
        logger.warning(
            "Dedup cross-batch: [%s] '%s' (score=%d) → dup de [%s] '%s' (score=%d)",
            nd.get("fuente", "?"), nd.get("titulo", "")[:70], nd.get("score", 0),
            no.get("fuente", "?"), no.get("titulo", "")[:70], no.get("score", 0),
        )
        nd["score"] = 0
        razon = f"Duplicado cross-batch — mismo hecho que noticia de {no.get('fuente', 'otra fuente')}"
        nd["razon"] = razon
        ia = nd.get("_criterios", {}).get("ia", {})
        ia["descripcion"] = "excluida por deduplicación cross-batch"
        ia["razon"] = razon

    if duplicado_de:
        logger.warning(
            "Dedup cross-batch: %d artículos descartados como duplicados inter-batch",
            len(duplicado_de),
        )

    return candidatas


# ── Caché de configuración (se carga una vez por ejecución) ─────────────────
_SCORE_CONFIG: dict | None = None
_SCORE_CONFIG_LOCK = threading.Lock()

def _get_score_config() -> dict:
    """
    Carga la configuración de scoring desde la DB una sola vez por ejecución.
    Cachea el resultado para no hacer múltiples queries innecesarias.
    Thread-safe: usa lock para evitar race condition si el scheduler lanza
    múltiples ejecuciones concurrentes.
    """
    global _SCORE_CONFIG
    with _SCORE_CONFIG_LOCK:
        if _SCORE_CONFIG is None:
            _SCORE_CONFIG = db.get_score_config()
    return _SCORE_CONFIG


# ── Scoring local (rápido, sin API) ──────────────────────────────────────────

def _score_local(noticia: dict) -> int:
    """
    Pre-scoring local usando las tablas de la DB.

    Jerarquía de empresa:
      1. score_empresa_tipo  → puntos por tipo (minera +200, epc +180, energía +160, …)
         Cada tipo se cuenta UNA sola vez aunque haya varias empresas del mismo tipo.
         Si ningún tipo matchea, cae al fallback genérico.
      2. score_empresas      → fallback genérico +80 si la empresa no está en score_empresa_tipo
      3. score_empresas_conocidas → bonus +150 adicional si hay contrato Y empresa conocida

    Contrato (score_keywords) y antigüedad son independientes del tipo de empresa.
    """
    cfg               = _get_score_config()
    reglas            = cfg["reglas"]
    empresas          = cfg["empresas"]
    ec                = cfg["empresas_conocidas"]
    keywords            = cfg["keywords"]                       # tipo='contrato'
    keywords_concepto   = cfg.get("keywords_concepto", [])      # tipo='concepto'
    keywords_entrevista = cfg.get("keywords_entrevista", [])    # tipo='entrevista'
    empresas_tipo       = cfg.get("empresas_tipo", [])
    sector_contexto     = cfg.get("sector_contexto", [])

    score    = 0
    criterios: dict = {}
    texto = (noticia.get("titulo", "") + " " + (noticia.get("resumen") or "")).lower()

    # ── Contratos / licitaciones (keywords tipo='contrato') ───────────────────
    # Requiere contexto sectorial para evitar falsos positivos con
    # "contrato laboral", "contrato colectivo", etc.
    tiene_contrato = (
        any(kw.lower() in texto for kw in keywords)
        and any(term.lower() in texto for term in sector_contexto)
    )
    if tiene_contrato:
        pts = reglas.get("contrato", _SC.contrato)
        score += pts
        criterios["contrato"] = pts

    # ── Conceptos sectoriales (keywords tipo='concepto') ─────────────────────
    # Hidrógeno, planificación estratégica, cero emisiones, etc.
    # Valen 80 pts (concepto_sectorial), NO 250 — son señales de calidad, no contratos.
    if any(kw.lower() in texto for kw in keywords_concepto):
        pts = reglas.get("concepto_sectorial", _SC.concepto_sectorial)
        score += pts
        criterios["concepto_sectorial"] = pts

    # ── Scoring por tipo de empresa (score_empresa_tipo) ─────────────────────
    tipos_sumados: set[str] = set()
    tipos_matcheados: list[dict] = []
    for nombre, tipo, puntos in empresas_tipo:
        if tipo not in tipos_sumados and nombre.lower() in texto:
            score += puntos
            tipos_sumados.add(tipo)
            tipos_matcheados.append({"tipo": tipo, "puntos": puntos})
    if tipos_matcheados:
        criterios["empresas_tipo"] = tipos_matcheados

    # ── Fallback genérico (score_empresas) ────────────────────────────────────
    if not tipos_sumados:
        if any(e.lower() in texto for e in empresas):
            pts = reglas.get("empresa_noticia", _SC.empresa_noticia)
            score += pts
            criterios["empresa_noticia"] = pts

    # ── Bonus empresa conocida que firma el contrato ──────────────────────────
    if tiene_contrato and any(e.lower() in texto for e in ec):
        pts = reglas.get("empresa_conocida", _SC.empresa_conocida)
        score += pts
        criterios["empresa_conocida"] = pts

    # ── Antigüedad ────────────────────────────────────────────────────────────
    try:
        fecha = datetime.fromisoformat(noticia["fecha"])
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - fecha
        if delta.days == 0:
            criterios["reciente_hoy"]   = reglas.get("reciente_hoy", _SC.reciente_hoy)
            criterios["reciente_3dias"] = reglas.get("reciente_3dias", _SC.reciente_3dias)
            score += criterios["reciente_hoy"] + criterios["reciente_3dias"]
        elif delta.days <= 3:
            criterios["reciente_3dias"] = reglas.get("reciente_3dias", _SC.reciente_3dias)
            score += criterios["reciente_3dias"]
    except Exception:
        pass

    # ── Penalización por entrevista ───────────────────────────────────────────
    if any(kw.lower() in texto for kw in keywords_entrevista):
        pts = reglas.get("entrevista_penalizacion", _SC.entrevista_penalizacion)
        score += pts
        criterios["entrevista_penalizacion"] = pts

    noticia["_pre_scoring"] = {
        "fuente":            "pre_score",
        "pre_scoring_total": score,
        "criterios":         criterios,
    }
    return score


# ── Scoring con Claude (semántico) ───────────────────────────────────────────

def _build_prompt(noticias: list[dict]) -> str:
    """Construye el prompt para Claude inyectando score_reglas, score_empresa_tipo y score_empresas."""
    cfg                = _get_score_config()
    reglas             = cfg["reglas"]
    empresas_conocidas = cfg["empresas_conocidas"]
    empresas_tipo      = cfg.get("empresas_tipo", [])   # list[(nombre, tipo, puntos)]
    empresas           = cfg["empresas"]

    items = []
    for i, n in enumerate(noticias):
        items.append(
            f'{i}. TÍTULO: {n["titulo"]}\n'
            f'   RESUMEN: {n["resumen"] or "(sin resumen)"}\n'
            f'   FECHA: {n["fecha"]}\n'
            f'   FUENTE: {n["fuente"]} ({n["pais"]})')

    p_contrato    = reglas.get("contrato", _SC.contrato)
    p_emp_cont    = reglas.get("empresa_conocida", _SC.empresa_conocida)
    p_empresa     = reglas.get("empresa_noticia", _SC.empresa_noticia)
    p_concepto    = reglas.get("concepto_sectorial", _SC.concepto_sectorial)
    p_3dias       = reglas.get("reciente_3dias", _SC.reciente_3dias)
    p_hoy         = reglas.get("reciente_hoy", _SC.reciente_hoy)
    p_entrevista  = reglas.get("entrevista_penalizacion", _SC.entrevista_penalizacion)
    p_enc_prov    = reglas.get("encuentro_proveedores", _SC.encuentro_proveedores)
    p_pol_edu     = _SC.filtro_politico_educacional

    # Empresas conocidas para el criterio de contrato (hasta 20)
    lista_emp_conocidas = ", ".join(empresas_conocidas[:20])
    if len(empresas_conocidas) > 20:
        lista_emp_conocidas += f" y {len(empresas_conocidas) - 20} más"

    # ── Bloque score_empresa_tipo agrupado por (tipo, puntos) ──
    tipos_agrupados: dict[tuple, list[str]] = {}
    for nombre, tipo, puntos in empresas_tipo:
        key = (tipo, puntos)
        if key not in tipos_agrupados:
            tipos_agrupados[key] = []
        tipos_agrupados[key].append(nombre)

    lineas_tipo = []
    for (tipo, puntos), nombres in sorted(tipos_agrupados.items(), key=lambda x: -x[0][1]):
        lista = ", ".join(nombres[:25])
        if len(nombres) > 25:
            lista += f" y {len(nombres) - 25} más"
        lineas_tipo.append(f"  - {tipo} (+{puntos} pts): {lista}")

    bloque_empresa_tipo = "\n".join(lineas_tipo) if lineas_tipo else "  (sin empresas configuradas)"

    # ── Bloque score_empresas (fallback genérico) ──
    lista_empresas_gen = ", ".join(empresas[:30])
    if len(empresas) > 30:
        lista_empresas_gen += f" y {len(empresas) - 30} más"

    return f"""Sos un analista experto en minería y energía de Latinoamérica.
Tu tarea es puntuar estas noticias para un boletín ejecutivo sobre minería y energía en Chile, Perú y Argentina.

REGLA GEOGRÁFICA OBLIGATORIA — aplicar ANTES de puntuar:
Score = 0 automático si el proyecto, contrato, inversión o suministro NO ocurre físicamente en Chile, Perú o Argentina.
Validación: "¿Dónde ocurre físicamente este proyecto o contrato?" → Si no es Chile, Perú o Argentina: score = 0. En caso de duda: score = 0.
Descartar si: noticia global sin impacto directo, proyecto en otro país (Brasil, México, África, etc.), resultado financiero sin proyectos concretos en estos países, análisis o tendencia sin proyecto específico.

FILTRO POLÍTICO Y EDUCACIONAL — score = 0 automático si la noticia es:
- Delegación, visita oficial o representación a evento/cumbre/foro internacional (sin proyecto o contrato concreto anunciado)
- Opinión, editorial o análisis de tendencias sin proyecto/inversión/contrato específico
- Declaración política o discurso de autoridad sin anuncio concreto de obra o inversión
- Alianza académica, hackathon, concurso, premio o evento universitario
- Artículo de divulgación o reflexión sobre política energética sin hecho concreto
- Informe de sostenibilidad o RSE sin proyecto de inversión asociado
Pregunta de validación: "¿Hay un contrato firmado, inversión anunciada, proyecto iniciado u operación concreta?" → Si la respuesta es NO: score = 0.
Cuando aplique este filtro, incluí OBLIGATORIAMENTE en el campo "desglose": "{p_pol_edu} (filtro político/educacional: sin proyecto/inversión concreto)"

FILTRO DE CALIDAD — score = 0 si la noticia no contiene al menos UNO de:
contrato / licitación / adjudicación | proyecto minero o energético con inversión concreta | construcción / expansión en curso | suministro de equipos, energía o cables | estudio sectorial con datos específicos | meta de cero emisiones con plan de inversión | hidrógeno verde con proyecto o planta concreta

SISTEMA DE PUNTUACIÓN (acumulativo):

1. CRITERIOS GENERALES (score_reglas):
- +{p_contrato} pts: Contrato firmado, licitación adjudicada o concesión otorgada
- +{p_emp_cont} pts: El contrato involucra una empresa conocida del sector. Lista: {lista_emp_conocidas}
- +{p_concepto} pts: Estudio sectorial, planificación estratégica del sector, meta de cero emisiones o noticia sobre hidrógeno (sostenible / verde / almacenamiento) en minería/energía
- +{p_enc_prov} pts: Noticia sobre financiamiento directo a través de concursos (encuentro de proveedores, convocatoria a proveedores, concurso de financiamiento)
- +{p_3dias} pts:    Noticia reciente (3 días o menos desde hoy)
- +{p_hoy} pts:      Noticia de hoy
- 0 pts:             No pasa el filtro geográfico o de calidad

2. EMPRESAS POR TIPO (score_empresa_tipo) — Si el título o resumen menciona alguna empresa
   de la lista, sumá los puntos de su tipo. Cada tipo se cuenta UNA sola vez aunque aparezcan
   varias empresas del mismo tipo:
{bloque_empresa_tipo}

3. EMPRESAS GENERALES — fallback (score_empresas):
   Usá ESTE CRITERIO SOLO si ninguna empresa del listado por tipo (punto 2) apareció.
   Si aparece alguna de estas empresas, sumá +{p_empresa} pts:
   {lista_empresas_gen}

PENALIZACIÓN (restar siempre que aplique, independientemente de la fuente):
- {p_entrevista} pts: La noticia es una entrevista o conversación con una persona
  (título contiene "entrevista", "conversamos con", "hablamos con", o es claramente
  un formato de pregunta-respuesta con un individuo)

BONUS POR COMBINACIÓN (sumar si aplica en la misma noticia):
- +100 pts: Empresa minera + EPC/EPCM
- +80 pts:  Empresa minera o EPC/EPCM + empresa de energía
- +60 pts:  Empresa minera + equipos o maquinaria
- +60 pts:  Empresa minera o EPC/EPCM + cables eléctricos
- +40 pts:  Dos o más tipos de equipo distintos
- +40 pts:  Cables + equipos en la misma noticia

CAMPO es_contrato — true ÚNICAMENTE si la noticia anuncia un evento contractual CONCRETO ya ocurrido:
  true:  contrato firmado o adjudicado | licitación adjudicada o ganada | concesión otorgada | EPC/EPCM adjudicado | order ganada (contract award)
  false: análisis de política o regulación | mención de futuras licitaciones | informe de producción | noticia financiera | discusión de marcos normativos | cualquier duda → false

REGLA ANTI-DUPLICADOS — aplicar DESPUÉS de puntuar cada noticia individualmente:
Si dos o más noticias de la lista tratan el MISMO HECHO CONCRETO (mismo proyecto, contrato, licitación o evento, aunque vengan de fuentes distintas):
- Conservá el puntaje calculado solo para la noticia con mayor score.
- A las demás asignales score = 0, es_contrato = false, desglose = "" y en "razon" escribí exactamente: "Duplicado de índice N" (donde N es el índice de la noticia que conservás).
- Criterio de similitud: mismo proyecto o contrato específico, misma empresa y misma acción (no alcanza con que hablen del mismo sector o país).

NOTICIAS A EVALUAR:
{chr(10).join(items)}

Respondé ÚNICAMENTE con un JSON array con este formato exacto, sin texto adicional:
[
  {{"indice": 0, "score": 330, "es_contrato": true,  "razon": "Contrato EPC adjudicado a Bechtel en proyecto minero en Chile", "desglose": "+250 (contrato firmado) +200 (empresa minera) +60 (reciente ≤3 días)"}},
  {{"indice": 1, "score": 0,   "es_contrato": false, "razon": "Noticia global sin impacto físico en Chile/Perú/Argentina",    "desglose": ""}},
  ...
]

El campo "desglose" debe describir en forma legible qué criterios sumaron al score, por ejemplo:
"+250 (contrato firmado) +200 (empresa minera) +60 (reciente ≤3 días) +25 (noticia de hoy)"
Si el score es 0, dejar "desglose" vacío.
"""


def _score_con_claude(noticias: list[dict], fecha_efectiva: date = None) -> list[dict]:
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
            resp = get_anthropic_client().messages.create(
                model="claude-opus-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": p}],
            )
            raw = resp.content[0].text.strip()
            # Extraer JSON de bloques de código Markdown si Claude los incluye
            if "```" in raw:
                partes = raw.split("```")
                # partes[1] es el contenido entre los backticks
                raw = partes[1].lstrip("json").strip() if len(partes) > 1 else raw
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error("Scorer: JSON inválido de Claude (primeros 300 chars): %s", raw[:300])
                raise ValueError(f"Respuesta de Claude no es JSON válido: {e}") from e

        scores_data, ok, error_msg = retrier.con_reintentos(
            fn=_llamar_claude,
            tipo_error=TipoError.API_IA,
            fuente=fuente_rep,
            url="api.anthropic.com",
        )

        if ok and scores_data:
            for item in scores_data:
                idx_global = i + item["indice"]
                noticia    = resultado[idx_global]

                score_ia = item.get("score", 0)

                noticia["score"]       = score_ia
                noticia["razon"]       = item.get("razon", "")
                noticia["es_contrato"] = bool(item.get("es_contrato", False))
                descripcion_ia = (
                    "excluida por filtro geográfico o de calidad (Claude)"
                    if score_ia == 0
                    else "candidata para ia semantica"
                )
                noticia["_criterios"]  = {
                    "pre_scoring": noticia.get("_pre_scoring", {}),
                    "ia": {
                        "descripcion":           descripcion_ia,
                        "fuente":                "claude",
                        "es_contrato":           bool(item.get("es_contrato", False)),
                        "razon":                 item.get("razon", ""),
                        "ia_semantica_criterio": item.get("desglose", ""),
                        "ia_score":              score_ia,
                    },
                }
            fuentes_ia_ok.update(fuentes_batch)
        else:
            fuentes_ia_fail.update(fuentes_batch)

    # Registra resultado IA por fuente (url_fuente = URL de la fuente en DB, no de la noticia)
    for url_fuente in fuentes_ia_ok - fuentes_ia_fail:
        n_ok = sum(1 for n in noticias if n.get("url_fuente", "") == url_fuente and n.get("score", 0) > 0)
        db.registrar_ia(url_fuente=url_fuente, ok=True, noticias_enviadas=n_ok, fecha=fecha_efectiva)
        # Marca los artículos como procesados para evitar doble cobro a la IA
        # si limpiar_articulos_pendientes falla más adelante
        if fecha_efectiva:
            try:
                db.marcar_articulos_ia_procesada(url_fuente=url_fuente, fecha=fecha_efectiva)
            except Exception as e:
                logger.warning("No se pudo marcar ia_procesada para %s: %s", url_fuente, e)
    for url_fuente in fuentes_ia_fail:
        db.registrar_ia(url_fuente=url_fuente, ok=False, error=error_msg or "Error en scoring Claude", fecha=fecha_efectiva)

    return resultado


# ── Filtro y clasificación geográfica (helpers privados) ─────────────────────

def _es_internacional_con_relevancia(noticia: dict) -> bool:
    """
    Verifica que una noticia de fuente Internacional mencione al menos un país
    objetivo (Chile, Perú, Argentina) Y al menos un tópico del sector.
    Las que no pasen este filtro no se envían a Claude ni compiten en el pool.
    """
    texto = (noticia.get("titulo", "") + " " + (noticia.get("resumen") or "")).lower()
    return (
        any(p in texto for p in _PAISES_OBJETIVO)
        and any(t in texto for t in _TOPICOS_INTERES)
    )


def _detectar_pais_mencionado(noticia: dict, nombres_paises: set[str]) -> str | None:
    """
    Detecta el primer país objetivo mencionado en el título + resumen de la noticia.
    Retorna el nombre canónico (como está en la DB) o None si no menciona ninguno.
    Usado para reclasificar internacionales al pool del país que mencionan.
    """
    texto = (noticia.get("titulo", "") + " " + (noticia.get("resumen") or "")).lower()
    for variante, canonical in _PAISES_VARIANTES:
        if variante in texto and canonical in nombres_paises:
            return canonical
    return None


# ── Selección del top 30 ──────────────────────────────────────────────────────

def seleccionar_top_noticias(noticias: list[dict]) -> list[dict]:
    """
    Selecciona las mejores noticias respetando la cuota por país definida en la DB.
    Carga los países activos desde `paises` — completamente dinámico.

    Estrategia:
      1. Carga cuotas desde db.get_paises_activos()
      2. Internacionales que mencionan un país objetivo → se incorporan al pool de ese país
         y compiten de igual a igual con las noticias locales
      3. Ordena por score desc dentro de cada pool de país
      4. Toma hasta cuota por país (sin relleno con irrelevantes)
    """
    paises = db.get_paises_activos()
    nombres_paises = {p["nombre"] for p in paises}
    cuotas = {p["nombre"]: p["cuota"] for p in paises}
    total_cuota = sum(cuotas.values())

    # Agrupa noticias por país.
    # Las internacionales que mencionan un país objetivo entran en ese pool directamente.
    por_pais: dict[str, list] = {p["nombre"]: [] for p in paises}

    reclasificadas = 0
    for n in noticias:
        pais = n["pais"]
        if pais not in nombres_paises:
            pais = _detectar_pais_mencionado(n, nombres_paises) or ""
            if pais:
                reclasificadas += 1

        if pais in por_pais:
            por_pais[pais].append(n)

    if reclasificadas:
        logger.info(
            "Internacionales reclasificadas al pool de su país mencionado: %d",
            reclasificadas,
        )

    # Ordena por score descendente dentro de cada pool
    for p in por_pais:
        por_pais[p].sort(key=lambda x: x["score"], reverse=True)

    seleccionadas = []
    for pais_cfg in paises:
        nombre = pais_cfg["nombre"]
        cuota  = pais_cfg["cuota"]
        top    = [n for n in por_pais[nombre] if n["score"] > 0][:cuota]
        seleccionadas.extend(top)

    logger.info(
        "Selección final: %d noticias (cuota total: %d) — %s",
        len(seleccionadas), total_cuota,
        " | ".join(f"{p['nombre']}:{len(por_pais[p['nombre']][:cuotas[p['nombre']]])}" for p in paises),
    )
    return seleccionadas[:total_cuota]


# ── Entrada pública ───────────────────────────────────────────────────────────

def puntuar_y_seleccionar(noticias: list[dict], fecha_efectiva: date = None) -> list[dict]:
    """
    Pipeline completo:
      1. Pre-scoring local (rápido)
      2. Pre-filtro geográfico: internacionales sin mención de CL/PE/AR + sector → score 0
      3. Filtra las top 60 para enviar a Claude (ahorra tokens)
      4. Scoring semántico con Claude
      5. Selección final top 30 con cuotas por país
    """
    logger.info("Iniciando scoring de %d noticias...", len(noticias))

    # Inicializar es_contrato en False para todas — Claude lo sobreescribe en top-60
    for n in noticias:
        n["es_contrato"] = False

    # Pre-score local
    for n in noticias:
        n["score"] = _score_local(n)

    # Inicializa _criterios con pre_scoring para TODAS las noticias.
    # Las top-60 que pasen a Claude tendrán _criterios sobreescrito con el detalle completo.
    # Las que no lleguen a Claude quedan con su pre_scoring como referencia en el JSON.
    for n in noticias:
        n["_criterios"] = {
            "pre_scoring": n.get("_pre_scoring", {}),
            "ia": {
                "descripcion":           f"no llegan a Claude (quedan fuera del top-{MAX_CANDIDATAS_IA})",
                "fuente":                "",
                "es_contrato":           False,
                "razon":                 "",
                "ia_semantica_criterio": "",
                "ia_score":              0,
            },
        }

    # Pre-filtro geográfico: internacionales sin relevancia quedan en score 0 y no van a Claude
    filtradas = 0
    for n in noticias:
        if n.get("pais") == "Internacional" and not _es_internacional_con_relevancia(n):
            n["score"] = 0
            n["_criterios"]["ia"]["descripcion"] = (
                "excluida por pre-filtro geográfico (fuente internacional sin mención de Chile/Perú/Argentina)"
            )
            filtradas += 1
    if filtradas:
        logger.info(
            "Pre-filtro geográfico: %d internacionales descartadas (sin mención de CL/PE/AR + sector)",
            filtradas,
        )

    # Toma las mejores MAX_CANDIDATAS_IA para análisis con IA (reduce costo de tokens)
    candidatas = sorted(noticias, key=lambda x: x["score"], reverse=True)[:MAX_CANDIDATAS_IA]
    logger.info("Candidatas para Claude: %d", len(candidatas))

    # Fuentes con noticias que no llegaron a candidatas (score=0 o insuficiente).
    # Se marcan ia_ok=TRUE ahora para que no aparezcan como "Error desconocido"
    # en el resumen. No es un fallo: sus noticias fueron filtradas antes de Claude.
    fuentes_con_candidatos = {n.get("url_fuente") for n in candidatas if n.get("url_fuente")}
    fuentes_sin_candidatos = {
        n.get("url_fuente") for n in noticias
        if n.get("url_fuente") and n.get("url_fuente") not in fuentes_con_candidatos
    }
    for url_fuente in fuentes_sin_candidatos:
        db.registrar_ia(
            url_fuente=url_fuente,
            ok=True,
            noticias_enviadas=0,
            fecha=fecha_efectiva,
        )
        logger.info(
            "Fuente sin candidatos para Claude (sin error): %s", url_fuente
        )

    # Scoring semántico
    candidatas = _score_con_claude(candidatas, fecha_efectiva)

    # Deduplicación cross-batch: artículos del mismo hecho en distintos batches de Claude
    candidatas = _deduplicar_cross_batch(candidatas)

    # Selección final (cuotas dinámicas desde DB)
    top30 = seleccionar_top_noticias(candidatas)

    # Persiste candidatas sabiendo cuáles entraron al boletín
    urls_boletin = {n["url"] for n in top30}
    db.registrar_ia_scoring_log(candidatas, fecha_efectiva, urls_boletin)

    return top30
