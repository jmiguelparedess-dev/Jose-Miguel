"""GENERAR CONTACTO: borradores personalizados (correo, LinkedIn, propuesta de reunión y argumento
comercial) basados exclusivamente en hechos registrados con fuente. NUNCA se envía nada: los borradores
quedan para revisión y aprobación del usuario."""
import json

from . import analysis
from . import quality as q
from .db import get_setting, now_iso
from .research import claude_engine

CHANNELS = {"email": "Correo electrónico", "linkedin": "Mensaje LinkedIn", "meeting": "Propuesta de reunión",
            "pitch": "Argumento comercial (uso interno)"}


class OutreachError(Exception):
    pass


def sender(conn):
    return {
        "name": get_setting(conn, "sender_name", "") or "[Nombre del abogado]",
        "position": get_setting(conn, "sender_position", "") or "[Cargo]",
        "email": get_setting(conn, "sender_email", "") or "[correo]",
        "phone": get_setting(conn, "sender_phone", "") or "[teléfono]",
        "firm": "Serrate Paz & Asociados",
    }


def build_basis(conn, cid, executive_id=None):
    c, execs, projects, news = analysis.load_context(conn, cid)
    a = json.loads(c["analysis_json"]) if c["analysis_json"] else analysis.analyze_and_store(conn, cid)
    if not a["approach"]["specific"]:
        raise OutreachError("No hay proyectos ni noticias documentadas sobre esta empresa: el mensaje sería "
                            "genérico. Use INVESTIGAR MÁS o registre un proyecto con su fuente antes de generar.")
    ex = None
    if executive_id:
        ex = next((e for e in execs if e["id"] == int(executive_id)), None)
    elif execs:
        top = [e for e in execs if e["position"] and any(
            r.lower() in e["position"].lower() for r in ("gerente general", "ceo", "propietario", "director",
                                                          "presidente", "socio", "fundador"))]
        ex = top[0] if top else execs[0]
    states = {r["field"]: r for r in conn.execute(
        "SELECT * FROM field_state WHERE entity_type='executive' AND entity_id=?", (ex["id"] if ex else -1,))}
    # Gancho para los mensajes EXTERNOS: un proyecto o noticia positiva/neutral. Las noticias de conflicto
    # nunca se citan ante el prospecto; sólo se usan en el argumento comercial interno.
    def is_conflict(n):
        return "Conflicto" in (n["categories"] or "")
    preferred = ("Proyecto", "Inversión", "Expansión", "Financiamiento", "Contratación pública")
    lead_news = next((n for n in news if not is_conflict(n) and any(c in (n["categories"] or "") for c in preferred)),
                     None) or next((n for n in news if not is_conflict(n)), None)
    lead_project = None
    if lead_news:
        lead_project = next((p for p in projects if p["name"].lower() in lead_news["title"].lower()), None)
    if not lead_project and projects:
        lead_project = next((p for p in projects if p["source_url"]), projects[0])
    conflict_news = next((n for n in news if is_conflict(n)), None)
    warnings = []
    if ex:
        conf = states.get("full_name")["confidence"] if states.get("full_name") else q.PROBABLE
        if conf != q.VERIFICADO:
            warnings.append(f"El nombre/cargo de {ex['full_name']} es PROBABLE: confírmelo antes de enviar.")
    else:
        warnings.append("No hay ejecutivo identificado: el mensaje se dirige al equipo directivo.")
    recipient = (ex["email"] if ex and ex["email"] else None) or c["email"]
    if not recipient:
        warnings.append("No hay correo publicado: el destinatario queda pendiente.")
    elif ex and not ex["email"]:
        warnings.append(f"Se usará el correo corporativo ({c['email']}); no hay correo profesional publicado del ejecutivo.")
    return {
        "company": {"name": c["name"], "city": c["city"], "sector": c["sector"], "activity": c["activity"],
                    "description": c["description"]},
        "executive": {"id": ex["id"], "name": ex["full_name"], "position": ex["position"],
                      "linkedin": ex["linkedin_url"]} if ex else None,
        "lead_news": {"title": lead_news["title"], "publisher": lead_news["publisher"],
                      "date": lead_news["published_at"], "url": lead_news["url"],
                      "categories": lead_news["categories"]} if lead_news else None,
        "lead_project": {"name": lead_project["name"], "type": lead_project["project_type"],
                         "city": lead_project["city"], "url": lead_project["source_url"]} if lead_project else None,
        "conflict_news": {"title": conflict_news["title"], "publisher": conflict_news["publisher"],
                          "date": conflict_news["published_at"], "url": conflict_news["url"]} if conflict_news else None,
        "needs": a["needs"][:3], "risks": a["risks"][:3], "approach": a["approach"]["text"],
        "recipient": recipient, "warnings": warnings,
    }


def _hook(b, short=False):
    n, p = b["lead_news"], b["lead_project"]
    if p and (not n or p["name"].lower() in n["title"].lower()):
        where = f" en {p['city']}" if p.get("city") else ""
        if n and not short:
            return (f"el proyecto {p['name']}{where}, a partir de la nota de {n['publisher'] or 'la prensa'}"
                    f"{' del ' + analysis._fmt_date(n['date']) if n['date'] else ''}")
        return f"el proyecto {p['name']}{where}"
    if n:
        if short:
            return f"la nota «{n['title']}»"
        return (f"la nota publicada por {n['publisher'] or 'la prensa'}"
                f"{' el ' + analysis._fmt_date(n['date']) if n['date'] else ''} sobre «{n['title']}»")
    if p:
        return f"el proyecto {p['name']}" + (f" en {p['city']}" if p.get("city") else "")
    return f"la actividad reciente de {b['company']['name']}"


def _focus(b):
    p = b["lead_project"]
    return f"el proyecto {p['name']}" if p else f"los proyectos en curso de {b['company']['name']}"


def _short(n):
    return n.get("short") or n["area"].lower()


def _ext_area(n):
    """Nombre del área para textos dirigidos al prospecto (sin connotación contenciosa)."""
    return {"Litigios y arbitraje": "Solución de controversias"}.get(n["area"], n["area"])


def generate_templates(b, s):
    c, ex = b["company"], b["executive"]
    needs = [n for n in b["needs"] if n["area"] != "Por determinar"] or b["needs"]
    main = needs[0]
    second = needs[1] if len(needs) > 1 else None
    greeting = f"Estimado/a {ex['name']}:" if ex else f"Estimado equipo directivo de {c['name']}:"
    sector = (c["sector"] or "construcción e inmobiliario").lower()
    first_name = ex["name"].split()[0] if ex else None

    topic = b["lead_project"]["name"] if b["lead_project"] else c["name"]
    email_subject = f"{topic}: {_ext_area(main).lower()}"
    email_body = (
        f"{greeting}\n\n"
        f"Le escribo como {s['position']} de {s['firm']}. Hemos seguido con interés {_hook(b)}.\n\n"
        f"En desarrollos de esta escala, buena parte de las contingencias se originan en {_short(main)}"
        + (f", y en {_short(second)}" if second else "") + ". "
        f"Nuestro equipo acompaña a empresas de {sector} precisamente en esos frentes, con un enfoque preventivo.\n\n"
        f"Me gustaría proponerle una conversación breve, de unos 30 minutos, para compartir observaciones concretas "
        f"sobre {_focus(b)} y conocer sus prioridades. ¿Tendría disponibilidad la próxima semana?\n\n"
        f"Atentamente,\n\n{s['name']}\n{s['position']}\n{s['firm']}\n{s['phone']} · {s['email']}"
    )

    li_greet = f"{first_name}, " if first_name else ""
    linkedin = (f"{li_greet}he seguido {_hook(b, short=True)}. En {s['firm']} asesoramos a empresas de {sector} en "
                f"{_ext_area(main).lower()}. ¿Le parece si conversamos 20 minutos?")
    if len(linkedin) > 300:
        linkedin = (f"{li_greet}he seguido la actividad reciente de {c['name']}. En {s['firm']} asesoramos a empresas "
                    f"de {sector} en {_ext_area(main).lower()}. ¿Conversamos 20 minutos?")

    city = c["city"] or "sus oficinas"
    agenda = "\n".join(f"  {i}. {_ext_area(n)}: {_short(n)}." for i, n in enumerate(needs[:3], 1))
    meeting = (
        f"Asunto: Propuesta de reunión — {s['firm']} / {c['name']}\n\n{greeting}\n\n"
        f"Le propongo una reunión de 30 minutos, presencial en {city} o virtual, para revisar {_focus(b)} "
        f"con la siguiente agenda:\n{agenda}\n  {len(needs[:3]) + 1}. Preguntas y prioridades de su equipo.\n\n"
        f"Podemos adaptarnos a su agenda; como referencia, martes o jueves de la próxima semana por la mañana. "
        f"No tiene costo ni compromiso.\n\n{s['name']}\n{s['firm']}"
    )

    pitch_lines = [f"• Por qué ahora: {b['approach']}"]
    if b.get("conflict_news"):
        cn = b["conflict_news"]
        pitch_lines.append(f"• Sensible (NO mencionar en el primer contacto): «{cn['title']}» "
                           f"({cn['publisher'] or 'prensa'}, {analysis._fmt_date(cn['date'])}).")
    pitch_lines += [f"• Necesidad: {n['area']} — {n['detail']}" + (f" [Evidencia: {n['evidence']}]" if n.get("evidence") else "")
                    for n in needs[:3]]
    if b["risks"]:
        pitch_lines.append("• Riesgos a abordar: " + " ".join(b["risks"][:3]))
    pitch_lines.append(f"• Servicio de entrada sugerido: revisión contractual preventiva de {_focus(b)}."
                       .replace(" de el ", " del "))
    if b["warnings"]:
        pitch_lines.append("• Antes de enviar: " + " ".join(b["warnings"]))
    return {
        "email": (email_subject, email_body),
        "linkedin": (None, linkedin),
        "meeting": (f"Propuesta de reunión — {c['name']}", meeting),
        "pitch": (f"Argumento comercial — {c['name']}", "\n".join(pitch_lines)),
    }


def generate(conn, cid, executive_id=None, use_claude=False):
    b = build_basis(conn, cid, executive_id)
    s = sender(conn)
    generator = "plantilla basada en hechos"
    texts = None
    if use_claude:
        if not claude_engine.available():
            raise OutreachError("La redacción con Claude requiere ANTHROPIC_API_KEY.")
        d = claude_engine.draft_outreach({**{k: v for k, v in b.items() if k != "warnings"}, "remitente": s})
        if d.get("email_body"):
            texts = {"email": (d.get("email_subject"), d["email_body"]), "linkedin": (None, d.get("linkedin", "")),
                     "meeting": (f"Propuesta de reunión — {b['company']['name']}", d.get("meeting", "")),
                     "pitch": (f"Argumento comercial — {b['company']['name']}", d.get("pitch", ""))}
            generator = "Claude (sobre hechos verificados)"
    if texts is None:
        texts = generate_templates(b, s)
    ids = []
    ts = now_iso()
    for channel, (subject, body) in texts.items():
        if not body:
            continue
        ids.append(conn.execute(
            "INSERT INTO outreach_drafts(company_id, executive_id, channel, subject, body, status, generator, "
            "basis_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, b["executive"]["id"] if b["executive"] else None, channel, subject, body, "borrador", generator,
             json.dumps(b, ensure_ascii=False), ts)).lastrowid)
    return ids, b


def set_draft_status(conn, draft_id, status):
    if status not in ("borrador", "aprobado", "descartado", "enviado manualmente"):
        raise ValueError("Estado inválido")
    col = {"aprobado": "approved_at", "enviado manualmente": "sent_at"}.get(status)
    if col:
        conn.execute(f"UPDATE outreach_drafts SET status=?, {col}=? WHERE id=?", (status, now_iso(), draft_id))
    else:
        conn.execute("UPDATE outreach_drafts SET status=? WHERE id=?", (status, draft_id))
