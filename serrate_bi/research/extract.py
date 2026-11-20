"""Extractores de información pública. Principio: sólo se extrae lo que aparece LITERALMENTE en la fuente.
Nunca se deducen correos por formato, ni nombres, ni teléfonos."""
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import quality as q

NAME_TOKEN = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+"
NAME_RE = (r"(?:(?:Ing|Arq|Lic|Dr|Dra|Sr|Sra|MSc|Abog)\.?[ \t]+)?"
           rf"({NAME_TOKEN}(?:[ \t]+(?:de[ \t]+(?:la[ \t]+|los[ \t]+)?|del[ \t]+)?{NAME_TOKEN}){{1,3}})")
ROLES = [
    "Gerente General", "Gerente Propietario", "Socio Gerente", "Gerente Ejecutivo", "CEO",
    "Director Ejecutivo", "Director General", "Presidente Ejecutivo", "Presidente del Directorio",
    "Presidente", "Socio Fundador", "Fundador", "Propietario", "Socio Principal", "Representante Legal",
    "Director", "Gerente Comercial", "Gerente Técnico", "Gerente de Proyectos",
]
TOP_ROLES = {"Gerente General", "Gerente Propietario", "Socio Gerente", "CEO", "Director Ejecutivo",
             "Director General", "Presidente Ejecutivo", "Presidente del Directorio", "Presidente",
             "Socio Fundador", "Fundador", "Propietario", "Socio Principal", "Gerente Ejecutivo"}
ROLE_RE = "|".join(re.escape(r) for r in ROLES)
NOT_NAME_WORDS = {
    "constructora", "inmobiliaria", "santa", "cruz", "paz", "cochabamba", "bolivia", "gerente", "general",
    "director", "proyecto", "proyectos", "edificio", "condominio", "torre", "empresa", "grupo", "sierra",
    "nosotros", "contacto", "inicio", "servicios", "urbanización", "ingeniería", "construcción", "obras",
    "calle", "avenida", "av", "zona", "barrio", "equipo", "nuestro", "nuestra", "somos", "misión", "visión",
    "ver", "más", "leer", "click", "aquí", "lunes", "viernes", "sábado", "enero", "febrero", "marzo", "abril",
    "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre", "municipal",
    "gobierno", "alcaldía", "cámara", "asociación", "ley", "decreto", "ministerio", "sociedad", "anónima",
    "limitada", "desarrolladora", "unidad", "departamento", "departamentos", "residencial", "plaza",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24})(?![\w-])")
BAD_EMAIL_PARTS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "tudominio", "yourdomain",
                   "@2x", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "u003e", "noreply", "no-reply")
PHONE_CONTEXT_RE = re.compile(
    r"(?:tel[eé]fonos?|telf?s?\.?|cel(?:ular)?\.?|whatsapp|fono|central|llámanos|llamanos|phone)\s*[:.]?\s*"
    r"((?:\+?\s?591[\s.\-]?)?(?:\(?\d{1,2}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}"
    r"(?:\s*(?:/|,|\sy\s|\s-\s)\s*(?:\(?\d{1,2}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4})*)",
    re.I)
PHONE_INTL_RE = re.compile(r"(\+\s?591[\s.\-]?(?:\(?\d\)?[\s.\-]?)?\d{3,4}[\s.\-]?\d{3,4})")
LINKEDIN_COMPANY_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9\-_%.]+/?", re.I)
LINKEDIN_PERSON_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%.]+/?", re.I)

PROJECT_WORDS = ["Edificio", "Torre", "Torres", "Condominio", "Urbanización", "Residencial", "Complejo",
                 "Centro Comercial", "Barrio Cerrado", "Mall", "Hotel", "Puente", "Carretera", "Planta",
                 "Hospital", "Parque Industrial", "Ciudad", "Country Club", "Smart Center"]
PROJECT_NAME_RE = re.compile(
    r"\b(" + "|".join(PROJECT_WORDS) + r")\s+((?:[\"«“]?[A-ZÁÉÍÓÚÑ0-9][\wÁÉÍÓÚÑáéíóúñ\-\.']*[\"»”]?\s?){1,4})")
PROJECT_STOP = {"de", "en", "la", "el", "del", "para", "con", "y", "que", "se", "por", "los", "las", "Santa",
                "Bolivia", "Cruz", "La", "El", "En"}

NEWS_CATEGORIES = {
    "Proyecto": ["proyecto", "edificio", "torre", "condominio", "urbanización", "construirá", "construye",
                 "lanzamiento", "lanza", "inaugura", "obra", "complejo", "residencial"],
    "Inversión": ["inversión", "invierte", "invertirá", "millones", "capital", "$us", "usd", "dólares"],
    "Contratación pública": ["licitación", "adjudica", "adjudicó", "sicoes", "contrato con", "gobernación",
                             "alcaldía", "abc", "ministerio de obras", "obra pública"],
    "Conflicto / litigio": ["demanda", "juicio", "arbitraje", "conflicto", "denuncia", "paraliza",
                            "paralización", "incumplimiento", "estafa", "reclamo", "avasall", "clausura",
                            "rescisión", "resolución de contrato", "proceso penal", "querella"],
    "Financiamiento": ["crédito", "financiamiento", "préstamo", "bono", "bolsa boliviana", "emisión",
                       "fideicomiso", "pagarés", "titularización", "banco"],
    "Expansión": ["expansión", "nueva sede", "nuevo mercado", "alianza", "fusión", "adquisición", "consorcio",
                  "joint venture", "socio estratégico"],
    "Regulatorio": ["normativa", "reglamento", "ley ", "decreto", "licencia de construcción",
                    "aprobación de planos", "uso de suelo", "impuestos", "derecho propietario"],
}


@dataclass
class ExecutiveMention:
    name: str
    position: str
    evidence: str


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n")).strip()


def extract_emails(html_or_text):
    found = []
    soup = BeautifulSoup(html_or_text, "html.parser")
    for a in soup.select('a[href^="mailto:"]'):
        addr = a["href"][7:].split("?")[0].strip()
        found.append(addr)
    found += EMAIL_RE.findall(soup.get_text(" "))
    out = []
    for e in found:
        e = e.strip().strip(".").lower()
        if not EMAIL_RE.fullmatch(e) or any(b in e for b in BAD_EMAIL_PARTS):
            continue
        if e not in out:
            out.append(e)
    return out


def extract_phones(html_or_text):
    soup = BeautifulSoup(html_or_text, "html.parser")
    raw = [a["href"][4:] for a in soup.select('a[href^="tel:"]')]
    text = soup.get_text(" ")
    for m in PHONE_CONTEXT_RE.finditer(text):
        # Varios números en la misma línea: «3-3456789 / 3-3456790», «… y …», «… - …» (guion con espacios).
        raw += re.split(r"\s*(?:/|,|\sy\s|\s-\s)\s*", m.group(1))
    raw += PHONE_INTL_RE.findall(text)
    out = []
    for r in raw:
        p = q.normalize_phone(r)
        if p and p not in out:
            out.append(p)
    return out


def extract_linkedin(html):
    companies, people = [], []
    for m in LINKEDIN_COMPANY_RE.findall(html):
        u = m.rstrip("/").split("?")[0]
        if u not in companies:
            companies.append(u)
    for m in LINKEDIN_PERSON_RE.findall(html):
        u = m.rstrip("/").split("?")[0]
        if u not in people:
            people.append(u)
    return companies, people


def valid_person_name(name):
    toks = [t for t in name.split() if t.lower() not in ("de", "la", "los", "del")]
    if not 2 <= len(toks) <= 4:
        return False
    if any(t.lower() in NOT_NAME_WORDS or q.strip_accents(t.lower()) in NOT_NAME_WORDS for t in toks):
        return False
    return all(len(t) >= 2 for t in toks)


def extract_executives(text):
    """Busca pares (nombre, cargo) explícitos: 'Gerente General: Ing. Juan Pérez' o 'Juan Pérez, Gerente General'."""
    out, seen = [], set()
    patterns = [
        re.compile(rf"({ROLE_RE})(?:\s+de\s+[^\n:,;]{{2,60}}?)?\s*[:\-–,|]\s*{NAME_RE}"),
        re.compile(rf"{NAME_RE}\s*[,\-–|(]\s*(?:es\s+(?:el|la)\s+)?({ROLE_RE})\b"),
        re.compile(rf"(?:el|la)\s+({ROLE_RE})\s+(?:de\s+[A-ZÁÉÍÓÚÑ][^\n,;:]{{2,60}}?,\s*)?{NAME_RE}"),
        # Nombre solo en una línea y cargo al inicio de la siguiente (tarjetas, páginas de equipo).
        re.compile(rf"^[ \t]*{NAME_RE}[ \t]*\n[ \t]*({ROLE_RE})\b", re.M),
    ]
    for i, pat in enumerate(patterns):
        for m in pat.finditer(text):
            if i in (1, 3):
                name, role = m.group(1), m.group(2)
            else:
                role, name = m.group(1), m.group(2)
            name = q.clean_spaces(name)
            if not valid_person_name(name):
                continue
            key = q.strip_accents(name.lower())
            if key in seen:
                continue
            seen.add(key)
            s, e = max(0, m.start() - 80), min(len(text), m.end() + 80)
            out.append(ExecutiveMention(name, _canonical_role(role), q.clean_spaces(text[s:e])))
    return out


def _canonical_role(role):
    for r in ROLES:
        if r.lower() == role.lower():
            return r
    return role


def parse_linkedin_title(title):
    """'Juan Pérez - Gerente General - Constructora X | LinkedIn' -> (nombre, cargo, empresa)."""
    t = re.sub(r"\s*\|\s*LinkedIn.*$", "", title or "", flags=re.I)
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", t) if p.strip()]
    if len(parts) < 2:
        return None
    name = parts[0]
    if not valid_person_name(name):
        return None
    position = parts[1] if len(parts) >= 2 else None
    company = parts[2] if len(parts) >= 3 else None
    return name, position, company


GENERIC_LOCALPARTS = {"info", "informacion", "ventas", "contacto", "contact", "comercial", "administracion",
                      "admin", "gerencia", "rrhh", "proyectos", "marketing", "consultas", "atencion", "hola",
                      "recepcion", "secretaria", "oficina", "soporte"}


def emails_near(text, name, window=200):
    """Correos publicados junto al nombre del ejecutivo: en la misma línea, o en las 3 líneas siguientes
    (bloque tipo tarjeta: nombre / cargo / correo) siempre que no aparezca otro cargo u otra persona antes.
    En el bloque siguiente no se atribuyen correos genéricos (info@, ventas@, …) a una persona."""
    out = []
    lines = [ln for ln in text.split("\n") if ln.strip()]
    role_re = re.compile(ROLE_RE)
    person_line = re.compile(rf"^[ \t]*{NAME_RE}[ \t]*$")

    def add(chunk, allow_generic):
        for e in EMAIL_RE.findall(chunk):
            e = e.lower()
            if any(b in e for b in BAD_EMAIL_PARTS) or e in out:
                continue
            if not allow_generic and e.split("@")[0] in GENERIC_LOCALPARTS:
                continue
            out.append(e)

    for i, ln in enumerate(lines):
        if name not in ln:
            continue
        add(ln[:window * 2], True)
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if role_re.search(nxt) and not (j == i + 1 and len(nxt) < 60 and not EMAIL_RE.search(nxt)):
                break  # otro cargo: comienza la información de otra persona
            m = person_line.match(nxt)
            if m and extract_valid_name(m.group(1)) and name not in nxt:
                break
            if len(nxt) < 160:
                add(nxt, False)
    return out


def extract_valid_name(n):
    return valid_person_name(q.clean_spaces(n))


def extract_projects(text):
    out, seen = [], set()
    for m in PROJECT_NAME_RE.finditer(text):
        kind = m.group(1)
        words = []
        for w in m.group(2).split():
            w2 = w.strip("\"«»“”.,")
            if not w2 or (w2 in PROJECT_STOP and words) or w2.lower() in ("de", "en", "para", "con", "y"):
                break
            words.append(w2)
        if not words or all(w in PROJECT_STOP for w in words):
            continue
        name = f"{kind} {' '.join(words)}"
        k = q.name_key(name)
        if k in seen or len(words[0]) < 2:
            continue
        seen.add(k)
        out.append((name, kind))
    return out


def classify_news(text):
    t = (text or "").lower()
    return [cat for cat, kws in NEWS_CATEGORIES.items() if any(k in t for k in kws)]


COMPANY_NAME_PATTERNS = [
    re.compile(r"\b((?:Constructora|Inmobiliaria|Desarrolladora|Urbanizadora|Promotora|Grupo|Consorcio)"
               r"(?:\s+(?:[A-ZÁÉÍÓÚÑ][\w&\.\-ÁÉÍÓÚÑáéíóúñ]*|&|y|de)){1,4})"),
    re.compile(r"\b((?:[A-ZÁÉÍÓÚÑ][\w&\.\-ÁÉÍÓÚÑáéíóúñ]+\s){1,4}(?:S\.R\.L\.|SRL|S\.A\.|Ltda\.))"),
]
COMPANY_NAME_STOP_TAIL = {"de", "y", "&", "en", "del", "la", "el", "Santa", "Bolivia", "Cruz", "La", "Paz"}


def extract_company_names(text):
    """Nombres de empresa mencionados explícitamente en un texto (p. ej. un titular de prensa)."""
    out = []
    for pat in COMPANY_NAME_PATTERNS:
        for m in pat.finditer(text or ""):
            name = q.clean_spaces(m.group(1))
            toks = name.split()
            while toks and toks[-1] in COMPANY_NAME_STOP_TAIL:
                toks.pop()
            name = " ".join(toks)
            core = q.core_key(name)
            if len(toks) < 2 or len(core) < 3:
                continue
            if any(q.name_key(name) == q.name_key(o) for o in out):
                continue
            out.append(name)
    return out


def internal_links(html, base_url, keywords):
    """Enlaces del mismo dominio cuyo texto o URL contiene alguna palabra clave."""
    soup = BeautifulSoup(html, "html.parser")
    base_dom = q.domain_of(base_url)
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if q.domain_of(href) != base_dom or not urlparse(href).scheme.startswith("http"):
            continue
        label = (a.get_text(" ") + " " + href).lower()
        if any(k in q.strip_accents(label) for k in keywords) and href not in out and href.rstrip("/") != base_url.rstrip("/"):
            out.append(href)
    return out


def page_title_and_description(html):
    soup = BeautifulSoup(html, "html.parser")
    title = q.clean_spaces(soup.title.get_text()) if soup.title else None
    desc = None
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            desc = q.clean_spaces(m["content"])
            break
    site_name = None
    m = soup.find("meta", attrs={"property": "og:site_name"})
    if m and m.get("content"):
        site_name = q.clean_spaces(m["content"])
    return title, desc, site_name


def headings(html, levels=("h1", "h2", "h3", "h4")):
    soup = BeautifulSoup(html, "html.parser")
    return [q.clean_spaces(h.get_text(" ")) for h in soup.find_all(levels) if q.clean_spaces(h.get_text(" "))]


def social_links(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=True):
        h = a["href"]
        for net in ("facebook.com", "instagram.com", "youtube.com", "tiktok.com", "x.com", "twitter.com"):
            if net in h and net not in out:
                out[net] = h.split("?")[0]
    return out
