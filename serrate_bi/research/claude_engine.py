"""Motor opcional de investigación y redacción con Claude (API de Anthropic) + búsqueda web del servidor.

Se activa si existe ANTHROPIC_API_KEY (o ANTHROPIC_AUTH_TOKEN). Salvaguardas contra datos inventados:
  * Se exige una URL de fuente y una cita textual para cada dato.
  * La URL debe figurar entre los resultados de búsqueda/lectura que el propio servidor devolvió en esa
    conversación (o pertenecer al dominio oficial de la empresa); si no, el dato se descarta.
  * Después, el pipeline vuelve a descargar la fuente y comprueba que el valor aparece en ella para decidir
    entre 'verificado' y 'probable'.
"""
import json
import os
import re

MODEL = os.environ.get("SERRATE_BI_CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM = (
    "Eres un analista de inteligencia comercial que trabaja para el estudio jurídico boliviano "
    "Serrate Paz & Asociados. Investigas empresas bolivianas de construcción, desarrollo y promoción "
    "inmobiliaria e infraestructura usando únicamente fuentes públicas verificables.\n"
    "Reglas estrictas:\n"
    "- No inventes ni deduzcas datos. Si un dato no aparece en una fuente que consultaste, omítelo.\n"
    "- Nunca construyas correos electrónicos a partir de un formato supuesto (p. ej. nombre.apellido@...).\n"
    "- Cada dato debe incluir la URL exacta de la fuente donde lo leíste y una cita textual breve de esa fuente.\n"
    "- Responde al final con un único bloque ```json``` con el esquema pedido, sin texto adicional después."
)


class ClaudeUnavailable(Exception):
    pass


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _client():
    if not available():
        raise ClaudeUnavailable("No hay credenciales de Anthropic configuradas (ANTHROPIC_API_KEY).")
    import anthropic
    return anthropic.Anthropic()


def _create(client, **kwargs):
    """Llamada con respaldo del servidor ante rechazos (fallbacks='default'); si la cuenta no lo admite,
    se reintenta sin él."""
    import anthropic
    try:
        return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        return client.messages.create(**kwargs)


def _run_with_web(prompt, max_searches=8, log=None):
    client = _client()
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches,
         "user_location": {"type": "approximate", "country": "BO", "timezone": "America/La_Paz"}},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
    ]
    messages = [{"role": "user", "content": prompt}]
    seen_urls, texts = set(), []
    for _ in range(6):
        resp = _create(client, model=MODEL, max_tokens=16000, system=SYSTEM, tools=tools,
                       output_config={"effort": "medium"}, messages=messages)
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype == "web_search_tool_result":
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for item in content:
                        url = getattr(item, "url", None)
                        if url:
                            seen_urls.add(url)
            elif btype == "web_fetch_tool_result":
                content = getattr(block, "content", None)
                url = getattr(content, "url", None)
                if url:
                    seen_urls.add(url)
            elif btype == "text":
                texts.append(block.text)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "refusal":
            raise ClaudeUnavailable("Claude rechazó la solicitud.")
        break
    if log:
        log(f"Claude consultó {len(seen_urls)} fuentes web")
    return parse_json_block("\n".join(texts)), seen_urls


def parse_json_block(text):
    m = re.search(r"```json\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        s, e = text.find("{"), text.rfind("}")
        raw = text[s:e + 1] if s >= 0 and e > s else "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _url_ok(url, seen_urls, official_domain=None):
    from .. import quality as q
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return False
    if url in seen_urls or url.rstrip("/") in {u.rstrip("/") for u in seen_urls}:
        return True
    d = q.domain_of(url)
    return bool(official_domain and d and (d == official_domain or d.endswith("." + official_domain)))


def research_company(company, log=None):
    """Investiga una empresa. Devuelve dict con listas de hallazgos ya filtrados por fuente."""
    known = {k: company.get(k) for k in ("name", "website", "city", "sector", "activity") if company.get(k)}
    prompt = (
        "Investiga en la web la siguiente empresa boliviana y devuelve datos públicos verificables.\n"
        f"Datos conocidos: {json.dumps(known, ensure_ascii=False)}\n\n"
        "Busca: sitio web oficial, actividad, ciudad, teléfono corporativo, correo corporativo publicado, "
        "LinkedIn de la empresa, gerente general / CEO / propietario / socio principal (nombre, cargo, LinkedIn "
        "y correo profesional sólo si están publicados), proyectos (edificios, condominios, urbanizaciones, "
        "obras) y noticias recientes (inversiones, licitaciones, conflictos).\n\n"
        "Esquema JSON:\n"
        '{"fields": [{"field": "website|activity|sector|city|phone|email|linkedin_url|description|legal_name", '
        '"value": "...", "source_url": "...", "quote": "..."}],\n'
        ' "executives": [{"full_name": "...", "position": "...", "linkedin_url": null, "email": null, '
        '"source_url": "...", "quote": "..."}],\n'
        ' "projects": [{"name": "...", "type": "...", "city": "...", "status": "...", "description": "...", '
        '"source_url": "...", "published_at": "AAAA-MM-DD o null"}],\n'
        ' "news": [{"title": "...", "url": "...", "publisher": "...", "published_at": "AAAA-MM-DD o null", '
        '"summary": "..."}]}\n'
        "El sector debe ser uno de: Construcción, Desarrollo inmobiliario, Promoción inmobiliaria, Infraestructura."
    )
    data, seen = _run_with_web(prompt, log=log)
    from .. import quality as q
    official = q.domain_of(company.get("website")) if company.get("website") else None
    for f in data.get("fields", []) or []:
        if f.get("field") == "website" and f.get("value"):
            official = official or q.domain_of(f["value"])
    out = {"fields": [], "executives": [], "projects": [], "news": [], "discarded": 0}
    for key in ("fields", "executives", "projects"):
        for item in data.get(key, []) or []:
            if isinstance(item, dict) and _url_ok(item.get("source_url"), seen, official):
                out[key].append(item)
            else:
                out["discarded"] += 1
    for item in data.get("news", []) or []:
        if isinstance(item, dict) and item.get("title") and _url_ok(item.get("url"), seen, None):
            out["news"].append(item)
        else:
            out["discarded"] += 1
    return out


def discover_companies(cities, sectors, extra_query=None, limit=15, log=None):
    prompt = (
        f"Encuentra hasta {limit} empresas bolivianas reales de los sectores {', '.join(sectors)} con actividad en "
        f"{', '.join(cities)}. Prioriza empresas con proyectos nuevos, edificios, urbanizaciones, condominios, "
        "inversiones u obras en ejecución recientes.\n"
        + (f"Enfoque adicional: {extra_query}\n" if extra_query else "") +
        "Esquema JSON:\n"
        '{"companies": [{"name": "...", "website": "url o null", "city": "...", "sector": "...", '
        '"activity": "...", "source_url": "...", "quote": "...", '
        '"projects": [{"name": "...", "type": "...", "source_url": "..."}]}]}'
    )
    data, seen = _run_with_web(prompt, max_searches=10, log=log)
    out = []
    for c in data.get("companies", []) or []:
        if isinstance(c, dict) and c.get("name") and _url_ok(c.get("source_url"), seen,
                                                              None):
            c["projects"] = [p for p in c.get("projects") or [] if isinstance(p, dict)
                             and _url_ok(p.get("source_url"), seen, None)]
            out.append(c)
    return out


def draft_outreach(facts: dict):
    """Redacta los cuatro textos de contacto a partir de hechos ya verificados en la base de datos."""
    client = _client()
    prompt = (
        "Redacta, en español de Bolivia, material de primer contacto de Serrate Paz & Asociados (estudio jurídico) "
        "dirigido a la empresa descrita abajo. Tono profesional, ejecutivo, directo y sobrio; nada de lenguaje "
        "publicitario ni promesas. Usa SOLO los hechos provistos; no agregues datos, cifras ni nombres. Si no hay "
        "nombre de ejecutivo, dirígete al equipo directivo. Menciona al menos un hecho concreto (proyecto o noticia) "
        "con su fecha si la tiene. No menciones denuncias, conflictos ni litigios de la empresa (campo conflict_news) en "
        "los textos dirigidos al prospecto (email, linkedin, meeting); úsalos sólo en el argumento interno (pitch).\n\n"
        f"HECHOS:\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
        "Devuelve sólo un bloque ```json``` con: {\"email_subject\": str, \"email_body\": str (máx. 180 palabras), "
        "\"linkedin\": str (máx. 280 caracteres), \"meeting\": str (propuesta de reunión de 30 minutos, máx. 120 "
        "palabras), \"pitch\": str (argumento comercial interno para el abogado, 3-5 viñetas)}."
    )
    resp = _create(client, model=MODEL, max_tokens=8000, output_config={"effort": "medium"},
                   messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason == "refusal":
        raise ClaudeUnavailable("Claude rechazó la solicitud.")
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return parse_json_block(text)
