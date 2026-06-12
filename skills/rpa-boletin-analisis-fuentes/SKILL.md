---
name: rpa-boletin-analisis-fuentes
description: >
  Analiza URLs de la tabla fuentes para decidir si deben scrapearse por XML directo,
  XML vía links RSS/Atom o solo HTML, con validación HTTP y estructural estricta.
  Trigger: Cuando haya que auditar feeds RSS/Atom, validar soporte XML/HTML o elegir
  el mejor feed noticioso para una fuente del proyecto rpa_boletin.
license: Apache-2.0
metadata:
  author: gentleman-programming
  version: "1.0"
---

## When to Use

- Cuando el proyecto necesite clasificar fuentes como `XML_DIRECTO_VALIDO`, `XML_DISPONIBLE_VIA_LINK`, `XML_EXISTE_PERO_NO_CORRESPONDE`, `XML_EXISTE_PERO_NO_UTIL`, `NO_XML_SOLO_HTML` o `XML_INVALIDO`.
- Cuando haya que inspeccionar respuestas HTTP reales antes de decidir la estrategia de scraping.
- Cuando haya múltiples feeds detectados y sea necesario elegir **solo uno** como mejor feed de noticias.
- Cuando se quiera guardar el resultado del análisis en `logs/` del proyecto.

## Critical Patterns

- **NO asumir feeds por convención**: no inventar `/rss.xml`, `/feed`, `/atom.xml` si no aparecen en HTML o no fueron validados por HTTP real.
- Validar en este orden: **status HTTP → content-type → contenido real → estructura XML → utilidad editorial**.
- Si `Content-Type` dice XML pero el cuerpo es HTML, clasificar como `XML_INVALIDO`.
- Si la URL original devuelve HTML, buscar feeds con:
  - `<link rel="alternate" type="application/rss+xml">`
  - `<link rel="alternate" type="application/atom+xml">`
  - enlaces reales visibles a `/feed/`, `/rss`, `/atom.xml`
- Para RSS válido exigir:
  - `<rss>`
  - `<channel>`
  - al menos un `<item>`
- Para Atom válido exigir:
  - `<feed>`
  - al menos un `<entry>`
- Para utilidad mínima de scraping, cada item/entry debe traer al menos:
  - título
  - link
  - fecha (`pubDate`, `updated`, `published` o equivalente)
- Comparar coherencia temática entre HTML y feed:
  - host
  - títulos
  - headings visibles
  - vocabulario editorial relacionado a minería/energía

## Selección del mejor feed

Si aparecen múltiples feeds XML válidos:

1. Elegir **uno solo**.
2. Priorizar:
   1. feed principal (`/feed/`, `/rss`)
   2. feed optimizado (`/feed/gn`)
   3. feed por categoría (`/category/.../feed`)
   4. otros feeds válidos
3. Excluir siempre:
   - `/comments/feed`
   - feeds de comentarios
   - feeds de usuarios o contenido no editorial
   - feeds que no correspondan temáticamente a la página
4. Entre dos feeds válidos del mismo nivel, preferir el más útil:
   - más items/entries
   - presencia consistente de título + link + fecha
   - mejor correspondencia editorial con la home analizada

## JSON de salida esperado

```json
{
  "url_analizada": "",
  "status_code": 0,
  "content_type": "",
  "es_xml_real": false,
  "es_html": false,
  "feeds_detectados": [],
  "mejor_feed_noticias": "",
  "razon_seleccion_feed": "",
  "xml_corresponde_a_la_pagina": false,
  "estructura_valida": false,
  "xml_util_para_scraping": false,
  "indicios_de_error": [],
  "motivo": "",
  "clasificacion": ""
}
```

## Persistencia y DB

- Guardar el reporte final en `logs/analisis_fuentes_xml_estricto.json`.
- Solo actualizar `fuentes.url_rss` si la clasificación final es `XML_DIRECTO_VALIDO`.
- Si la URL original es HTML pero tiene feed detectado vía link, **no** actualizar `url_rss` salvo que exista una regla explícita adicional.

## Commands

```bash
.venv\Scripts\python analizar_fuentes_xml_estricto.py
Get-Content logs\analisis_fuentes_xml_estricto.json
```

## Relevant Files

- `analizar_fuentes_xml_estricto.py` — script principal del análisis HTTP/XML/HTML.
- `logs/analisis_fuentes_xml_estricto.json` — salida persistida del análisis.
- `.env` — credenciales de base de datos para leer y eventualmente actualizar `fuentes.url_rss`.
