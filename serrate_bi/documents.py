"""Carga de documentos (Excel/CSV, PDF, fotos, texto) para actualizar la información de prospectos y clientes.

Principios:
  * El archivo original se conserva y queda registrado como FUENTE de cada dato que aporte.
  * Sólo se proponen datos que aparecen literalmente en el documento (mismos extractores que la investigación web).
  * Nada se guarda en las fichas sin la confirmación del usuario (vista previa + selección).
  * Fotos y PDF escaneados: el texto se obtiene por OCR (Claude con ANTHROPIC_API_KEY, o Tesseract si está
    instalado). El OCR sólo transcribe; la extracción de datos la hacen reglas deterministas sobre ese texto.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil

from . import quality as q
from . import repo
from .db import now_iso
from .research import claude_engine, extract

MAX_BYTES = 25 * 1024 * 1024
KINDS = {
    "tabla": {".xlsx", ".xlsm", ".csv"},
    "pdf": {".pdf"},
    "imagen": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
    "texto": {".txt"},
}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12"}
EMPTY_VALUES = {"", "pendiente", "no disponible", "-", "—", "n/a", "na", "s/d", "sd", "none", "null", "nan", "0"}

# Campo destino -> encabezados reconocidos (normalizados sin acentos).
COLUMN_SYNONYMS = {
    "name": ["empresa", "nombre de la empresa", "nombre empresa", "razon social", "cliente", "compania", "company",
             "nombre comercial", "prospecto"],
    "activity": ["actividad", "giro", "rubro", "actividad economica"],
    "sector": ["sector"],
    "city": ["ciudad", "localidad", "municipio"],
    "department": ["departamento", "depto"],
    "website": ["web", "pagina web", "sitio web", "website", "url", "sitio"],
    "email": ["correo", "email", "e mail", "correo electronico", "correo corporativo", "mail"],
    "phone": ["telefono", "tel", "telf", "celular", "phone", "movil", "telefono corporativo"],
    "linkedin_url": ["linkedin", "linkedin empresa", "linkedin de la empresa"],
    "exec_name": ["gerente", "gerente general", "contacto", "nombre contacto", "nombre del contacto", "representante",
                  "representante legal", "ceo", "propietario", "ejecutivo", "nombre"],
    "exec_position": ["cargo", "puesto", "posicion", "cargo del contacto"],
    "exec_email": ["correo contacto", "email contacto", "correo del contacto", "correo gerente", "correo profesional"],
    "exec_phone": ["telefono contacto", "celular contacto", "telefono del contacto", "celular gerente"],
    "exec_linkedin": ["linkedin contacto", "linkedin gerente", "linkedin ejecutivo", "linkedin del ejecutivo"],
    "notes": ["notas", "nota", "observaciones", "comentarios", "comentario"],
}
TARGET_LABELS = {
    "": "— no importar —", "name": "Empresa", "activity": "Actividad", "sector": "Sector", "city": "Ciudad",
    "department": "Departamento", "website": "Página web", "email": "Correo corporativo",
    "phone": "Teléfono corporativo", "linkedin_url": "LinkedIn empresa", "exec_name": "Ejecutivo: nombre",
    "exec_position": "Ejecutivo: cargo", "exec_email": "Ejecutivo: correo", "exec_phone": "Ejecutivo: teléfono",
    "exec_linkedin": "Ejecutivo: LinkedIn", "notes": "Notas (se guardan en el CRM)",
}


class DocumentError(Exception):
    pass


def uploads_dir(db_path):
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def kind_for(filename):
    ext = os.path.splitext(filename.lower())[1]
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind, ext
    if ext == ".xls":
        raise DocumentError("Formato .xls antiguo: abra el archivo en Excel y guárdelo como .xlsx.")
    raise DocumentError("Formato no admitido. Use Excel (.xlsx), CSV, PDF, foto (JPG/PNG) o texto (.txt).")


def _safe_name(name):
    name = os.path.basename(name or "documento")
    return re.sub(r"[^\w.\- ()áéíóúñÁÉÍÓÚÑ]", "_", name)[:150] or "documento"


def save_upload(conn, db_path, filename, data: bytes, company_id=None):
    if not data:
        raise DocumentError("El archivo está vacío.")
    if len(data) > MAX_BYTES:
        raise DocumentError("El archivo supera el máximo de 25 MB.")
    kind, ext = kind_for(filename)
    sha = hashlib.sha256(data).hexdigest()
    dup = conn.execute("SELECT id FROM documents WHERE sha256=? AND COALESCE(company_id,0)=?",
                       (sha, company_id or 0)).fetchone()
    if dup:
        return dup["id"], False
    stored = f"{sha[:24]}{ext}"
    with open(os.path.join(uploads_dir(db_path), stored), "wb") as fh:
        fh.write(data)
    safe = _safe_name(filename)
    sid = repo.upsert_source(conn, None, f"Documento: {safe}", "documento")
    did = conn.execute(
        "INSERT INTO documents(company_id, filename, stored_name, kind, mime, size, sha256, status, source_id, "
        "uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (company_id, safe, stored, kind, MIME.get(ext), len(data), sha, "cargado", sid, now_iso())).lastrowid
    return did, True


def file_path(db_path, doc):
    return os.path.join(uploads_dir(db_path), doc["stored_name"])


# =========================================================================== extracción
def _ocr_tesseract(path):
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None
    if not shutil.which("tesseract"):
        return None
    try:
        return pytesseract.image_to_string(Image.open(path), lang="spa+eng")
    except Exception:
        return pytesseract.image_to_string(Image.open(path))


def ocr_available():
    return claude_engine.available() or bool(shutil.which("tesseract"))


def process(conn, db_path, doc_id):
    """Extrae el contenido del documento (texto o tabla). No modifica fichas."""
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    path = file_path(db_path, doc)
    text, table, method, message, status = None, None, None, None, "procesado"
    ext = os.path.splitext(doc["stored_name"])[1]
    try:
        if doc["kind"] == "tabla":
            table = read_table(path, ext)
            method = "lectura de hoja de cálculo"
            if not table["sheets"]:
                status, message = "sin datos", "No se encontró una fila de encabezados reconocible (p. ej. «Empresa», «Correo», «Teléfono»)."
        elif doc["kind"] == "texto":
            with open(path, "rb") as fh:
                raw = fh.read()
            text = raw.decode("utf-8", errors="replace")
            method = "texto"
        elif doc["kind"] == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:60])
            method = "texto del PDF"
            if len(text.strip()) < 40:
                if claude_engine.available():
                    with open(path, "rb") as fh:
                        text = claude_engine.transcribe(fh.read(), "application/pdf")
                    method = "OCR (Claude)"
                else:
                    status = "requiere OCR"
                    message = ("PDF escaneado (sin texto). Configure ANTHROPIC_API_KEY para leerlo automáticamente o "
                               "registre los datos manualmente; el archivo queda guardado como respaldo.")
        elif doc["kind"] == "imagen":
            if claude_engine.available():
                with open(path, "rb") as fh:
                    text = claude_engine.transcribe(fh.read(), doc["mime"] or "image/jpeg")
                method = "OCR (Claude)"
            else:
                text = _ocr_tesseract(path)
                method = "OCR (Tesseract)" if text else None
                if not text:
                    status = "requiere OCR"
                    message = ("Para leer fotos automáticamente configure ANTHROPIC_API_KEY (o instale Tesseract). "
                               "El archivo queda guardado y puede registrar los datos manualmente.")
    except DocumentError as e:
        status, message = "error", str(e)
    except Exception as e:  # archivo dañado, etc.
        status, message = "error", f"No se pudo leer el archivo: {e.__class__.__name__}: {e}"
    if text is not None and status == "procesado" and not text.strip():
        status, message = "sin datos", "El documento no contiene texto legible."
    conn.execute("UPDATE documents SET status=?, extraction_method=?, extracted_text=?, table_json=?, message=?, "
                 "processed_at=? WHERE id=?",
                 (status, method, (text or None) and text[:200000], json.dumps(table, ensure_ascii=False) if table else None,
                  message, now_iso(), doc_id))
    return status


def _norm_header(h):
    return re.sub(r"[^a-z0-9 ]", " ", q.strip_accents(str(h or "").lower())).strip()


def map_header(h):
    n = re.sub(r"\s+", " ", _norm_header(h))
    if not n or "confianza" in n or "fuente" in n or n in ("id",):
        return ""
    for field, syns in COLUMN_SYNONYMS.items():
        if n in syns:
            return field
    best, best_len = "", 0
    for field, syns in COLUMN_SYNONYMS.items():
        for s_ in syns:
            if len(s_) >= 4 and re.search(rf"(?<![a-z]){re.escape(s_)}(?![a-z])", n) and len(s_) > best_len:
                best, best_len = field, len(s_)
    return best


def _rows_from_file(path, ext):
    if ext == ".csv":
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "latin-1"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(txt[:5000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        yield "CSV", list(csv.reader(io.StringIO(txt), dialect))
        return
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if v is None else str(v).strip() for v in r])
            if len(rows) > 5000:
                break
        yield ws.title, rows


def read_table(path, ext):
    sheets = []
    for title, rows in _rows_from_file(path, ext):
        header_idx = None
        for i, r in enumerate(rows[:15]):
            mapped = [map_header(c) for c in r]
            if sum(1 for m in mapped if m) >= 2 and "name" in mapped:
                header_idx = i
                break
        if header_idx is None:
            continue
        headers = rows[header_idx]
        data = [r for r in rows[header_idx + 1:] if any(str(c).strip() for c in r)]
        mapping = []
        used = set()
        for h in headers:
            m = map_header(h)
            if m in used and m not in ("notes",):
                m = ""
            used.add(m)
            mapping.append(m)
        sheets.append({"title": title, "headers": headers, "mapping": mapping, "rows": data})
    return {"sheets": sheets}


def _val(v):
    v = q.clean_spaces(str(v or ""))
    return "" if v.lower() in EMPTY_VALUES else v


def _clean_field(field, v):
    v = _val(v)
    if not v:
        return None
    if field in ("email", "exec_email"):
        v = v.lower().replace("mailto:", "")
        return v if extract.EMAIL_RE.fullmatch(v) else None
    if field in ("phone", "exec_phone"):
        return q.normalize_phone(v.split("/")[0].split(",")[0])
    if field == "website":
        d = q.domain_of(v)
        return f"https://{d}" if d and "." in d and not q.is_excluded_domain(d) else None
    if field == "linkedin_url":
        return v if "linkedin.com/company" in v.lower() else None
    if field == "exec_linkedin":
        return v if "linkedin.com/in" in v.lower() else None
    if field == "sector":
        for s in q.SECTORS:
            if q.strip_accents(s.lower()) == q.strip_accents(v.lower()):
                return s
        return q.classify_sector(v)
    if field == "city":
        city, _ = q.detect_city(v)
        return city or v.title()
    return v


def preview_table(conn, sheet, mapping):
    """Para cada fila: empresa existente o nueva, y qué datos aportaría."""
    out = []
    for r in sheet["rows"]:
        row = {mapping[i]: r[i] for i in range(min(len(r), len(mapping))) if mapping[i]}
        name = _val(row.get("name"))
        if not name:
            out.append({"name": None, "status": "sin nombre (se omite)", "fields": {}})
            continue
        dup = q.find_duplicate(conn, name, _clean_field("website", row.get("website")) if row.get("website") else None)
        fields = {}
        for f, v in row.items():
            if f in ("name",):
                continue
            cv = v if f == "notes" else _clean_field(f, v)
            if cv:
                fields[f] = _val(cv) if f == "notes" else cv
        out.append({"name": name, "existing_id": dup["id"] if dup else None,
                    "existing_name": dup["name"] if dup else None,
                    "status": f"actualiza «{dup['name']}»" if dup else "nueva empresa", "fields": fields})
    return out


def import_table(conn, doc, sheet, mapping, confidence, create_new=True):
    from . import analysis
    sid = doc["source_id"]
    origin = "documento"
    stats = {"empresas_nuevas": 0, "empresas_actualizadas": 0, "datos_nuevos": 0, "datos_reconfirmados": 0,
             "datos_modificados": 0, "valores_alternativos": 0, "ejecutivos": 0, "notas": 0, "filas_omitidas": 0}
    touched = set()
    key = {"nuevo": "datos_nuevos", "verificado": "datos_reconfirmados", "mejorado": "datos_reconfirmados",
           "reemplazado": "datos_modificados", "alternativo": "valores_alternativos"}
    for item in preview_table(conn, sheet, mapping):
        if not item["name"] or (not item["existing_id"] and not create_new):
            stats["filas_omitidas"] += 1
            continue
        f = item["fields"]
        if item["existing_id"]:
            cid = item["existing_id"]
            stats["empresas_actualizadas"] += 1
        else:
            cid, created = repo.create_company(conn, item["name"], origin=origin, source_id=sid, confidence=confidence)
            stats["empresas_nuevas" if created else "empresas_actualizadas"] += 1
        touched.add(cid)
        for field in ("activity", "sector", "city", "department", "website", "email", "phone", "linkedin_url"):
            if f.get(field):
                r = repo.record_fact(conn, "company", cid, cid, field, f[field], confidence, sid, origin,
                                     f"Importado de {doc['filename']}")
                stats[key.get(r, "datos_nuevos")] += 1
        if f.get("city") and not f.get("department") and f["city"] in q.CITY_TO_DEPARTMENT:
            repo.record_fact(conn, "company", cid, cid, "department", q.CITY_TO_DEPARTMENT[f["city"]], confidence,
                             sid, origin, "Derivado de la ciudad")
        if f.get("exec_name") and extract.valid_person_name(re.sub(r"^(Ing|Arq|Lic|Dr|Dra|Sr|Sra)\.?\s+", "", f["exec_name"])):
            eid, created = repo.upsert_executive(conn, cid, f["exec_name"], f.get("exec_position"), confidence, sid,
                                                 origin, f"Importado de {doc['filename']}")
            stats["ejecutivos"] += int(created)
            for src, dst in (("exec_email", "email"), ("exec_phone", "phone"), ("exec_linkedin", "linkedin_url")):
                if f.get(src):
                    r = repo.record_fact(conn, "executive", eid, cid, dst, f[src], confidence, sid, origin,
                                         f"Importado de {doc['filename']}")
                    stats[key.get(r, "datos_nuevos")] += 1
        if f.get("notes"):
            repo.add_activity(conn, cid, "Comentario", f"Nota importada de «{doc['filename']}»: {f['notes']}")
            stats["notas"] += 1
    for cid in touched:
        analysis.analyze_and_store(conn, cid)
    return stats


# =========================================================================== propuestas desde texto
URL_RE = re.compile(r"\b((?:https?://)?(?:www\.)[a-z0-9\-]+(?:\.[a-z0-9\-]+)+|https?://[a-z0-9\-]+(?:\.[a-z0-9\-]+)+)", re.I)


def _line_of(text, needle):
    for ln in text.splitlines():
        if needle.lower() in ln.lower():
            return q.clean_spaces(ln)[:200]
    return ""


def proposals_from_text(text, company=None):
    """Lista de datos propuestos (cada uno con su evidencia literal). El usuario elige cuáles aplicar."""
    if not text:
        return []
    props = []
    execs = extract.extract_executives(text)
    personal = {}
    for m in execs:
        props.append({"target": "executive", "field": "full_name", "value": m.name, "position": m.position,
                      "label": f"Ejecutivo: {m.name} — {m.position}", "evidence": m.evidence[:200]})
        for e in extract.emails_near(text, m.name):
            personal[e] = m.name
            props.append({"target": "executive", "field": "email", "value": e, "exec_name": m.name,
                          "label": f"Correo de {m.name}", "evidence": _line_of(text, e)})
    for e in extract.extract_emails(text):
        if e not in personal:
            props.append({"target": "company", "field": "email", "value": e, "label": "Correo corporativo",
                          "evidence": _line_of(text, e)})
    for p in extract.extract_phones(text):
        digits = p[-8:]
        props.append({"target": "company", "field": "phone", "value": p, "label": "Teléfono",
                      "evidence": next((q.clean_spaces(ln) for ln in text.splitlines()
                                        if digits[-7:] in re.sub(r"\D", "", ln)), "")[:200]})
    li_c, li_p = extract.extract_linkedin(text)
    for u in li_c[:1]:
        props.append({"target": "company", "field": "linkedin_url", "value": u, "label": "LinkedIn empresa",
                      "evidence": _line_of(text, u)})
    for u in li_p:
        owner = next((m.name for m in execs if all(t in u.lower() for t in q.strip_accents(m.name.lower()).split()[:2])), None)
        if owner:
            props.append({"target": "executive", "field": "linkedin_url", "value": u, "exec_name": owner,
                          "label": f"LinkedIn de {owner}", "evidence": _line_of(text, u)})
    for m in URL_RE.findall(text):
        d = q.domain_of(m)
        if d and not q.is_excluded_domain(d) and not d.endswith("linkedin.com"):
            props.append({"target": "company", "field": "website", "value": f"https://{d}", "label": "Página web",
                          "evidence": _line_of(text, m)})
            break
    for name, kind in extract.extract_projects(text)[:15]:
        props.append({"target": "project", "field": "project", "value": name, "project_type": kind,
                      "label": f"Proyecto ({kind})", "evidence": _line_of(text, name)})
    city, dept = q.detect_city(text)
    if city:
        props.append({"target": "company", "field": "city", "value": city, "label": "Ciudad",
                      "evidence": _line_of(text, city.split()[0])})
    seen, out = set(), []
    for p in props:
        k = (p["target"], p["field"], p["value"].lower(), p.get("exec_name"))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def suggest_companies(conn, text):
    """Empresas registradas cuyo nombre aparece en el texto, y nombres de empresa detectados."""
    t = q.name_key(text or "")
    found = []
    for r in conn.execute("SELECT id, name, core_key FROM companies WHERE archived=0"):
        if r["core_key"] and len(r["core_key"]) >= 4 and r["core_key"] in t:
            found.append(r)
    return found, extract.extract_company_names(text or "")


def apply_proposals(conn, doc, company_id, selected, confidence):
    from . import analysis
    sid, origin = doc["source_id"], "documento"
    ev = f"Tomado del documento «{doc['filename']}»"
    exec_ids = {}
    n = 0
    for p in selected:
        if p["target"] == "executive" and p["field"] == "full_name":
            eid, _ = repo.upsert_executive(conn, company_id, p["value"], p.get("position"), confidence, sid, origin,
                                           p.get("evidence") or ev)
            exec_ids[p["value"]] = eid
            n += 1
    for p in selected:
        if p["target"] == "company":
            repo.record_fact(conn, "company", company_id, company_id, p["field"], p["value"], confidence, sid, origin,
                             p.get("evidence") or ev)
            if p["field"] == "city" and p["value"] in q.CITY_TO_DEPARTMENT:
                repo.record_fact(conn, "company", company_id, company_id, "department",
                                 q.CITY_TO_DEPARTMENT[p["value"]], confidence, sid, origin, "Derivado de la ciudad")
            n += 1
        elif p["target"] == "executive" and p["field"] != "full_name":
            eid = exec_ids.get(p.get("exec_name"))
            if not eid:
                eid, _ = repo.upsert_executive(conn, company_id, p["exec_name"], None, confidence, sid, origin, ev)
            repo.record_fact(conn, "executive", eid, company_id, p["field"], p["value"], confidence, sid, origin,
                             p.get("evidence") or ev)
            n += 1
        elif p["target"] == "project":
            repo.add_project(conn, company_id, p["value"], p.get("project_type"), None, p.get("evidence"), None,
                             confidence, sid)
            n += 1
    conn.execute("UPDATE documents SET company_id=COALESCE(company_id, ?), applied_at=?, applied_summary=? WHERE id=?",
                 (company_id, now_iso(), f"{n} dato(s) incorporados", doc["id"]))
    analysis.analyze_and_store(conn, company_id)
    return n


def image_b64(data):
    return base64.standard_b64encode(data).decode()
