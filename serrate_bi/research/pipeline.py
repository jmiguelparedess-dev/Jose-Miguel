"""Orquestación de la investigación: descubrimiento de empresas, investigación individual
("INVESTIGAR MÁS") y actualización semanal."""
import json
import re

from .. import analysis
from .. import quality as q
from .. import repo
from ..db import get_setting, now_iso, set_setting
from . import claude_engine, extract, http, providers

CONTACT_PAGE_KEYWORDS = ["contact", "nosotros", "quienes", "empresa", "equipo", "directorio", "about",
                         "team", "historia", "staff", "gerencia"]
PROJECT_PAGE_KEYWORDS = ["proyecto", "obras", "portafolio", "portfolio", "desarrollos", "emprendimientos"]
TITLE_JUNK = re.compile(r"^(inicio|home|bienvenidos?|p[aá]gina principal|welcome)$", re.I)

DISCOVERY_WEB_TEMPLATES = {
    "Construcción": ["empresa constructora {city} Bolivia", "constructoras en {city} Bolivia obras civiles"],
    "Desarrollo inmobiliario": ["desarrolladora inmobiliaria {city} Bolivia", "inmobiliaria proyectos {city} Bolivia"],
    "Promoción inmobiliaria": ["promotora inmobiliaria {city} Bolivia preventa departamentos"],
    "Infraestructura": ["empresa infraestructura carreteras {city} Bolivia"],
}
DISCOVERY_NEWS_TEMPLATES = ["constructora {city}", "proyecto inmobiliario {city}", "condominio {city}",
                            "urbanización {city}", "edificio construcción {city}", "inversión inmobiliaria {city}",
                            "obra adjudicada {city} empresa"]
DEFAULT_CITIES = ["Santa Cruz", "La Paz", "Cochabamba"]


class RunContext:
    """Registro de avance y estadísticas de un trabajo."""

    def __init__(self, conn, run_id):
        self.conn, self.run_id = conn, run_id
        self.stats = {"empresas_nuevas": 0, "empresas_existentes": 0, "datos_nuevos": 0, "datos_verificados": 0,
                      "datos_modificados": 0, "alternativos": 0, "proyectos_nuevos": 0, "noticias_nuevas": 0,
                      "ejecutivos_nuevos": 0, "descartados_sin_fuente": 0, "consultas_fallidas": 0,
                      "errores": 0}

    def log(self, msg):
        if self.run_id:
            repo.run_log(self.conn, self.run_id, msg)

    def search(self, provs, query, max_results=10):
        def failed():
            self.stats["consultas_fallidas"] += 1
        return providers.search_with_fallback(provs, query, max_results, self.log, failed)

    def count_fact(self, result):
        key = {"nuevo": "datos_nuevos", "verificado": "datos_verificados", "mejorado": "datos_verificados",
               "reemplazado": "datos_modificados", "alternativo": "alternativos"}.get(result)
        if key:
            self.stats[key] += 1

    def fact(self, entity_type, entity_id, company_id, field, value, confidence, source_id, origin, evidence=None):
        r = repo.record_fact(self.conn, entity_type, entity_id, company_id, field, value, confidence, source_id,
                             origin, evidence, self.run_id)
        self.count_fact(r)
        return r


def _engine_mode(conn):
    mode = get_setting(conn, "research_engine", "auto")
    if mode == "claude" or (mode == "auto" and claude_engine.available()):
        return "claude+web" if claude_engine.available() else "web"
    return "web"


# =========================================================================== INVESTIGAR MÁS
def investigate_company(conn, company_id, run_id=None, light=False):
    ctx = RunContext(conn, run_id)
    c = repo.get_company(conn, company_id)
    if not c:
        raise ValueError("Empresa inexistente")
    ctx.log(f"Investigando «{c['name']}» (modo {'ligero' if light else 'completo'})")
    origin = "investigación automática"

    if not c["website"] and not light:
        _find_website(ctx, c, origin)
        c = repo.get_company(conn, company_id)
    if c["website"]:
        _crawl_official_site(ctx, c, origin, max_pages=3 if light else 7)
        c = repo.get_company(conn, company_id)
    if not light:
        if not c["linkedin_url"]:
            _find_linkedin_company(ctx, c, origin)
        _find_executives(ctx, c, origin)
    _find_news(ctx, c, origin)

    if _engine_mode(conn) == "claude+web" and not light:
        try:
            _claude_research(ctx, repo.get_company(conn, company_id))
        except Exception as e:  # el motor opcional nunca debe tumbar la investigación básica
            ctx.stats["errores"] += 1
            ctx.log(f"Motor Claude no disponible: {e}")

    c = repo.get_company(conn, company_id)
    cols = {"last_researched_at": now_iso()}
    if c["crm_status"] == "Nuevo prospecto":
        cols["crm_status"] = "Investigado"
    repo.touch_company(conn, company_id, **cols)
    analysis.analyze_and_store(conn, company_id)
    ctx.log("Investigación terminada: " + ", ".join(f"{k}={v}" for k, v in ctx.stats.items() if v))
    return ctx.stats


def _find_website(ctx, c, origin):
    core = q.core_key(c["name"])
    tokens = [t for t in core.split() if len(t) >= 4]
    results = ctx.search(providers.web_providers(), f'"{c["name"]}" Bolivia', 10)
    for r in results:
        dom = q.domain_of(r.url)
        if not dom or q.is_excluded_domain(dom) or q.is_news_domain(dom):
            continue
        dom_flat = dom.replace("-", "").replace(".", "")
        title_key = q.name_key(r.title)
        if (tokens and any(t in dom_flat for t in tokens)) or (core and core in title_key):
            sid = repo.upsert_source(ctx.conn, r.url, r.title, "buscador", r.provider, r.snippet)
            ctx.fact("company", c["id"], c["id"], "website", f"https://{dom}", q.PROBABLE, sid, origin,
                     f"Resultado de búsqueda: {r.title}")
            ctx.log(f"Posible sitio oficial: {dom}")
            return
    ctx.log("No se encontró sitio web oficial (queda pendiente)")


def _crawl_official_site(ctx, c, origin, max_pages=7):
    base = q.normalize_url(c["website"])
    try:
        home = http.fetch(base, respect_robots=True)
    except http.FetchError as e:
        ctx.log(f"Sitio no accesible: {e}")
        return
    if not home.ok:
        ctx.log(f"El sitio respondió HTTP {home.status}")
        return
    official = q.domain_of(home.url) or q.domain_of(base)
    title, desc, site_name = extract.page_title_and_description(home.text)
    home_text = extract.html_to_text(home.text)
    core = q.core_key(c["name"])
    sid = repo.upsert_source(ctx.conn, home.url, title, "sitio oficial", official, desc)
    name_on_site = core and (core in q.name_key(home_text[:20000]) or core in q.name_key(title or ""))
    ctx.fact("company", c["id"], c["id"], "website", f"https://{official}",
             q.VERIFICADO if name_on_site else q.PROBABLE, sid, origin,
             "El sitio responde" + (" y menciona el nombre de la empresa" if name_on_site else ""))
    if not name_on_site:
        ctx.log("Advertencia: el nombre de la empresa no aparece en el sitio; datos del sitio marcados como probables")
    conf = q.VERIFICADO if name_on_site else q.PROBABLE
    if desc:
        ctx.fact("company", c["id"], c["id"], "description", desc, conf, sid, origin, "Meta descripción del sitio")

    pages = [(home.url, home.text, sid)]
    links = extract.internal_links(home.text, home.url, CONTACT_PAGE_KEYWORDS + PROJECT_PAGE_KEYWORDS)
    for url in links[: max_pages - 1]:
        try:
            r = http.fetch(url, respect_robots=True)
        except http.FetchError as e:
            ctx.log(str(e))
            continue
        if r.ok and "html" in (r.content_type or "html"):
            t, d, _ = extract.page_title_and_description(r.text)
            pages.append((r.url, r.text, repo.upsert_source(ctx.conn, r.url, t, "sitio oficial", official, d)))
    ctx.log(f"Sitio oficial: {len(pages)} páginas leídas")

    all_text = ""
    for url, html, psid in pages:
        text = extract.html_to_text(html)
        all_text += "\n" + text
        mentions = extract.extract_executives(text)
        personal = {e for m in mentions for e in extract.emails_near(text, m.name, 150)}
        # Correos corporativos: los publicados que NO aparecen asociados a una persona.
        emails = [e for e in extract.extract_emails(html) if e not in personal]
        emails.sort(key=lambda e: (not e.endswith("@" + official), e))
        for e in emails:
            ctx.fact("company", c["id"], c["id"], "email", e, conf, psid, origin, f"Publicado en {url}")
        for p in extract.extract_phones(html):
            ctx.fact("company", c["id"], c["id"], "phone", p, conf, psid, origin, f"Publicado en {url}")
        li_companies, li_people = extract.extract_linkedin(html)
        for li in li_companies[:1]:
            ctx.fact("company", c["id"], c["id"], "linkedin_url", li, conf, psid, origin, f"Enlace en {url}")
        for m in mentions:
            eid, created = repo.upsert_executive(ctx.conn, c["id"], m.name, m.position, conf, psid, origin,
                                                 m.evidence, ctx.run_id)
            ctx.stats["ejecutivos_nuevos"] += int(created)
            for e in extract.emails_near(text, m.name, 150):
                ctx.fact("executive", eid, c["id"], "email", e, conf, psid, origin,
                         "Correo publicado junto al nombre del ejecutivo")
            for li in li_people:
                slug = q.strip_accents(m.name.lower()).split()
                if slug and all(part in li.lower() for part in slug[:2]):
                    ctx.fact("executive", eid, c["id"], "linkedin_url", li, conf, psid, origin,
                             "Enlace a LinkedIn publicado en el sitio oficial")
        is_project_page = any(k in url.lower() for k in PROJECT_PAGE_KEYWORDS)
        candidates = extract.extract_projects(text) if is_project_page else []
        if is_project_page:
            for h in extract.headings(html):
                for name, kind in extract.extract_projects(h):
                    candidates.append((name, kind))
        for name, kind in candidates[:25]:
            city, _ = q.detect_city(_context(text, name))
            pid, created = repo.add_project(ctx.conn, c["id"], name, kind, city, None, None, conf, psid)
            ctx.stats["proyectos_nuevos"] += int(created)

    sector = q.classify_sector(f"{title or ''} {desc or ''} {all_text[:30000]}")
    if sector:
        ctx.fact("company", c["id"], c["id"], "sector", sector, q.PROBABLE, sid, origin,
                 "Clasificado por palabras clave del sitio oficial")
    city, dept = q.detect_city(_contact_text(all_text))
    if city:
        ctx.fact("company", c["id"], c["id"], "city", city, q.PROBABLE, sid, origin,
                 "Ciudad mencionada en la información de contacto del sitio")
        ctx.fact("company", c["id"], c["id"], "department", dept, q.PROBABLE, sid, origin, "Derivado de la ciudad")
    if not c["activity"] and desc:
        ctx.fact("company", c["id"], c["id"], "activity", desc[:200], conf, sid, origin, "Descripción del sitio oficial")


def _context(text, needle, window=200):
    i = text.find(needle)
    return text[max(0, i - window): i + window] if i >= 0 else ""


def _contact_text(text):
    m = re.search(r"(direcci[oó]n|oficina|ubicaci[oó]n|contacto|address)", text, re.I)
    return text[m.start(): m.start() + 600] if m else text[:3000]


def _find_linkedin_company(ctx, c, origin):
    core = q.core_key(c["name"])
    results = ctx.search(providers.web_providers(), f'site:linkedin.com/company "{c["name"]}"', 8)
    for r in results:
        m = extract.LINKEDIN_COMPANY_RE.search(r.url)
        if m and core and core in q.name_key(r.title + " " + r.snippet):
            sid = repo.upsert_source(ctx.conn, r.url, r.title, "linkedin", r.provider, r.snippet)
            ctx.fact("company", c["id"], c["id"], "linkedin_url", m.group(0).rstrip("/"), q.PROBABLE, sid, origin,
                     f"Resultado de búsqueda: {r.title}")
            return


def _find_executives(ctx, c, origin):
    core = q.core_key(c["name"])
    queries = [f'"{c["name"]}" gerente general', f'site:linkedin.com/in "{c["name"]}"']
    for query in queries:
        for r in ctx.search(providers.web_providers(), query, 10):
            text = f"{r.title}. {r.snippet}"
            if not core or core not in q.name_key(text):
                continue  # el resultado no menciona a la empresa: no se asocia
            stype = "linkedin" if "linkedin.com" in r.url else "buscador"
            sid = repo.upsert_source(ctx.conn, r.url, r.title, stype, r.provider, r.snippet)
            if "linkedin.com/in/" in r.url:
                parsed = extract.parse_linkedin_title(r.title)
                if parsed and parsed[1] and any(k.lower() in parsed[1].lower() for k in extract.ROLES):
                    eid, created = repo.upsert_executive(ctx.conn, c["id"], parsed[0], parsed[1], q.PROBABLE, sid,
                                                         origin, text, ctx.run_id)
                    ctx.stats["ejecutivos_nuevos"] += int(created)
                    ctx.fact("executive", eid, c["id"], "linkedin_url", r.url.split("?")[0], q.PROBABLE, sid,
                             origin, r.title)
                continue
            for m in extract.extract_executives(text):
                eid, created = repo.upsert_executive(ctx.conn, c["id"], m.name, m.position, q.PROBABLE, sid,
                                                     origin, m.evidence, ctx.run_id)
                ctx.stats["ejecutivos_nuevos"] += int(created)


def _news_matches(core, text):
    nk = q.name_key(text)
    return bool(core) and (core in nk)


def _find_news(ctx, c, origin):
    core = q.core_key(c["name"])
    results = ctx.search(providers.news_providers(), f'"{c["name"]}"', 20)
    for r in results:
        text = f"{r.title}. {r.snippet}"
        if not _news_matches(core, text):
            continue
        sid = repo.upsert_source(ctx.conn, r.url, r.title, "noticia", r.publisher, r.snippet)
        _, created = repo.add_news(ctx.conn, c["id"], r.title, r.url, r.publisher, r.published_at, r.snippet,
                                   extract.classify_news(text), sid)
        ctx.stats["noticias_nuevas"] += int(created)
        for name, kind in extract.extract_projects(r.title):
            city, _ = q.detect_city(text)
            _, pc = repo.add_project(ctx.conn, c["id"], name, kind, city, r.title, None, q.PROBABLE, sid,
                                     r.published_at)
            ctx.stats["proyectos_nuevos"] += int(pc)
        for m in extract.extract_executives(text):
            eid, created = repo.upsert_executive(ctx.conn, c["id"], m.name, m.position, q.PROBABLE, sid, origin,
                                                 m.evidence, ctx.run_id)
            ctx.stats["ejecutivos_nuevos"] += int(created)


# --------------------------------------------------------------------------- Claude
def _verify_on_page(url, value, field):
    """Descarga la fuente y comprueba si el valor aparece literalmente. True / False / None (no se pudo)."""
    try:
        r = http.fetch(url)
    except http.FetchError:
        return None
    if not r.ok:
        return None
    text = extract.html_to_text(r.text) if "html" in (r.content_type or "html") else r.text
    if field == "phone":
        digits = re.sub(r"\D", "", value)[-7:]
        return digits in re.sub(r"\D", "", text)
    return q.strip_accents(value.lower()) in q.strip_accents(text.lower())


def _claude_confidence(ctx, url, value, field, official_domain):
    verified = _verify_on_page(url, value, field)
    dom = q.domain_of(url)
    if verified and official_domain and dom and (dom == official_domain or dom.endswith("." + official_domain)):
        return q.VERIFICADO, "Confirmado en el sitio oficial"
    if verified:
        return q.PROBABLE, "Confirmado en la fuente (tercero)"
    if verified is False:
        return q.PROBABLE, "Reportado por la búsqueda asistida; no se encontró textual en la página"
    return q.PROBABLE, "Reportado por la búsqueda asistida; la página no pudo descargarse para confirmar"


def _claude_research(ctx, c):
    ctx.log("Búsqueda asistida con Claude + búsqueda web")
    data = claude_engine.research_company(dict(c), log=ctx.log)
    ctx.stats["descartados_sin_fuente"] += data.get("discarded", 0)
    origin = "búsqueda asistida (Claude)"
    official = c["domain"]
    allowed = set(q.COMPANY_FIELDS) - {"name"}
    for f in data["fields"]:
        field, value, url = f.get("field"), f.get("value"), f.get("source_url")
        if field not in allowed or not value:
            continue
        if field == "sector" and value not in q.SECTORS:
            continue
        if field == "email" and not extract.EMAIL_RE.fullmatch(str(value).strip()):
            continue
        if field == "phone":
            value = q.normalize_phone(str(value))
            if not value:
                continue
        conf, note = _claude_confidence(ctx, url, str(value), field, official)
        sid = repo.upsert_source(ctx.conn, url, None, "búsqueda asistida", q.domain_of(url), f.get("quote"))
        ctx.fact("company", c["id"], c["id"], field, value, conf, sid, origin, f"{note}. Cita: {f.get('quote') or ''}")
    for e in data["executives"]:
        name, url = e.get("full_name"), e.get("source_url")
        if not name or not extract.valid_person_name(re.sub(r"^(Ing|Arq|Lic|Dr|Dra)\.?\s+", "", name)):
            continue
        conf, note = _claude_confidence(ctx, url, name, "full_name", official)
        sid = repo.upsert_source(ctx.conn, url, None, "búsqueda asistida", q.domain_of(url), e.get("quote"))
        eid, created = repo.upsert_executive(ctx.conn, c["id"], name, e.get("position"), conf, sid, origin,
                                             f"{note}. Cita: {e.get('quote') or ''}", ctx.run_id)
        ctx.stats["ejecutivos_nuevos"] += int(created)
        if e.get("email") and extract.EMAIL_RE.fullmatch(str(e["email"]).strip()):
            ec, en = _claude_confidence(ctx, url, e["email"], "email", official)
            ctx.fact("executive", eid, c["id"], "email", e["email"].strip().lower(), ec, sid, origin, en)
        if e.get("linkedin_url") and "linkedin.com/in/" in str(e["linkedin_url"]):
            ctx.fact("executive", eid, c["id"], "linkedin_url", e["linkedin_url"], q.PROBABLE, sid, origin,
                     "Perfil reportado por la búsqueda asistida")
    for p in data["projects"]:
        if not p.get("name"):
            continue
        sid = repo.upsert_source(ctx.conn, p["source_url"], None, "búsqueda asistida", q.domain_of(p["source_url"]))
        _, created = repo.add_project(ctx.conn, c["id"], p["name"], p.get("type"), p.get("city"),
                                      p.get("description"), p.get("status"), q.PROBABLE, sid, p.get("published_at"))
        ctx.stats["proyectos_nuevos"] += int(created)
    for n in data["news"]:
        sid = repo.upsert_source(ctx.conn, n["url"], n["title"], "noticia", n.get("publisher"), n.get("summary"))
        _, created = repo.add_news(ctx.conn, c["id"], n["title"], n["url"], n.get("publisher"), n.get("published_at"),
                                   n.get("summary"), extract.classify_news(f"{n['title']} {n.get('summary') or ''}"),
                                   sid)
        ctx.stats["noticias_nuevas"] += int(created)


# =========================================================================== DESCUBRIMIENTO
def _name_from_title(title):
    parts = [p.strip() for p in re.split(r"\s+[|\-–—:·]\s+", title or "") if p.strip()]
    parts = [p for p in parts if not TITLE_JUNK.match(p)]
    if not parts:
        return None
    for p in parts:
        if q.classify_sector(p) or re.search(r"\b(S\.?R\.?L|S\.?A|Ltda)\b", p):
            return p if len(p) <= 70 else None
    return parts[0] if len(parts[0]) <= 50 else None


def _mentions_bolivia(text, dom):
    return (dom or "").endswith(".bo") or bool(q.detect_city(text)[0]) or "bolivia" in (text or "").lower()


def discover(conn, run_id=None, cities=None, sectors=None, extra_query=None, investigate_new=True, max_new=10):
    ctx = RunContext(conn, run_id)
    cities = cities or DEFAULT_CITIES
    sectors = sectors or q.SECTORS
    new_ids = []
    ctx.log(f"Descubrimiento en {', '.join(cities)} — sectores: {', '.join(sectors)}")

    def add_candidate(name, website, source_id, evidence, text, conf=q.PROBABLE):
        name = q.clean_spaces(name)
        if not name or len(q.core_key(name)) < 3:
            return None
        dup = q.find_duplicate(conn, name, website)
        if dup:
            ctx.stats["empresas_existentes"] += 1
            return dup["id"]
        city, dept = q.detect_city(text)
        sector = q.classify_sector(text)
        cid, created = repo.create_company(conn, name, origin="descubrimiento automático", source_id=source_id,
                                           confidence=conf, run_id=run_id, website=website)
        if created:
            ctx.stats["empresas_nuevas"] += 1
            new_ids.append(cid)
            ctx.log(f"Nueva empresa: {name}" + (f" ({website})" if website else ""))
            if city:
                ctx.fact("company", cid, cid, "city", city, q.PROBABLE, source_id, "descubrimiento automático", evidence)
                ctx.fact("company", cid, cid, "department", dept, q.PROBABLE, source_id, "descubrimiento automático",
                         "Derivado de la ciudad")
            if sector:
                ctx.fact("company", cid, cid, "sector", sector, q.PROBABLE, source_id, "descubrimiento automático",
                         "Clasificado por palabras clave de la fuente")
        return cid

    # 1) Búsqueda web: sitios propios de empresas.
    for city in cities:
        for sector in sectors:
            for tpl in DISCOVERY_WEB_TEMPLATES.get(sector, []):
                for r in ctx.search(providers.web_providers(), tpl.format(city=city), 10):
                    dom = q.domain_of(r.url)
                    text = f"{r.title}. {r.snippet}"
                    if not dom:
                        continue
                    if q.is_excluded_domain(dom) or q.is_news_domain(dom):
                        names = extract.extract_company_names(text)
                        if names and _mentions_bolivia(text, dom):
                            sid = repo.upsert_source(conn, r.url, r.title, "buscador", r.provider, r.snippet)
                            for n in names:
                                add_candidate(n, None, sid, text, text)
                        continue
                    if not q.classify_sector(text) or not _mentions_bolivia(text, dom):
                        continue
                    name = _name_from_title(r.title)
                    if not name:
                        continue
                    sid = repo.upsert_source(conn, r.url, r.title, "buscador", r.provider, r.snippet)
                    add_candidate(name, f"https://{dom}", sid, text, text)

    # 2) Noticias: proyectos, obras e inversiones con empresas mencionadas.
    for city in cities:
        for tpl in DISCOVERY_NEWS_TEMPLATES:
            for r in ctx.search(providers.news_providers(), tpl.format(city=city), 15):
                text = f"{r.title}. {r.snippet}"
                names = extract.extract_company_names(text)
                if not names:
                    continue
                sid = repo.upsert_source(conn, r.url, r.title, "noticia", r.publisher, r.snippet)
                for n in names:
                    cid = add_candidate(n, None, sid, text, text)
                    if not cid:
                        continue
                    _, created = repo.add_news(conn, cid, r.title, r.url, r.publisher, r.published_at, r.snippet,
                                               extract.classify_news(text), sid)
                    ctx.stats["noticias_nuevas"] += int(created)
                    for pname, kind in extract.extract_projects(r.title):
                        pc_city, _ = q.detect_city(text)
                        _, pc = repo.add_project(conn, cid, pname, kind, pc_city, r.title, None, q.PROBABLE, sid,
                                                 r.published_at)
                        ctx.stats["proyectos_nuevos"] += int(pc)

    # 3) Búsqueda asistida (opcional).
    if _engine_mode(conn) == "claude+web":
        try:
            found = claude_engine.discover_companies(cities, sectors, extra_query, log=ctx.log)
            for comp in found:
                sid = repo.upsert_source(conn, comp["source_url"], None, "búsqueda asistida",
                                         q.domain_of(comp["source_url"]), comp.get("quote"))
                text = " ".join(str(comp.get(k) or "") for k in ("city", "sector", "activity", "quote"))
                cid = add_candidate(comp["name"], comp.get("website"), sid, comp.get("quote"), text)
                if cid:
                    for p in comp.get("projects", []):
                        psid = repo.upsert_source(conn, p["source_url"], None, "búsqueda asistida",
                                                  q.domain_of(p["source_url"]))
                        repo.add_project(conn, cid, p["name"], p.get("type"), None, None, None, q.PROBABLE, psid)
        except Exception as e:
            ctx.stats["errores"] += 1
            ctx.log(f"Motor Claude no disponible: {e}")
    elif extra_query:
        for r in ctx.search(providers.news_providers(), extra_query, 15):
            text = f"{r.title}. {r.snippet}"
            sid = repo.upsert_source(conn, r.url, r.title, "noticia", r.publisher, r.snippet)
            for n in extract.extract_company_names(text):
                add_candidate(n, None, sid, text, text)

    for cid in new_ids:
        analysis.analyze_and_store(conn, cid)
    if investigate_new:
        for cid in new_ids[:max_new]:
            try:
                st = investigate_company(conn, cid, run_id)
                for k in ("datos_nuevos", "proyectos_nuevos", "noticias_nuevas", "ejecutivos_nuevos"):
                    ctx.stats[k] += st.get(k, 0)
            except Exception as e:
                ctx.stats["errores"] += 1
                ctx.log(f"Error investigando empresa {cid}: {e}")
    ctx.log(f"Descubrimiento terminado: {ctx.stats['empresas_nuevas']} nuevas, "
            f"{ctx.stats['empresas_existentes']} ya registradas (duplicados evitados)")
    return ctx.stats


# =========================================================================== SEMANAL
def weekly_update(conn, run_id=None, discover_new=True):
    ctx = RunContext(conn, run_id)
    started = now_iso()
    rows = conn.execute("SELECT id, name FROM companies WHERE archived=0 AND crm_status != 'Descartado' "
                        "ORDER BY last_researched_at IS NOT NULL, last_researched_at").fetchall()
    ctx.log(f"Actualización semanal de {len(rows)} empresas")
    for r in rows:
        try:
            st = investigate_company(conn, r["id"], run_id, light=True)
            for k, v in st.items():
                ctx.stats[k] = ctx.stats.get(k, 0) + v
        except Exception as e:
            ctx.stats["errores"] += 1
            ctx.log(f"Error en {r['name']}: {e}")
    if discover_new:
        st = discover(conn, run_id, investigate_new=True, max_new=5)
        for k, v in st.items():
            ctx.stats[k] = ctx.stats.get(k, 0) + v
    changes = conn.execute("SELECT COUNT(*) AS n FROM change_log WHERE changed_at >= ?", (started,)).fetchone()["n"]
    ctx.stats["cambios_registrados"] = changes
    set_setting(conn, "last_weekly_run", now_iso())
    ctx.log(f"Actualización semanal terminada: {changes} cambios registrados")
    return ctx.stats


def stats_summary(stats):
    labels = {"empresas_nuevas": "empresas nuevas", "empresas_existentes": "duplicados evitados",
              "datos_nuevos": "datos nuevos", "datos_verificados": "datos reconfirmados",
              "datos_modificados": "datos modificados", "alternativos": "valores alternativos",
              "proyectos_nuevos": "proyectos nuevos", "noticias_nuevas": "noticias nuevas",
              "ejecutivos_nuevos": "ejecutivos nuevos", "descartados_sin_fuente": "descartados sin fuente",
              "cambios_registrados": "cambios registrados", "consultas_fallidas": "consultas sin respuesta",
              "errores": "errores"}
    parts = [f"{v} {labels.get(k, k)}" for k, v in stats.items() if v]
    if stats.get("consultas_fallidas") and not any(stats.get(k) for k in ("empresas_nuevas", "datos_nuevos",
                                                                         "noticias_nuevas", "proyectos_nuevos")):
        parts.insert(0, "ATENCIÓN: los buscadores no respondieron (verifique la conexión a internet)")
    return ", ".join(parts) or "Sin novedades"


def dumps(stats):
    return json.dumps(stats, ensure_ascii=False)
