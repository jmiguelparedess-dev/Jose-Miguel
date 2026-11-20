"""Análisis comercial explicable: puntuación 1-100 por componentes, necesidades jurídicas, riesgos y motivo
concreto de acercamiento. Cada conclusión cita la evidencia (proyecto/noticia) que la sustenta."""
import json
from datetime import date, datetime

from . import quality as q
from .db import now_iso

SECTOR_POINTS = {"Desarrollo inmobiliario": 25, "Construcción": 23, "Infraestructura": 22,
                 "Promoción inmobiliaria": 20}
CATEGORY_POINTS = {"Conflicto / litigio": 8, "Contratación pública": 6, "Financiamiento": 5, "Inversión": 5,
                   "Expansión": 4, "Regulatorio": 3, "Proyecto": 2}


def _days_ago(d):
    if not d:
        return None
    try:
        return (date.today() - datetime.fromisoformat(d[:10]).date()).days
    except ValueError:
        return None


def _fmt_date(d):
    if not d:
        return "fecha no indicada"
    try:
        dt = datetime.fromisoformat(d[:10]).date()
    except ValueError:
        return d
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
             "noviembre", "diciembre"]
    return f"{dt.day} de {meses[dt.month - 1]} de {dt.year}"


def load_context(conn, cid):
    c = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
    execs = conn.execute("SELECT * FROM executives WHERE company_id=? ORDER BY is_primary DESC, id", (cid,)).fetchall()
    projects = conn.execute("SELECT p.*, s.url AS source_url FROM projects p LEFT JOIN sources s ON s.id=p.source_id "
                            "WHERE company_id=? ORDER BY COALESCE(published_at, first_seen_at) DESC", (cid,)).fetchall()
    news = conn.execute("SELECT * FROM news WHERE company_id=? ORDER BY published_at IS NULL, published_at DESC",
                        (cid,)).fetchall()
    return c, execs, projects, news


def analyze(conn, cid):
    c, execs, projects, news = load_context(conn, cid)
    components = []

    sector = c["sector"]
    pts = SECTOR_POINTS.get(sector, 8)
    components.append(("Afinidad sectorial", pts, 25,
                       f"Sector {sector}" if sector else "Sector no confirmado (pendiente)"))

    n_proj = len(projects)
    pts = 0 if n_proj == 0 else 8 if n_proj == 1 else 12 if n_proj == 2 else 16 if n_proj <= 4 else 20
    components.append(("Proyectos identificados", pts, 20, f"{n_proj} proyecto(s) documentado(s)"))

    recent = [n for n in news if (_days_ago(n["published_at"]) or 9999) <= 365]
    undated = [n for n in news if not n["published_at"]]
    weight = len(recent) + 0.5 * len(undated)
    pts = 0 if weight == 0 else 6 if weight < 2 else 10 if weight < 4 else 15
    components.append(("Actividad reciente", pts, 15,
                       f"{len(recent)} noticia(s) en los últimos 12 meses" + (f", {len(undated)} sin fecha" if undated else "")))

    cats = {}
    for n in news:
        for cat in (n["categories"] or "").split(", "):
            if cat:
                cats.setdefault(cat, []).append(n)
    pts = sum(CATEGORY_POINTS.get(cat, 0) for cat in cats)
    ptypes = {(p["project_type"] or "").lower() for p in projects}
    if ptypes & {"urbanización", "condominio", "barrio cerrado"}:
        pts += 3
    pts = min(pts, 20)
    components.append(("Detonantes de necesidad jurídica", pts, 20,
                       ", ".join(sorted(cats)) if cats else "Sin detonantes documentados"))

    dept = c["department"]
    pts = 10 if dept in q.PRIORITY_DEPARTMENTS else 6 if dept else 3
    components.append(("Ubicación", pts, 10, dept or "Departamento pendiente"))

    pts = 0
    detail = []
    if execs:
        pts += 4; detail.append("ejecutivo identificado")
        if any(e["email"] or e["linkedin_url"] for e in execs):
            pts += 2; detail.append("canal directo al ejecutivo")
    if c["email"]:
        pts += 2; detail.append("correo corporativo")
    if c["phone"]:
        pts += 2; detail.append("teléfono")
    components.append(("Posibilidad de contacto", pts, 10, ", ".join(detail) or "Sin datos de contacto"))

    score = max(1, min(100, sum(p for _, p, _, _ in components)))

    needs, risks = _needs_and_risks(c, projects, news, cats)
    approach = _approach_reason(c, execs, projects, news, cats, needs)
    level = "Alto" if score >= 70 else "Medio" if score >= 40 else "Bajo"
    reason = (f"Potencial {level} ({score}/100). " +
              "; ".join(f"{name}: {p}/{mx} ({d})" for name, p, mx, d in components) + ".")
    return {
        "score": score, "level": level, "reason": reason,
        "components": [{"name": n, "points": p, "max": m, "detail": d} for n, p, m, d in components],
        "needs": needs, "risks": risks, "approach": approach, "analyzed_at": now_iso(),
        "evidence_count": len(projects) + len(news),
    }


def _ev(item, kind):
    if item is None:
        return None
    if kind == "news":
        return f"«{item['title']}» ({item['publisher'] or 'prensa'}, {_fmt_date(item['published_at'])})"
    return f"Proyecto «{item['name']}»" + (f" en {item['city']}" if item["city"] else "")


def _needs_and_risks(c, projects, news, cats):
    sector = c["sector"] or ""
    needs, risks = [], []
    first_proj = projects[0] if projects else None
    ptypes = " ".join((p["project_type"] or "") + " " + p["name"] for p in projects).lower()

    if sector in ("Construcción", "Infraestructura") or projects:
        needs.append({"area": "Contratos de construcción y obra", "short": "contratos de obra con subcontratistas y proveedores (plazos, garantías, penalidades y recepción)",
                      "detail": "Redacción y revisión de contratos con comitentes, subcontratistas y proveedores: "
                                "precio y reajustes, plazos, garantías, penalidades, recepción y responsabilidad por vicios.",
                      "evidence": _ev(first_proj, "project")})
    if sector in ("Desarrollo inmobiliario", "Promoción inmobiliaria") or any(
            w in ptypes for w in ("edificio", "torre", "condominio", "residencial")):
        needs.append({"area": "Desarrollo inmobiliario y compraventa", "short": "contratos de preventa y compraventa, propiedad horizontal y saneamiento del derecho propietario",
                      "detail": "Contratos de preventa y compraventa, régimen de propiedad horizontal, due diligence "
                                "del derecho propietario e inscripción en Derechos Reales.",
                      "evidence": _ev(first_proj, "project")})
        risks.append("Reclamos de compradores por retrasos de entrega o diferencias con lo ofertado en preventa.")
    if any(w in ptypes for w in ("urbanización", "loteamiento", "barrio cerrado")):
        needs.append({"area": "Saneamiento de tierras y derecho propietario", "short": "verificación de títulos, aprobación de urbanizaciones y prevención de avasallamientos",
                      "detail": "Verificación de títulos, aprobación municipal de urbanizaciones y defensa ante "
                                "avasallamientos o dobles ventas.",
                      "evidence": _ev(next((p for p in projects if "urbaniz" in (p["name"] + (p["project_type"] or "")).lower()), first_proj), "project")})
        risks.append("Conflictos de derecho propietario, límites o avasallamientos en terrenos en desarrollo.")
    if "Contratación pública" in cats or sector == "Infraestructura":
        n = cats.get("Contratación pública", [None])[0]
        needs.append({"area": "Contratación pública", "short": "contratos con entidades públicas (órdenes de cambio, boletas de garantía y reclamos de pago)",
                      "detail": "Procesos bajo las Normas Básicas del SABS (D.S. 0181): impugnaciones, órdenes de cambio, "
                                "boletas de garantía, reclamos de pago y resolución de contratos con entidades públicas.",
                      "evidence": _ev(n, "news")})
        risks.append("Retrasos de pago de entidades públicas y ejecución de boletas de garantía.")
    if "Conflicto / litigio" in cats:
        n = cats["Conflicto / litigio"][0]
        needs.append({"area": "Litigios y arbitraje", "short": "gestión de reclamos y solución de controversias",
                      "detail": "Defensa o reclamación en sede judicial o arbitral (Ley 708 de Conciliación y Arbitraje) "
                                "y estrategia de gestión del conflicto.",
                      "evidence": _ev(n, "news")})
        risks.append("Contingencia litigiosa o reputacional ya visible en prensa.")
    elif projects or sector:
        needs.append({"area": "Arbitraje y prevención de conflictos", "short": "cláusulas de solución de controversias y prevención de reclamos",
                      "detail": "Cláusulas de solución de controversias (conciliación y arbitraje, Ley 708), protocolos "
                                "de reclamos y auditoría contractual preventiva de los proyectos en curso.",
                      "evidence": _ev(first_proj, "project")})
    if {"Financiamiento", "Expansión", "Inversión"} & set(cats):
        key = next(k for k in ("Financiamiento", "Expansión", "Inversión") if k in cats)
        needs.append({"area": "Estructuración societaria y financiera", "short": "estructuración de inversiones, consorcios, fideicomisos y acuerdos entre socios",
                      "detail": "Vehículos de inversión, consorcios y joint ventures, fideicomisos, garantías y "
                                "acuerdos entre socios.",
                      "evidence": _ev(cats[key][0], "news")})
        risks.append("Obligaciones con financiadores y socios (garantías, covenants, acuerdos de accionistas).")
    if len(projects) >= 3:
        risks.append("Varios proyectos simultáneos: concentración de riesgo contractual con subcontratistas y proveedores.")
    if not needs:
        needs.append({"area": "Por determinar",
                      "detail": "No hay suficiente información documentada. Usar INVESTIGAR MÁS.", "evidence": None})
    return needs, risks


def _approach_reason(c, execs, projects, news, cats, needs):
    lead = None
    for cat in ("Conflicto / litigio", "Contratación pública", "Financiamiento", "Inversión", "Expansión", "Proyecto"):
        if cat in cats:
            lead = ("news", cats[cat][0], cat)
            break
    if lead is None and news:
        lead = ("news", news[0], None)
    if lead is None and projects:
        lead = ("project", projects[0], None)
    top_need = needs[0]["area"] if needs else "asesoramiento jurídico"
    if lead is None:
        return {"text": f"No hay proyectos ni noticias documentadas sobre {c['name']}. El acercamiento se basaría "
                        f"sólo en su sector; se recomienda INVESTIGAR MÁS antes de contactar.",
                "specific": False, "evidence_url": None}
    kind, item, cat = lead
    if kind == "news":
        text = (f"{_ev(item, 'news')}. " +
                {"Conflicto / litigio": "La empresa enfrenta una situación contenciosa visible: oportunidad para "
                                        "ofrecer defensa y estrategia de resolución.",
                 "Contratación pública": "La contratación con el Estado implica riesgos de pago, garantías y "
                                         "resolución contractual donde el estudio puede acompañar.",
                 "Financiamiento": "Una operación de financiamiento requiere estructuración y revisión de garantías.",
                 "Inversión": "Una nueva inversión suele requerir estructuración, contratos de obra y compraventa.",
                 "Expansión": "La expansión o alianza requiere estructuración societaria y acuerdos entre socios.",
                 "Proyecto": "Un proyecto en marcha genera contratos de obra, preventa y compraventa que conviene blindar."
                 }.get(cat, "La actividad reciente abre necesidades en " + top_need.lower() + "."))
        return {"text": text, "specific": True, "evidence_url": item["url"]}
    text = (f"{_ev(item, 'project')}: un proyecto en desarrollo concentra contratos de obra, preventa y compraventa, "
            f"y posibles controversias; Serrate Paz puede aportar en {top_need.lower()}.")
    return {"text": text, "specific": True, "evidence_url": item["source_url"]}


def analyze_and_store(conn, cid):
    a = analyze(conn, cid)
    conn.execute("UPDATE companies SET score=?, score_reason=?, analysis_json=?, analyzed_at=? WHERE id=?",
                 (a["score"], a["reason"], json.dumps(a, ensure_ascii=False), a["analyzed_at"], cid))
    return a
