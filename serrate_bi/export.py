"""Exportación a Excel (una hoja por entidad, con fuentes, confianza y fechas)."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _sheet(wb, title, headers, rows):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [min(len(str(r[i - 1] or "")), 60) for r in rows[:300]]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(max(width, 10), 62)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def build_workbook(conn, company_ids=None):
    wb = Workbook()
    wb.remove(wb.active)
    where, args = "", []
    if company_ids is not None:
        ids = list(company_ids) or [-1]
        where = f" WHERE c.id IN ({','.join('?' * len(ids))})"
        args = ids

    def conf(entity, eid, field):
        r = conn.execute("SELECT fs.confidence, fs.verified_at, s.url, s.title FROM field_state fs "
                         "LEFT JOIN sources s ON s.id=fs.source_id WHERE entity_type=? AND entity_id=? AND field=?",
                         (entity, eid, field)).fetchone()
        return r

    companies = conn.execute("SELECT c.* FROM companies c" + where + " ORDER BY c.score DESC", args).fetchall()
    rows = []
    for c in companies:
        def fv(field):
            r = conf("company", c["id"], field)
            if not c[field]:
                return ["Pendiente", "no disponible", ""]
            return [c[field], r["confidence"] if r else "", (r["url"] or r["title"]) if r else ""]
        row = [c["id"], c["name"]]
        for f in ("activity", "sector", "city", "department", "website", "email", "phone", "linkedin_url"):
            row += fv(f)
        row += [c["score"], c["score_reason"], c["crm_status"], c["next_follow_up"], c["last_researched_at"],
                c["last_verified_at"], c["created_at"]]
        rows.append(row)
    headers = ["ID", "Empresa"]
    for label in ("Actividad", "Sector", "Ciudad", "Departamento", "Página web", "Correo corporativo",
                  "Teléfono", "LinkedIn empresa"):
        headers += [label, f"{label} · confianza", f"{label} · fuente"]
    headers += ["Puntuación", "Motivo de la puntuación", "Estado CRM", "Próximo seguimiento",
                "Última investigación", "Última verificación", "Fecha de alta"]
    _sheet(wb, "Empresas", headers, rows)

    ids = [c["id"] for c in companies] or [-1]
    ph = ",".join("?" * len(ids))
    execs = conn.execute(f"SELECT e.*, c.name AS company FROM executives e JOIN companies c ON c.id=e.company_id "
                         f"WHERE e.company_id IN ({ph}) ORDER BY c.name", ids).fetchall()
    erows = []
    for e in execs:
        row = [e["id"], e["company"]]
        for f in ("full_name", "position", "email", "phone", "linkedin_url"):
            r = conf("executive", e["id"], f)
            row += [e[f] or "Pendiente", r["confidence"] if r else "no disponible",
                    (r["url"] or r["title"]) if r else ""]
        row.append(e["last_verified_at"])
        erows.append(row)
    eh = ["ID", "Empresa"]
    for label in ("Nombre", "Cargo", "Correo profesional", "Teléfono", "LinkedIn"):
        eh += [label, f"{label} · confianza", f"{label} · fuente"]
    eh.append("Última verificación")
    _sheet(wb, "Ejecutivos", eh, erows)

    _sheet(wb, "Proyectos", ["Empresa", "Proyecto", "Tipo", "Ciudad", "Estado", "Descripción", "Confianza",
                             "Fuente", "Fecha publicación", "Detectado"],
           [tuple(r) for r in conn.execute(
               f"SELECT c.name, p.name, p.project_type, p.city, p.status, p.description, p.confidence, s.url, "
               f"p.published_at, p.first_seen_at FROM projects p JOIN companies c ON c.id=p.company_id "
               f"LEFT JOIN sources s ON s.id=p.source_id WHERE p.company_id IN ({ph}) ORDER BY c.name", ids)])
    _sheet(wb, "Noticias", ["Empresa", "Titular", "Medio", "Fecha", "Categorías", "URL", "Detectada"],
           [tuple(r) for r in conn.execute(
               f"SELECT c.name, n.title, n.publisher, n.published_at, n.categories, n.url, n.first_seen_at "
               f"FROM news n JOIN companies c ON c.id=n.company_id WHERE n.company_id IN ({ph}) "
               f"ORDER BY n.published_at DESC", ids)])
    _sheet(wb, "Actividad CRM", ["Empresa", "Tipo", "Detalle", "Fecha", "Reunión", "Seguimiento", "Hecho"],
           [tuple(r) for r in conn.execute(
               f"SELECT c.name, a.kind, a.content, a.activity_date, a.meeting_at, a.follow_up_date, "
               f"CASE a.done WHEN 1 THEN 'sí' ELSE 'no' END FROM activities a JOIN companies c ON c.id=a.company_id "
               f"WHERE a.company_id IN ({ph}) ORDER BY a.activity_date DESC", ids)])
    _sheet(wb, "Datos y fuentes", ["Empresa", "Entidad", "Campo", "Valor", "Confianza", "Origen", "Fuente",
                                   "Evidencia", "Fecha de consulta"],
           [tuple(r) for r in conn.execute(
               f"SELECT c.name, f.entity_type, f.field, f.value, f.confidence, f.origin, COALESCE(s.url, s.title), "
               f"f.evidence, f.observed_at FROM facts f JOIN companies c ON c.id=f.company_id "
               f"LEFT JOIN sources s ON s.id=f.source_id WHERE f.company_id IN ({ph}) ORDER BY c.name, f.observed_at",
               ids)])
    _sheet(wb, "Historial de cambios", ["Fecha", "Empresa", "Campo", "Tipo de cambio", "Valor anterior",
                                        "Valor nuevo", "Confianza anterior", "Confianza nueva", "Origen", "Fuente"],
           [tuple(r) for r in conn.execute(
               f"SELECT l.changed_at, c.name, l.field, l.change_type, l.old_value, l.new_value, l.old_confidence, "
               f"l.new_confidence, l.origin, COALESCE(s.url, s.title) FROM change_log l "
               f"JOIN companies c ON c.id=l.company_id LEFT JOIN sources s ON s.id=l.source_id "
               f"WHERE l.company_id IN ({ph}) ORDER BY l.changed_at DESC", ids)])
    _sheet(wb, "Fuentes", ["ID", "URL", "Título", "Medio / dominio", "Tipo", "Primera consulta", "Última consulta"],
           [tuple(r) for r in conn.execute(
               "SELECT id, url, title, publisher, source_type, first_retrieved_at, last_retrieved_at FROM sources "
               "ORDER BY last_retrieved_at DESC")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
