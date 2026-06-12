from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


import csv
import os
import smtplib
from dataclasses import dataclass, asdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import psycopg2
import psycopg2.extras
from bs4 import BeautifulSoup
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/rss+xml,application/atom+xml,*/*;q=0.8"
    ),
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}

KNOWN_FEEDS = {
    "https://www.miningweekly.com": "https://www.miningweekly.com/page/rss-feed/feed:home",
    "https://www.mineriaydesarrollo.com": "https://www.mineriaydesarrollo.com/rss",
}

KNOWN_APIS = {
    "www.bnamericas.com": "https://apidocs.bnamericas.com/",
    "bnamericas.com": "https://apidocs.bnamericas.com/",
}


@dataclass
class FuenteResult:
    id: int
    nombre: str
    pais: str
    url_origen: str
    metodo_db: str | None
    url_xml_db: str | None
    url_xml_detectada: str | None
    tipo_xml: str | None
    url_api: str | None
    estado: str
    detalle: str


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def load_fuentes() -> list[dict]:
    with get_connection() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """
            SELECT id, nombre, url, url_rss, metodo, pais
            FROM fuentes
            WHERE activa = TRUE
            ORDER BY pais, nombre
            """
        )
        return [dict(r) for r in cur.fetchall()]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.strip().rstrip("/")


def build_candidates(origin: str, configured_xml: str | None, homepage_html: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        if not url:
            return
        final = url.strip()
        if not final or final in seen:
            return
        seen.add(final)
        candidates.append(final)

    add(KNOWN_FEEDS.get(origin))
    add(configured_xml)

    common_paths = [
        "/feed",
        "/feed/",
        "/rss",
        "/rss/",
        "/rss.xml",
        "/feed.xml",
        "/atom.xml",
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/wp/feed/",
        "/page/rss-feed/feed:home",
        "/agencia/rss.aspx",
    ]
    for path in common_paths:
        add(urljoin(origin, path))

    soup = BeautifulSoup(homepage_html or "", "html.parser")

    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])) if isinstance(link.get("rel"), list) else str(link.get("rel", ""))
        type_ = (link.get("type") or "").lower()
        href = urljoin(origin, link["href"])
        if "alternate" in rel.lower() and any(x in type_ for x in ("rss", "atom", "xml")):
            add(href)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(" ", strip=True) or "").lower()
        href_abs = urljoin(origin, href)
        href_low = href_abs.lower()
        if any(token in href_low for token in ("/feed", "rss", "atom", "sitemap.xml")):
            add(href_abs)
        elif "rss" in text or "xml" in text:
            add(href_abs)

    return candidates


def classify_xml(url: str, response: httpx.Response) -> tuple[bool, str | None]:
    ctype = (response.headers.get("content-type") or "").lower()
    text = response.text[:5000].lower()
    url_low = url.lower()

    if response.status_code >= 400:
        return False, None

    if "oembed" in url_low or "/wp-json/" in url_low:
        return False, None

    if "<rss" in text or "application/rss+xml" in ctype:
        return True, "rss"
    if "<feed" in text or "application/atom+xml" in ctype:
        return True, "atom"
    if "<urlset" in text or "<sitemapindex" in text:
        return True, "sitemap"
    if "application/xml" in ctype or "text/xml" in ctype:
        parsed = feedparser.parse(response.text)
        if getattr(parsed, "entries", None):
            return True, "rss"
        return True, "xml"
    return False, None


def detect_api(origin: str, homepage_html: str) -> str | None:
    host = urlparse(origin).netloc.lower()
    if host in KNOWN_APIS:
        return KNOWN_APIS[host]

    soup = BeautifulSoup(homepage_html or "", "html.parser")
    for a in soup.find_all("a", href=True):
        href = urljoin(origin, a["href"])
        low = href.lower()
        text = (a.get_text(" ", strip=True) or "").lower()
        if any(blocked in low for blocked in ("api.whatsapp.com", "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "tiktok.com")):
            continue
        if any(token in low for token in ("swagger", "openapi", "/api/", "developers", "api.")):
            return href
        if "api" in text and any(token in text for token in ("doc", "developer", "developers", "api reference", "swagger", "openapi")):
            return href
    return None


def validate_fuente(client: httpx.Client, fuente: dict) -> FuenteResult:
    origin = fuente["url"].strip()
    host = urlparse(origin).netloc.lower()
    configured_xml = normalize_url(fuente.get("url_rss"))
    homepage_html = ""
    homepage_status = None

    try:
        resp_home = client.get(origin)
        homepage_status = resp_home.status_code
        homepage_html = resp_home.text if resp_home.status_code < 500 else ""
    except Exception as exc:
        homepage_html = f"ERROR_HOME::{exc}"

    api_url = detect_api(origin, homepage_html if not homepage_html.startswith("ERROR_HOME::") else "")

    best_xml = None
    best_type = None
    tested: list[str] = []
    candidates = build_candidates(origin, configured_xml, homepage_html if not homepage_html.startswith("ERROR_HOME::") else "")

    type_priority = {"rss": 0, "atom": 1, "xml": 2, "sitemap": 3}
    best_score = 999

    for candidate in candidates:
        tested.append(candidate)
        try:
            resp = client.get(candidate)
        except Exception:
            continue
        ok, xml_type = classify_xml(candidate, resp)
        if not ok or not xml_type:
            continue
        score = type_priority.get(xml_type, 9)
        if score < best_score:
            best_xml = candidate
            best_type = xml_type
            best_score = score
        if score == 0:
            break

    if not best_xml and origin in KNOWN_FEEDS:
        best_xml = KNOWN_FEEDS[origin]
        best_type = "rss"

    if host in KNOWN_APIS and best_type == "sitemap":
        best_xml = None
        best_type = None

    if host in KNOWN_APIS and not best_xml:
        estado = "API"
        detalle = "Fuente identificada con API pública; sin XML útil validado"
    elif best_xml and configured_xml and normalize_url(best_xml) == normalize_url(configured_xml):
        estado = "OK"
        detalle = "XML configurado en DB validado"
    elif best_xml and configured_xml and normalize_url(best_xml) != normalize_url(configured_xml):
        estado = "REVISAR"
        detalle = f"DB apunta a {configured_xml}, pero se detectó mejor candidato {best_xml}"
    elif best_xml and not configured_xml:
        estado = "ENCONTRADO"
        detalle = "XML no estaba configurado en DB; se detectó automáticamente"
    elif api_url:
        estado = "API"
        detalle = "No se validó XML útil, pero sí una API pública"
    else:
        estado = "SIN_XML"
        if isinstance(homepage_status, int):
            detalle = f"No se detectó XML/API útil. Home status={homepage_status}"
        else:
            detalle = "No se detectó XML/API útil y falló la carga del home"

    return FuenteResult(
        id=fuente["id"],
        nombre=fuente["nombre"],
        pais=fuente["pais"],
        url_origen=origin,
        metodo_db=fuente.get("metodo"),
        url_xml_db=configured_xml,
        url_xml_detectada=best_xml,
        tipo_xml=best_type,
        url_api=api_url,
        estado=estado,
        detalle=detalle,
    )


def results_to_html(results: Iterable[FuenteResult]) -> str:
    rows = []
    for r in results:
        xml_link = f'<a href="{r.url_xml_detectada}">{r.url_xml_detectada}</a>' if r.url_xml_detectada else ""
        api_link = f'<a href="{r.url_api}">{r.url_api}</a>' if r.url_api else ""
        rows.append(
            f"""
            <tr>
              <td>{r.nombre}</td>
              <td>{r.pais}</td>
              <td><a href="{r.url_origen}">{r.url_origen}</a></td>
              <td>{xml_link}</td>
              <td>{api_link}</td>
              <td>{r.estado}</td>
              <td>{r.detalle}</td>
            </tr>
            """
        )
    return f"""
    <html>
      <body style="font-family:Arial,sans-serif">
        <h2>Investigación XML / API de fuentes</h2>
        <p>Tabla generada automáticamente desde la base de datos <code>fuentes</code>.</p>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:12px">
          <thead style="background:#f2f2f2">
            <tr>
              <th>Fuente</th>
              <th>País</th>
              <th>URL origen</th>
              <th>URL XML</th>
              <th>URL API</th>
              <th>Estado</th>
              <th>Detalle</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </body>
    </html>
    """


def save_outputs(results: list[FuenteResult]) -> tuple[Path, Path]:
    out_dir = ROOT / "tests" / "output" / "feeds"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "investigacion_fuentes_xml.csv"
    html_path = out_dir / "investigacion_fuentes_xml.html"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "id",
                "nombre",
                "pais",
                "url_origen",
                "metodo_db",
                "url_xml_db",
                "url_xml_detectada",
                "tipo_xml",
                "url_api",
                "estado",
                "detalle",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))

    html_path.write_text(results_to_html(results), encoding="utf-8")
    return csv_path, html_path


def send_email(html_body: str, recipients: list[str]) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Investigación de fuentes: XML / API"
    msg["From"] = os.environ["FROM_EMAIL"]
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.sendmail(os.environ["FROM_EMAIL"], recipients, msg.as_string())


def main() -> None:
    fuentes = load_fuentes()
    results: list[FuenteResult] = []

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20, verify=True) as client:
        for fuente in fuentes:
            result = validate_fuente(client, fuente)
            results.append(result)
            print(
                f"[{result.estado:<10}] {result.nombre} | xml={result.url_xml_detectada or '-'} | api={result.url_api or '-'}"
            )

    csv_path, html_path = save_outputs(results)
    html = html_path.read_text(encoding="utf-8")
    recipients = [e.strip() for e in os.environ["TO_EMAILS"].split(",") if e.strip()]
    send_email(html, recipients)
    print(f"\nCSV:  {csv_path}")
    print(f"HTML: {html_path}")
    print(f"Email enviado a: {', '.join(recipients)}")


if __name__ == "__main__":
    main()
