"""Reglas de calidad de datos: niveles de confianza, normalización y deduplicación."""
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlparse

# --- Niveles de confianza -------------------------------------------------
VERIFICADO = "verificado"
PROBABLE = "probable"
NO_DISPONIBLE = "no disponible"
CONFIDENCE_RANK = {NO_DISPONIBLE: 0, PROBABLE: 1, VERIFICADO: 2}
CONFIDENCE_HELP = {
    VERIFICADO: "Publicado en el sitio oficial de la empresa o en un registro oficial, confirmado por "
                "dos fuentes independientes, o confirmado manualmente.",
    PROBABLE: "Encontrado en una sola fuente de terceros (prensa, buscador, LinkedIn, directorio). "
              "Requiere confirmación antes de usarlo.",
    NO_DISPONIBLE: "No se encontró en fuentes públicas. Pendiente.",
}

# --- Estados CRM -----------------------------------------------------------
CRM_STATUSES = [
    "Nuevo prospecto", "Investigado", "Pendiente de contacto", "Contactado",
    "Reunión solicitada", "Reunión confirmada", "Reunión realizada",
    "Negociación", "Cliente", "Descartado",
]
ACTIVITY_KINDS = ["Comentario", "Llamada", "Correo enviado", "Mensaje LinkedIn", "Reunión",
                  "Propuesta enviada", "Cambio de estado", "Tarea de seguimiento"]

# --- Campos ----------------------------------------------------------------
COMPANY_FIELDS = {
    "name": "Nombre de la empresa",
    "legal_name": "Razón social",
    "activity": "Actividad",
    "sector": "Sector",
    "city": "Ciudad",
    "department": "Departamento",
    "website": "Página web",
    "email": "Correo corporativo",
    "phone": "Teléfono corporativo",
    "linkedin_url": "LinkedIn de la empresa",
    "description": "Descripción",
}
# Campos cuya ausencia hace que la ficha se considere incompleta.
KEY_COMPANY_FIELDS = ["activity", "sector", "city", "website", "email", "phone", "linkedin_url"]
EXECUTIVE_FIELDS = {
    "full_name": "Nombre",
    "position": "Cargo",
    "email": "Correo profesional",
    "phone": "Teléfono",
    "linkedin_url": "LinkedIn del ejecutivo",
}

SECTORS = ["Construcción", "Desarrollo inmobiliario", "Promoción inmobiliaria", "Infraestructura"]

SECTOR_KEYWORDS = {
    "Infraestructura": ["infraestructura", "carretera", "carreteras", "puente", "puentes", "vial", "viales",
                        "obras públicas", "obra pública", "saneamiento", "alcantarillado", "hidroeléctrica",
                        "represa", "ferrovi", "aeropuerto", "pavimento", "asfalt", "movimiento de tierras"],
    "Promoción inmobiliaria": ["promotora", "promoción inmobiliaria", "preventa", "comercializa",
                               "venta de departamentos", "corredor inmobiliario", "bienes raíces"],
    "Desarrollo inmobiliario": ["desarrolladora", "desarrollo inmobiliario", "desarrollos inmobiliarios",
                                "inmobiliaria", "condominio", "urbanización", "urbanizaciones", "loteamiento",
                                "edificio", "torre", "departamentos", "barrio cerrado", "residencial"],
    "Construcción": ["constructora", "construcción", "construcciones", "obras civiles", "ingeniería civil",
                     "contratista", "edificaciones"],
}

# --- Geografía -------------------------------------------------------------
CITY_TO_DEPARTMENT = {
    "Santa Cruz de la Sierra": "Santa Cruz", "Montero": "Santa Cruz", "Warnes": "Santa Cruz",
    "Cotoca": "Santa Cruz", "Porongo": "Santa Cruz", "La Guardia": "Santa Cruz", "Camiri": "Santa Cruz",
    "Puerto Suárez": "Santa Cruz", "San Ignacio de Velasco": "Santa Cruz",
    "La Paz": "La Paz", "El Alto": "La Paz", "Viacha": "La Paz",
    "Cochabamba": "Cochabamba", "Quillacollo": "Cochabamba", "Sacaba": "Cochabamba",
    "Tiquipaya": "Cochabamba", "Colcapirhua": "Cochabamba", "Vinto": "Cochabamba",
    "Sucre": "Chuquisaca", "Tarija": "Tarija", "Yacuiba": "Tarija", "Bermejo": "Tarija",
    "Oruro": "Oruro", "Potosí": "Potosí", "Uyuni": "Potosí", "Trinidad": "Beni", "Riberalta": "Beni",
    "Cobija": "Pando",
}
CITY_ALIASES = {
    "santa cruz de la sierra": "Santa Cruz de la Sierra", "santa cruz": "Santa Cruz de la Sierra",
    "scz": "Santa Cruz de la Sierra", "la paz": "La Paz", "lpz": "La Paz", "el alto": "El Alto",
    "cochabamba": "Cochabamba", "cbba": "Cochabamba",
}
PRIORITY_DEPARTMENTS = ["Santa Cruz", "La Paz", "Cochabamba"]
DEPARTMENTS = ["Santa Cruz", "La Paz", "Cochabamba", "Chuquisaca", "Tarija", "Oruro", "Potosí", "Beni", "Pando"]

LEGAL_FORMS = [
    r"sociedad de responsabilidad limitada", r"sociedad an[oó]nima mixta", r"sociedad an[oó]nima",
    r"s\.?\s?r\.?\s?l\.?", r"s\.?\s?a\.?\s?m\.?", r"s\.?\s?a\.?", r"ltda\.?", r"limitada",
    r"e\.?\s?i\.?\s?r\.?\s?l\.?", r"unipersonal", r"s\.?\s?c\.?\s?a\.?",
]
GENERIC_WORDS = {"constructora", "construcciones", "construccion", "inmobiliaria", "desarrolladora",
                 "desarrollos", "grupo", "empresa", "compania", "cia", "ingenieria", "bolivia", "y", "de",
                 "del", "la", "el", "los", "las", "and", "asociados", "proyectos", "inversiones",
                 "promotora", "urbanizadora", "consorcio", "srl", "sa", "ltda"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clean_spaces(s):
    return re.sub(r"\s+", " ", s or "").strip()


def name_key(name: str) -> str:
    """Clave normalizada de nombre de empresa (sin acentos, forma societaria ni puntuación)."""
    s = strip_accents((name or "").lower())
    s = s.replace("&", " y ")
    for lf in LEGAL_FORMS:
        s = re.sub(r"(?<![a-z])" + lf + r"(?![a-z])", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return clean_spaces(s)


def core_key(name: str) -> str:
    """Clave sin palabras genéricas, para detectar duplicados como 'Constructora X' vs 'X SRL'."""
    tokens = [t for t in name_key(name).split() if t not in GENERIC_WORDS]
    return " ".join(tokens)


def domain_of(url: str):
    if not url:
        return None
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    try:
        host = urlparse(u).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_url(url: str):
    if not url:
        return None
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u.rstrip("/")


def normalize_phone(raw: str):
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00591"):
        digits = digits[5:]
    elif digits.startswith("591") and len(digits) > 9:
        digits = digits[3:]
    if len(digits) not in (7, 8):
        return None
    return "+591 " + digits


def value_key(field: str, value: str) -> str:
    v = clean_spaces(str(value))
    if field in ("email",):
        return v.lower()
    if field in ("website", "linkedin_url"):
        return (normalize_url(v) or "").lower().replace("http://", "https://").replace("://www.", "://")
    if field == "phone":
        return re.sub(r"\D", "", v)[-8:]
    return strip_accents(v.lower())


def similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_duplicate(conn, name: str, website: str = None):
    """Devuelve la fila de empresa existente que corresponde a (name, website), o None."""
    nk, ck, dom = name_key(name), core_key(name), domain_of(website) if website else None
    if nk:
        row = conn.execute("SELECT * FROM companies WHERE name_key=?", (nk,)).fetchone()
        if row:
            return row
    if dom and not is_excluded_domain(dom):
        row = conn.execute("SELECT * FROM companies WHERE domain=?", (dom,)).fetchone()
        if row:
            return row
    if ck and len(ck) >= 4:
        row = conn.execute("SELECT * FROM companies WHERE core_key=?", (ck,)).fetchone()
        if row:
            return row
        for row in conn.execute("SELECT * FROM companies WHERE length(core_key) >= 4"):
            if similar(ck, row["core_key"]) >= 0.92:
                return row
    return None


# Dominios que nunca son el sitio oficial de una empresa constructora.
EXCLUDED_DOMAINS = [
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "wikipedia.org", "google.com", "news.google.com", "bing.com", "duckduckgo.com", "gob.bo",
    "eldeber.com.bo", "la-razon.com", "larazon.com.bo", "opinion.com.bo", "lostiempos.com",
    "paginasiete.bo", "eju.tv", "unitel.bo", "reduno.com.bo", "erbol.com.bo", "correodelsur.com",
    "elmundo.com.bo", "infobae.com", "elpais.com", "eldiario.net", "brujuladigital.net", "visionsostenible.com",
    "mercadolibre.com", "olx.com.bo", "ultracasas.com", "infocasas.com.bo", "properati.com",
    "paginasamarillas.com.bo", "cylex.com.bo", "empresite.com", "dnb.com", "bnamericas.com", "emis.com",
    "glassdoor.com", "indeed.com", "computrabajo.com", "trabajito.com.bo", "whatsapp.com", "wa.me",
    "blogspot.com", "wordpress.com", "medium.com", "scribd.com", "issuu.com", "pinterest.com",
    "tripadvisor.com", "maps.google.com", "goo.gl", "bit.ly", "linktr.ee",
]
NEWS_DOMAINS = [
    "eldeber.com.bo", "la-razon.com", "larazon.com.bo", "opinion.com.bo", "lostiempos.com", "paginasiete.bo",
    "eju.tv", "unitel.bo", "reduno.com.bo", "erbol.com.bo", "correodelsur.com", "elmundo.com.bo",
    "infobae.com", "eldiario.net", "brujuladigital.net", "visionsostenible.com", "bnamericas.com",
    "abi.bo", "elalteno.com.bo", "ahoraelpueblo.bo", "noticiasfides.com", "elpotosi.net",
]
OFFICIAL_REGISTRY_HINTS = ["gob.bo", "seprec", "fundempresa", "sicoes"]


def _domain_matches(dom: str, candidates) -> bool:
    return any(dom == c or dom.endswith("." + c) for c in candidates)


def is_excluded_domain(dom: str) -> bool:
    return bool(dom) and _domain_matches(dom, EXCLUDED_DOMAINS)


def is_news_domain(dom: str) -> bool:
    return bool(dom) and _domain_matches(dom, NEWS_DOMAINS)


def is_official_registry(dom: str) -> bool:
    return bool(dom) and any(h in dom for h in OFFICIAL_REGISTRY_HINTS)


def detect_city(text: str):
    """Detecta la primera ciudad boliviana mencionada. Devuelve (ciudad, departamento) o (None, None)."""
    if not text:
        return None, None
    t = strip_accents(text.lower())
    best = None
    for city in CITY_TO_DEPARTMENT:
        k = strip_accents(city.lower())
        m = re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), city)
    for alias, city in CITY_ALIASES.items():
        m = re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), city)
    if not best:
        return None, None
    return best[1], CITY_TO_DEPARTMENT.get(best[1])


def classify_sector(text: str):
    """Clasifica el sector por palabras clave. Devuelve None si no hay evidencia suficiente."""
    if not text:
        return None
    t = text.lower()
    scores = {}
    for sector, kws in SECTOR_KEYWORDS.items():
        scores[sector] = sum(t.count(k) for k in kws)
    sector, score = max(scores.items(), key=lambda kv: kv[1])
    return sector if score > 0 else None


def is_incomplete(company_row) -> bool:
    return any(not company_row[f] for f in KEY_COMPANY_FIELDS)


def missing_fields(company_row, has_executive: bool):
    missing = [COMPANY_FIELDS[f] for f in KEY_COMPANY_FIELDS if not company_row[f]]
    if not has_executive:
        missing.append("Gerente general / ejecutivo principal")
    return missing
