from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;q=0.9,'
        'application/rss+xml,application/atom+xml,text/xml;q=0.9,*/*;q=0.8'
    ),
    'Accept-Language': 'es-CL,es;q=0.9,en;q=0.8',
}
XML_CONTENT_TYPES = {
    'application/rss+xml',
    'application/xml',
    'text/xml',
    'application/atom+xml',
}
HTML_SNIPPETS = ('<!doctype html', '<html', '<head', '<body')
ERROR_PATTERNS = {
    'captcha': 'CAPTCHA o challenge detectado',
    'cloudflare': 'Cloudflare/WAF detectado',
    'access denied': 'Bloqueo de acceso detectado',
    'forbidden': 'Acceso prohibido en contenido',
    'no encontramos la pagina': 'Página de error HTML detectada',
    'not found': 'Página de error HTML detectada',
    'error 404': 'Página de error HTML detectada',
    'temporarily unavailable': 'Página de error genérica detectada',
}
TIME_TAGS_RSS = ('pubDate', 'published', 'dc:date', 'updated')
TIME_TAGS_ATOM = ('updated', 'published')
KEYWORDS = {'mineria', 'minería', 'energia', 'energía', 'minero', 'minera', 'mining', 'energy', 'h2', 'hidrogeno', 'hidrógeno'}


@dataclass
class Fetched:
    url: str
    requested_url: str
    status_code: int | None
    content_type: str
    text: str
    headers: dict[str, str]
    final_url: str | None
    redirect_chain: list[dict[str, Any]]
    error: str | None = None


def get_connection():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.environ['DB_NAME'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
    )


def load_fuentes() -> list[dict[str, Any]]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, nombre, url, url_rss
            FROM fuentes
            WHERE activa = TRUE
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    return [
        {'id': r[0], 'nombre': r[1], 'url': r[2], 'url_rss': None if r[3] in (None, '', 'None') else r[3]}
        for r in rows
    ]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.strip().rstrip('/')


def is_excluded_feed(url: str) -> bool:
    url_low = (url or '').lower()
    return '/comments/feed' in url_low


def fetch(client: httpx.Client, url: str) -> Fetched:
    try:
        resp = client.get(url, follow_redirects=True)
        return Fetched(
            url=url,
            requested_url=url,
            status_code=resp.status_code,
            content_type=(resp.headers.get('content-type') or '').split(';')[0].strip().lower(),
            text=resp.text or '',
            headers={k.lower(): v for k, v in resp.headers.items()},
            final_url=str(resp.url),
            redirect_chain=[{'status_code': h.status_code, 'location': h.headers.get('location'), 'url': str(h.url)} for h in resp.history],
            error=None,
        )
    except Exception as exc:
        return Fetched(
            url=url,
            requested_url=url,
            status_code=None,
            content_type='',
            text='',
            headers={},
            final_url=None,
            redirect_chain=[],
            error=f'{type(exc).__name__}: {exc}',
        )


def detect_html(text: str) -> bool:
    sample = (text or '')[:5000].lower()
    return any(x in sample for x in HTML_SNIPPETS)


def detect_xml_root(text: str) -> tuple[bool, str, ET.Element | None, str | None]:
    stripped = (text or '').lstrip('\ufeff\n\r\t ')
    if not stripped:
        return False, '', None, 'respuesta vacía'
    if detect_html(stripped):
        return False, '', None, None
    try:
        root = ET.fromstring(stripped.encode('utf-8'))
    except ET.ParseError as exc:
        return False, '', None, f'XML mal formado: {exc}'
    tag = root.tag
    local = tag.split('}', 1)[-1].lower() if '}' in tag else tag.lower()
    return True, local, root, None


def find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        tag = child.tag.split('}', 1)[-1] if '}' in child.tag else child.tag
        if tag.lower() == name.lower():
            return child
    return None


def find_children(parent: ET.Element, name: str) -> list[ET.Element]:
    result = []
    for child in parent.iter():
        tag = child.tag.split('}', 1)[-1] if '}' in child.tag else child.tag
        if tag.lower() == name.lower():
            result.append(child)
    return result


def text_of(elem: ET.Element | None) -> str:
    if elem is None:
        return ''
    return ' '.join((elem.text or '').split()).strip()


def atom_link_value(entry: ET.Element) -> str:
    for link in find_children(entry, 'link'):
        href = link.attrib.get('href')
        if href:
            return href.strip()
        value = text_of(link)
        if value:
            return value
    return ''


def detect_indicios(fetch_result: Fetched) -> list[str]:
    indicios: list[str] = []
    if fetch_result.error:
        indicios.append(fetch_result.error)
        return indicios
    if fetch_result.redirect_chain:
        indicios.append('Redirección detectada')
    if fetch_result.status_code is not None and 400 <= fetch_result.status_code < 500:
        indicios.append(f'HTTP {fetch_result.status_code}')
    if fetch_result.status_code is not None and fetch_result.status_code >= 500:
        indicios.append(f'HTTP {fetch_result.status_code}')
    sample = (fetch_result.text or '')[:10000].lower()
    for pattern, label in ERROR_PATTERNS.items():
        if pattern in sample and label not in indicios:
            indicios.append(label)
    if not (fetch_result.text or '').strip():
        indicios.append('Respuesta vacía')
    return indicios


def extract_page_signals(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html or '', 'html.parser')
    title = ' '.join((soup.title.get_text(' ', strip=True) if soup.title else '').split())
    heading_texts = []
    for tag in soup.find_all(['h1', 'h2', 'article'], limit=15):
        text = ' '.join(tag.get_text(' ', strip=True).split())
        if text:
            heading_texts.append(text)
    visible_feed_links = []
    for tag in soup.find_all(['a', 'link'], href=True):
        href = urljoin(base_url, tag['href'])
        rel = ' '.join(tag.get('rel', [])) if isinstance(tag.get('rel'), list) else str(tag.get('rel', ''))
        type_ = (tag.get('type') or '').lower()
        text = ' '.join(tag.get_text(' ', strip=True).split()).lower() if tag.name == 'a' else ''
        href_low = href.lower()
        if 'alternate' in rel.lower() and type_ in XML_CONTENT_TYPES:
            visible_feed_links.append({'url': href, 'kind': 'alternate'})
        elif any(token in href_low for token in ('/feed', 'rss', 'atom.xml', 'rss.xml')):
            visible_feed_links.append({'url': href, 'kind': 'candidate'})
        elif 'rss' in text or 'atom' in text or 'xml' in text:
            visible_feed_links.append({'url': href, 'kind': 'visible'})
    dedup = []
    seen = set()
    for item in visible_feed_links:
        if item['url'] not in seen:
            seen.add(item['url'])
            dedup.append(item)
    return {'title': title, 'headings': heading_texts, 'feed_links': dedup}


def correspondence_score(page_signals: dict[str, Any], feed_titles: list[str], page_url: str, feed_url: str) -> bool:
    if not feed_titles:
        return False
    page_host = urlparse(page_url).netloc.replace('www.', '').lower()
    feed_host = urlparse(feed_url).netloc.replace('www.', '').lower()
    if page_host and feed_host and page_host != feed_host:
        return False
    page_blob = ' '.join([page_signals.get('title', ''), *page_signals.get('headings', [])]).lower()
    feed_blob = ' '.join(feed_titles[:5]).lower()
    if any(k in page_blob and k in feed_blob for k in KEYWORDS):
        return True
    page_words = {w for w in re.findall(r'\w+', page_blob) if len(w) > 4}
    feed_words = {w for w in re.findall(r'\w+', feed_blob) if len(w) > 4}
    overlap = page_words & feed_words
    return len(overlap) >= 2 or bool(page_host == feed_host and feed_words & KEYWORDS)


def score_feed_candidate(feed_url: str, analysis: dict[str, Any], corresponds: bool) -> tuple[int, str]:
    url_low = (feed_url or '').lower()
    reasons: list[str] = []

    if '/comments/feed' in url_low:
        return -1000, 'Descartado: feed de comentarios'
    if not analysis['estructura_valida']:
        return -900, 'Descartado: XML sin estructura mínima válida'
    if not analysis['es_xml_real']:
        return -800, 'Descartado: no es XML real'
    if not corresponds:
        return -700, 'Descartado: no corresponde temáticamente con la página'

    score = 0
    if analysis['xml_util_para_scraping']:
        score += 100
        reasons.append('tiene campos útiles para scraping')
    else:
        score -= 200
        reasons.append('carece de campos útiles completos')

    if re.search(r'(^|/)feed/?$', url_low) or url_low.endswith('/rss') or url_low.endswith('/rss/'):
        score += 50
        reasons.append('es feed principal de noticias')
    elif '/feed/gn' in url_low:
        score += 40
        reasons.append('es feed optimizado tipo Google News')
    elif '/category/' in url_low and '/feed' in url_low:
        score += 30
        reasons.append('es feed por categoría')
    else:
        score += 10
        reasons.append('es feed secundario válido')

    item_count = analysis['cantidad_items_o_entries']
    score += min(item_count, 50)
    reasons.append(f'contiene {item_count} items/entries')

    return score, '; '.join(reasons)


def analyze_xml(fetch_result: Fetched) -> dict[str, Any]:
    indicios = detect_indicios(fetch_result)
    es_html = detect_html(fetch_result.text)
    es_xml_real, raiz_xml, root, parse_error = detect_xml_root(fetch_result.text)
    if parse_error:
        indicios.append(parse_error)
    tiene_rss = raiz_xml == 'rss'
    tiene_atom = raiz_xml == 'feed'
    tiene_channel = False
    tiene_item = False
    tiene_entry = False
    cantidad = 0
    estructura_valida = False
    xml_util = False
    feed_titles: list[str] = []

    if es_xml_real and root is not None:
        if tiene_rss:
            channel = find_child(root, 'channel')
            tiene_channel = channel is not None
            items = find_children(channel, 'item') if channel is not None else []
            tiene_item = len(items) > 0
            cantidad = len(items)
            estructura_valida = tiene_channel and tiene_item
            useful_count = 0
            for item in items[:10]:
                title = text_of(find_child(item, 'title'))
                link = text_of(find_child(item, 'link'))
                temporal = ''
                for tag in TIME_TAGS_RSS:
                    temporal = text_of(find_child(item, tag))
                    if temporal:
                        break
                if title:
                    feed_titles.append(title)
                if title and link and temporal:
                    useful_count += 1
            xml_util = useful_count > 0
            if estructura_valida and not xml_util:
                indicios.append('RSS sin nodos útiles suficientes para scraping')
        elif tiene_atom:
            entries = find_children(root, 'entry')
            tiene_entry = len(entries) > 0
            cantidad = len(entries)
            estructura_valida = tiene_entry
            useful_count = 0
            for entry in entries[:10]:
                title = text_of(find_child(entry, 'title'))
                link = atom_link_value(entry)
                temporal = ''
                for tag in TIME_TAGS_ATOM:
                    temporal = text_of(find_child(entry, tag))
                    if temporal:
                        break
                if title:
                    feed_titles.append(title)
                if title and link and temporal:
                    useful_count += 1
            xml_util = useful_count > 0
            if estructura_valida and not xml_util:
                indicios.append('Atom sin nodos útiles suficientes para scraping')
        else:
            indicios.append(f'Raíz XML no soportada: {raiz_xml or "desconocida"}')
    else:
        if fetch_result.content_type in XML_CONTENT_TYPES and es_html:
            indicios.append('HTML disfrazado de XML por Content-Type')
        elif fetch_result.content_type in XML_CONTENT_TYPES and not es_xml_real:
            indicios.append('Content-Type XML pero contenido no parseable como XML')

    return {
        'status_code': fetch_result.status_code or 0,
        'content_type': fetch_result.content_type,
        'es_xml_real': es_xml_real,
        'es_html': es_html,
        'raiz_xml': raiz_xml,
        'tiene_rss': tiene_rss,
        'tiene_atom': tiene_atom,
        'tiene_channel': tiene_channel,
        'tiene_item': tiene_item,
        'tiene_entry': tiene_entry,
        'cantidad_items_o_entries': cantidad,
        'estructura_valida': estructura_valida,
        'xml_util_para_scraping': xml_util,
        'indicios_de_error': indicios,
        'feed_titles': feed_titles,
    }


def classify_direct(fetch_result: Fetched, analyzed: dict[str, Any]) -> tuple[str, str]:
    if fetch_result.error:
        return 'XML_INVALIDO', f'No se pudo obtener la URL: {fetch_result.error}'
    if analyzed['status_code'] >= 400:
        return 'XML_INVALIDO', f'La URL respondió HTTP {analyzed["status_code"]}'
    if analyzed['status_code'] >= 300 and analyzed['status_code'] < 400:
        return 'XML_INVALIDO', 'La URL respondió con redirección y no contenido final utilizable'
    if analyzed['es_xml_real']:
        if analyzed['estructura_valida'] and analyzed['xml_util_para_scraping']:
            return 'XML_DIRECTO_VALIDO', 'La URL responde XML real, con estructura válida y datos útiles para scraping'
        if analyzed['estructura_valida'] and not analyzed['xml_util_para_scraping']:
            return 'XML_EXISTE_PERO_NO_UTIL', 'La URL expone XML real pero sin campos mínimos útiles para scraping'
        return 'XML_INVALIDO', 'La URL expone XML, pero no cumple la estructura mínima requerida'
    if analyzed['es_html']:
        return 'NO_XML_SOLO_HTML', 'La URL responde HTML; se deben validar feeds alternativos si existen'
    return 'XML_INVALIDO', 'La respuesta no es HTML ni XML utilizable'


def analyze_candidate_feed(client: httpx.Client, feed_url: str, page_signals: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    fetched = fetch(client, feed_url)
    analyzed = analyze_xml(fetched)
    if not analyzed['es_xml_real'] or not analyzed['estructura_valida']:
        return None
    corresponde = correspondence_score(page_signals, analyzed['feed_titles'], page_url, fetched.final_url or feed_url)
    clasificacion = 'XML_DISPONIBLE_VIA_LINK'
    motivo = 'Feed XML detectado dentro del HTML y validado correctamente'
    if not corresponde:
        clasificacion = 'XML_EXISTE_PERO_NO_CORRESPONDE'
        motivo = 'Existe un feed XML, pero no parece representar el contenido de la página analizada'
    elif not analyzed['xml_util_para_scraping']:
        clasificacion = 'XML_EXISTE_PERO_NO_UTIL'
        motivo = 'El feed XML existe, pero no tiene datos suficientes para scraping útil'
    score, razon = score_feed_candidate(fetched.final_url or feed_url, analyzed, corresponde)
    return {
        'url': fetched.final_url or feed_url,
        'requested_url': feed_url,
        'clasificacion': clasificacion,
        'motivo': motivo,
        'corresponde': corresponde,
        'xml_util': analyzed['xml_util_para_scraping'],
        'score_feed': score,
        'razon_feed': razon,
        'analysis': analyzed,
    }


def inspect_feed_endpoint(client: httpx.Client, feed_url: str, page_signals: dict[str, Any] | None, page_url: str) -> dict[str, Any]:
    fetched = fetch(client, feed_url)
    analyzed = analyze_xml(fetched)
    corresponde = False
    if analyzed['es_xml_real'] and analyzed['estructura_valida'] and page_signals is not None:
        corresponde = correspondence_score(page_signals, analyzed['feed_titles'], page_url, fetched.final_url or feed_url)
    score, razon = score_feed_candidate(fetched.final_url or feed_url, analyzed, corresponde)
    return {
        'url': fetched.final_url or feed_url,
        'requested_url': feed_url,
        'fetched': fetched,
        'analysis': analyzed,
        'corresponde': corresponde,
        'score_feed': score,
        'razon_feed': razon,
    }


def build_output(
    url: str,
    analyzed: dict[str, Any],
    feeds_detectados: list[str],
    mejor_feed_noticias: str,
    razon_seleccion_feed: str,
    corresponde: bool,
    motivo: str,
    clasificacion: str,
) -> dict[str, Any]:
    return {
        'url_analizada': url,
        'status_code': analyzed['status_code'],
        'content_type': analyzed['content_type'],
        'es_xml_real': analyzed['es_xml_real'],
        'es_html': analyzed['es_html'],
        'feeds_detectados': feeds_detectados,
        'mejor_feed_noticias': mejor_feed_noticias,
        'razon_seleccion_feed': razon_seleccion_feed,
        'xml_corresponde_a_la_pagina': corresponde,
        'estructura_valida': analyzed['estructura_valida'],
        'xml_util_para_scraping': analyzed['xml_util_para_scraping'],
        'indicios_de_error': analyzed['indicios_de_error'],
        'motivo': motivo,
        'clasificacion': clasificacion,
    }


def analyze_url(client: httpx.Client, fuente: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    fetched = fetch(client, fuente['url'])
    analyzed = analyze_xml(fetched)
    clasificacion, motivo = classify_direct(fetched, analyzed)
    final_url = fetched.final_url or fuente['url']
    configured_feed = normalize_url(fuente.get('url_rss'))

    if clasificacion == 'XML_DIRECTO_VALIDO':
        corresponde = True
        return build_output(
            final_url,
            analyzed,
            [final_url],
            final_url,
            'La URL analizada ya es el feed XML directo válido y útil para noticias',
            corresponde,
            motivo,
            clasificacion,
        ), final_url

    if clasificacion == 'XML_EXISTE_PERO_NO_UTIL':
        return build_output(
            final_url,
            analyzed,
            [final_url],
            final_url,
            'La URL analizada expone XML, pero no cumple utilidad suficiente para scraping de noticias',
            True,
            motivo,
            clasificacion,
        ), None

    if analyzed['es_html']:
        page_signals = extract_page_signals(fetched.text, final_url)
        candidate_urls = [item['url'] for item in page_signals['feed_links']]
        if configured_feed and configured_feed not in candidate_urls and not is_excluded_feed(configured_feed):
            candidate_urls.insert(0, configured_feed)
        validated = []
        blocked_candidates = []
        seen_candidates = set()
        for candidate in candidate_urls:
            normalized_candidate = normalize_url(candidate) or candidate
            if normalized_candidate in seen_candidates:
                continue
            seen_candidates.add(normalized_candidate)
            inspected = inspect_feed_endpoint(client, candidate, page_signals, final_url)
            result = analyze_candidate_feed(client, candidate, page_signals, final_url)
            if result:
                validated.append(result)
            elif inspected['fetched'].status_code and inspected['fetched'].status_code >= 400:
                blocked_candidates.append(inspected)
        if validated:
            validated.sort(key=lambda item: item['score_feed'], reverse=True)
            best = validated[0]
            out = build_output(
                final_url,
                analyzed,
                [v['url'] for v in validated],
                best['url'],
                best['razon_feed'],
                best['corresponde'],
                best['motivo'],
                best['clasificacion'],
            )
            return out, None
        if blocked_candidates:
            blocked_candidates.sort(
                key=lambda item: (normalize_url(item['requested_url']) != configured_feed, item['requested_url'])
            )
            blocked = blocked_candidates[0]
            blocked_status = blocked['analysis']['status_code']
            blocked_indicios = list(analyzed['indicios_de_error'])
            blocked_indicios.extend(
                x for x in blocked['analysis']['indicios_de_error']
                if x not in blocked_indicios
            )
            if f'Feed bloqueado: {blocked["requested_url"]}' not in blocked_indicios:
                blocked_indicios.append(f'Feed bloqueado: {blocked["requested_url"]}')
            blocked_analysis = dict(analyzed)
            blocked_analysis['indicios_de_error'] = blocked_indicios
            blocked_analysis['estructura_valida'] = False
            blocked_analysis['xml_util_para_scraping'] = False
            return build_output(
                final_url,
                blocked_analysis,
                [],
                '',
                f'Existe un feed configurado o candidato ({blocked["requested_url"]}), pero respondió HTTP {blocked_status} y no pudo validarse como XML utilizable desde el scraper',
                False,
                f'La URL original responde HTML y el feed asociado respondió bloqueo/error HTTP {blocked_status}',
                'XML_INVALIDO',
            ), None
        out = build_output(
            final_url,
            analyzed,
            [],
            '',
            'No se detectó ningún feed XML válido y coherente dentro del HTML',
            False,
            'La URL responde HTML y no expone enlaces alternate RSS/Atom válidos ni feeds detectables en el documento',
            'NO_XML_SOLO_HTML',
        )
        return out, None

    return build_output(
        final_url,
        analyzed,
        [],
        '',
        'No existe feed XML utilizable para esta URL',
        False,
        motivo,
        'XML_INVALIDO',
    ), None


def update_url_rss(updates: list[tuple[int, str]]) -> None:
    if not updates:
        return
    with get_connection() as conn, conn.cursor() as cur:
        for fuente_id, url_rss in updates:
            cur.execute('UPDATE fuentes SET url_rss = %s WHERE id = %s', (url_rss, fuente_id))
        conn.commit()


def main() -> None:
    fuentes = load_fuentes()
    results: list[dict[str, Any]] = []
    updates: list[tuple[int, str]] = []

    with httpx.Client(headers=HEADERS, timeout=20, verify=True) as client:
        for fuente in fuentes:
            result, direct_url = analyze_url(client, fuente)
            result['fuente_id'] = fuente['id']
            result['fuente_nombre'] = fuente['nombre']
            result['url_rss_db'] = fuente['url_rss']
            result['url_rss_sugerida'] = direct_url
            result['url_rss_actualizada'] = False
            if result['clasificacion'] == 'XML_DIRECTO_VALIDO' and direct_url:
                if normalize_url(fuente['url_rss']) != normalize_url(direct_url):
                    updates.append((fuente['id'], direct_url))
                    result['url_rss_actualizada'] = True
            results.append(result)

    update_url_rss(updates)

    out_dir = ROOT / 'tests' / 'output' / 'feeds'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'analisis_fuentes_xml_estricto.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(out_path))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
