"""Acceso a datos. Toda escritura de un dato pasa por `record_fact`, que guarda la observación,
conserva el historial y decide (con reglas explícitas) si cambia el valor vigente."""
import json
import re

from . import quality as q
from .db import now_iso, transaction

COMPANY_COLUMNS = set(q.COMPANY_FIELDS)
EXECUTIVE_COLUMNS = {"full_name", "position", "email", "phone", "linkedin_url"}


# --------------------------------------------------------------------------- fuentes
def upsert_source(conn, url=None, title=None, source_type="web", publisher=None, snippet=None):
    ts = now_iso()
    if url:
        row = conn.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
        if row:
            conn.execute("UPDATE sources SET last_retrieved_at=?, title=COALESCE(title, ?), "
                         "snippet=COALESCE(?, snippet), publisher=COALESCE(publisher, ?) WHERE id=?",
                         (ts, title, snippet, publisher, row["id"]))
            return row["id"]
    cur = conn.execute(
        "INSERT INTO sources(url, title, publisher, source_type, snippet, first_retrieved_at, last_retrieved_at) "
        "VALUES(?,?,?,?,?,?,?)", (url, title, publisher, source_type, snippet, ts, ts))
    return cur.lastrowid


def manual_source(conn, note=None):
    return upsert_source(conn, url=None, title="Ingreso manual" + (f": {note}" if note else ""),
                         source_type="manual")


# --------------------------------------------------------------------------- empresas
def create_company(conn, name, origin="manual", source_id=None, confidence=q.PROBABLE, run_id=None, **fields):
    """Crea una empresa (o devuelve la existente si es duplicada). Devuelve (id, created: bool)."""
    name = q.clean_spaces(name)
    if not name:
        raise ValueError("El nombre de la empresa es obligatorio")
    dup = q.find_duplicate(conn, name, fields.get("website"))
    if dup:
        return dup["id"], False
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO companies(name, name_key, core_key, created_at, updated_at) VALUES(?,?,?,?,?)",
        (name, q.name_key(name), q.core_key(name), ts, ts))
    cid = cur.lastrowid
    record_fact(conn, "company", cid, cid, "name", name, confidence, source_id, origin, run_id=run_id)
    for f, v in fields.items():
        if v and f in COMPANY_COLUMNS:
            record_fact(conn, "company", cid, cid, f, v, confidence, source_id, origin, run_id=run_id)
    return cid, True


def get_company(conn, cid):
    return conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()


def list_companies(conn, city=None, department=None, sector=None, status=None, potential=None,
                   search=None, incomplete=None, sort="score", include_archived=False):
    sql = ["SELECT c.*, (SELECT COUNT(*) FROM executives e WHERE e.company_id=c.id) AS n_exec, "
           "(SELECT COUNT(*) FROM projects p WHERE p.company_id=c.id) AS n_projects, "
           "(SELECT COUNT(*) FROM news n WHERE n.company_id=c.id) AS n_news "
           "FROM companies c WHERE 1=1"]
    args = []
    if not include_archived:
        sql.append("AND c.archived=0")
    if city:
        sql.append("AND c.city=?"); args.append(city)
    if department:
        sql.append("AND c.department=?"); args.append(department)
    if sector:
        sql.append("AND c.sector=?"); args.append(sector)
    if status:
        sql.append("AND c.crm_status=?"); args.append(status)
    if potential == "alto":
        sql.append("AND c.score >= 70")
    elif potential == "medio":
        sql.append("AND c.score >= 40 AND c.score < 70")
    elif potential == "bajo":
        sql.append("AND c.score < 40")
    elif potential == "sin":
        sql.append("AND c.score IS NULL")
    if search:
        sql.append("AND (c.name LIKE ? OR c.activity LIKE ? OR c.description LIKE ?)")
        args += [f"%{search}%"] * 3
    if incomplete:
        sql.append("AND (" + " OR ".join(f"c.{f} IS NULL OR c.{f}=''" for f in q.KEY_COMPANY_FIELDS) +
                   " OR NOT EXISTS (SELECT 1 FROM executives e WHERE e.company_id=c.id))")
    order = {
        "score": "c.score IS NULL, c.score DESC, c.name",
        "name": "c.name COLLATE NOCASE",
        "recent": "c.last_researched_at IS NULL, c.last_researched_at DESC",
        "created": "c.created_at DESC",
        "follow_up": "c.next_follow_up IS NULL, c.next_follow_up",
    }.get(sort, "c.score IS NULL, c.score DESC")
    sql.append("ORDER BY " + order)
    return conn.execute(" ".join(sql), args).fetchall()


def touch_company(conn, cid, **cols):
    cols["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE companies SET {sets} WHERE id=?", (*cols.values(), cid))


# --------------------------------------------------------------------------- ejecutivos
def upsert_executive(conn, company_id, full_name, position=None, confidence=q.PROBABLE, source_id=None,
                     origin="investigación automática", evidence=None, run_id=None):
    full_name = q.clean_spaces(full_name)
    nk = q.strip_accents(full_name.lower())
    nk = re.sub(r"^(ing|arq|lic|dr|dra|sr|sra|msc|mba)\.?\s+", "", nk)
    row = conn.execute("SELECT id FROM executives WHERE company_id=? AND name_key=?", (company_id, nk)).fetchone()
    created = False
    if row:
        eid = row["id"]
    else:
        ts = now_iso()
        has_primary = conn.execute("SELECT 1 FROM executives WHERE company_id=? AND is_primary=1",
                                   (company_id,)).fetchone()
        eid = conn.execute(
            "INSERT INTO executives(company_id, full_name, name_key, is_primary, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)", (company_id, full_name, nk, 0 if has_primary else 1, ts, ts)).lastrowid
        created = True
    record_fact(conn, "executive", eid, company_id, "full_name", full_name, confidence, source_id, origin,
                evidence, run_id)
    if position:
        record_fact(conn, "executive", eid, company_id, "position", position, confidence, source_id, origin,
                    evidence, run_id)
    return eid, created


def executives_for(conn, company_id):
    return conn.execute("SELECT * FROM executives WHERE company_id=? ORDER BY is_primary DESC, id",
                        (company_id,)).fetchall()


# --------------------------------------------------------------------------- hechos / procedencia
def _source_domain(conn, source_id):
    if not source_id:
        return None
    row = conn.execute("SELECT url, source_type FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        return None
    if row["source_type"] == "manual":
        return "manual"
    return q.domain_of(row["url"]) if row["url"] else None


def _apply_state(conn, entity_type, entity_id, company_id, field, value, vkey, confidence, source_id, origin,
                 run_id=None):
    ts = now_iso()
    conn.execute(
        "INSERT INTO field_state(entity_type, entity_id, company_id, field, value, value_key, confidence, "
        "source_id, origin, run_id, verified_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(entity_type, entity_id, field) DO UPDATE SET value=excluded.value, "
        "value_key=excluded.value_key, confidence=excluded.confidence, source_id=excluded.source_id, "
        "origin=excluded.origin, run_id=excluded.run_id, verified_at=excluded.verified_at, "
        "updated_at=excluded.updated_at",
        (entity_type, entity_id, company_id, field, value, vkey, confidence, source_id, origin, run_id, ts, ts))
    table = "companies" if entity_type == "company" else "executives"
    cols = COMPANY_COLUMNS if entity_type == "company" else EXECUTIVE_COLUMNS
    if field in cols:
        extra = ""
        args = [value]
        if entity_type == "company" and field == "website":
            extra = ", domain=?"
            args.append(q.domain_of(value))
        if entity_type == "company" and field == "name":
            extra += ", name_key=?, core_key=?"
            args += [q.name_key(value), q.core_key(value)]
        conn.execute(f"UPDATE {table} SET {field}=?{extra}, updated_at=?, last_verified_at=? WHERE id=?",
                     (*args, ts, ts, entity_id))


def _log(conn, company_id, entity_type, entity_id, field, change_type, old, new, old_c, new_c,
         source_id, origin, run_id):
    conn.execute(
        "INSERT INTO change_log(company_id, entity_type, entity_id, field, change_type, old_value, new_value, "
        "old_confidence, new_confidence, source_id, origin, run_id, changed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, entity_type, entity_id, field, change_type, old, new, old_c, new_c, source_id, origin,
         run_id, now_iso()))


def record_fact(conn, entity_type, entity_id, company_id, field, value, confidence, source_id, origin,
                evidence=None, run_id=None, force=False):
    """Registra una observación y actualiza el valor vigente según estas reglas:

    1. Sin valor vigente -> se adopta el nuevo valor.
    2. Mismo valor -> se actualiza la fecha de verificación; si llega con más confianza, o si un dato
       'probable' es confirmado por una fuente de OTRO dominio, se eleva a 'verificado'.
    3. Valor distinto -> reemplaza si (a) tiene mayor confianza, o (b) proviene del MISMO dominio que
       respaldaba el valor vigente, con igual confianza, en una investigación posterior (la fuente se
       actualizó). Nunca reemplaza automáticamente un dato ingresado manualmente. En otro caso queda
       como valor alternativo visible en la ficha.
    `force=True` (edición manual o adopción explícita) reemplaza siempre.
    Devuelve 'nuevo', 'verificado', 'mejorado', 'reemplazado' o 'alternativo'.
    """
    if value is None:
        return None
    value = q.clean_spaces(str(value))
    if not value:
        return None
    if confidence not in (q.VERIFICADO, q.PROBABLE):
        raise ValueError(f"Confianza inválida: {confidence}")
    vkey = q.value_key(field, value)
    ts = now_iso()
    conn.execute(
        "INSERT INTO facts(entity_type, entity_id, company_id, field, value, value_key, confidence, source_id, "
        "origin, evidence, run_id, observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (entity_type, entity_id, company_id, field, value, vkey, confidence, source_id, origin,
         (evidence or "")[:1000] or None, run_id, ts))
    state = conn.execute("SELECT * FROM field_state WHERE entity_type=? AND entity_id=? AND field=?",
                         (entity_type, entity_id, field)).fetchone()
    if state is None:
        _apply_state(conn, entity_type, entity_id, company_id, field, value, vkey, confidence, source_id, origin,
                     run_id)
        _log(conn, company_id, entity_type, entity_id, field, "nuevo", None, value, None, confidence,
             source_id, origin, run_id)
        return "nuevo"
    if state["value_key"] == vkey and not force:
        new_conf = state["confidence"]
        result = "verificado"
        if q.CONFIDENCE_RANK[confidence] > q.CONFIDENCE_RANK[state["confidence"]]:
            new_conf, result = confidence, "mejorado"
        elif state["confidence"] == q.PROBABLE:
            d_old, d_new = _source_domain(conn, state["source_id"]), _source_domain(conn, source_id)
            if d_old and d_new and d_old != d_new:
                new_conf, result = q.VERIFICADO, "mejorado"
        # Si la nueva observación aporta más confianza, pasa a ser la fuente principal del dato.
        new_source = source_id if (result == "mejorado" and source_id) else state["source_id"]
        conn.execute("UPDATE field_state SET verified_at=?, confidence=?, source_id=?, run_id=COALESCE(?, run_id) "
                     "WHERE entity_type=? AND entity_id=? AND field=?",
                     (ts, new_conf, new_source, run_id, entity_type, entity_id, field))
        table = "companies" if entity_type == "company" else "executives"
        conn.execute(f"UPDATE {table} SET last_verified_at=? WHERE id=?", (ts, entity_id))
        if result == "mejorado":
            _log(conn, company_id, entity_type, entity_id, field, "confianza elevada", state["value"], value,
                 state["confidence"], new_conf, source_id, origin, run_id)
        return result
    higher = q.CONFIDENCE_RANK[confidence] > q.CONFIDENCE_RANK[state["confidence"]]
    same_source_update = (
        confidence == state["confidence"] and run_id is not None and state["run_id"] != run_id
        and _source_domain(conn, state["source_id"]) not in (None, "manual")
        and _source_domain(conn, state["source_id"]) == _source_domain(conn, source_id))
    replace = force or (state["origin"] != "manual" and (higher or same_source_update))
    if replace:
        _apply_state(conn, entity_type, entity_id, company_id, field, value, vkey, confidence, source_id, origin,
                     run_id)
        _log(conn, company_id, entity_type, entity_id, field, "modificado", state["value"], value,
             state["confidence"], confidence, source_id, origin, run_id)
        return "reemplazado"
    _log(conn, company_id, entity_type, entity_id, field, "valor alternativo", state["value"], value,
         state["confidence"], confidence, source_id, origin, run_id)
    return "alternativo"


def field_states(conn, entity_type, entity_id):
    rows = conn.execute(
        "SELECT fs.*, s.url AS source_url, s.title AS source_title, s.source_type, s.last_retrieved_at "
        "FROM field_state fs LEFT JOIN sources s ON s.id=fs.source_id WHERE fs.entity_type=? AND fs.entity_id=?",
        (entity_type, entity_id)).fetchall()
    return {r["field"]: r for r in rows}


def alternatives(conn, entity_type, entity_id):
    """Valores observados distintos del vigente, agrupados por campo (el más reciente de cada uno)."""
    rows = conn.execute(
        "SELECT f.*, s.url AS source_url, s.title AS source_title FROM facts f "
        "LEFT JOIN sources s ON s.id=f.source_id "
        "LEFT JOIN field_state fs ON fs.entity_type=f.entity_type AND fs.entity_id=f.entity_id AND fs.field=f.field "
        "WHERE f.entity_type=? AND f.entity_id=? AND (fs.value_key IS NULL OR f.value_key != fs.value_key) "
        "ORDER BY f.observed_at DESC", (entity_type, entity_id)).fetchall()
    out, seen = {}, set()
    for r in rows:
        k = (r["field"], r["value_key"])
        if k in seen:
            continue
        seen.add(k)
        out.setdefault(r["field"], []).append(r)
    return out


def manual_update(conn, entity_type, entity_id, company_id, field, value, confidence, note=None):
    """Edición manual del usuario. Si el valor queda vacío se elimina el valor vigente (se registra)."""
    value = q.clean_spaces(value or "")
    if not value:
        state = conn.execute("SELECT * FROM field_state WHERE entity_type=? AND entity_id=? AND field=?",
                             (entity_type, entity_id, field)).fetchone()
        if state:
            if entity_type == "company" and field == "name":
                raise ValueError("El nombre no puede quedar vacío")
            conn.execute("DELETE FROM field_state WHERE entity_type=? AND entity_id=? AND field=?",
                         (entity_type, entity_id, field))
            table = "companies" if entity_type == "company" else "executives"
            extra = ", domain=NULL" if (entity_type == "company" and field == "website") else ""
            conn.execute(f"UPDATE {table} SET {field}=NULL{extra}, updated_at=? WHERE id=?", (now_iso(), entity_id))
            _log(conn, company_id, entity_type, entity_id, field, "eliminado", state["value"], None,
                 state["confidence"], None, None, "manual", None)
        return "eliminado"
    sid = manual_source(conn, note)
    return record_fact(conn, entity_type, entity_id, company_id, field, value, confidence, sid, "manual",
                       evidence=note, force=True)


def adopt_fact(conn, fact_id):
    f = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    if not f:
        raise ValueError("Observación inexistente")
    record_fact(conn, f["entity_type"], f["entity_id"], f["company_id"], f["field"], f["value"],
                f["confidence"], f["source_id"], "manual", evidence="Valor adoptado manualmente", force=True)
    return f


# --------------------------------------------------------------------------- proyectos / noticias
def add_project(conn, company_id, name, project_type=None, city=None, description=None, status=None,
                confidence=q.PROBABLE, source_id=None, published_at=None):
    nk = q.name_key(name)
    if not nk:
        return None, False
    row = conn.execute("SELECT id FROM projects WHERE company_id=? AND name_key=?", (company_id, nk)).fetchone()
    if row:
        conn.execute("UPDATE projects SET description=COALESCE(description, ?), city=COALESCE(city, ?), "
                     "status=COALESCE(?, status), project_type=COALESCE(project_type, ?) WHERE id=?",
                     (description, city, status, project_type, row["id"]))
        return row["id"], False
    pid = conn.execute(
        "INSERT INTO projects(company_id, name, name_key, project_type, city, description, status, confidence, "
        "source_id, published_at, first_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (company_id, q.clean_spaces(name), nk, project_type, city, description, status, confidence, source_id,
         published_at, now_iso())).lastrowid
    _log(conn, company_id, "company", company_id, "proyecto", "nuevo proyecto", None, q.clean_spaces(name), None,
         confidence, source_id, "investigación automática", None)
    return pid, True


def add_news(conn, company_id, title, url, publisher=None, published_at=None, summary=None, categories=None,
             source_id=None):
    row = conn.execute("SELECT id FROM news WHERE company_id=? AND url=?", (company_id, url)).fetchone()
    if row:
        return row["id"], False
    # Evita duplicar la misma noticia publicada con otra URL (mismo titular).
    for r in conn.execute("SELECT id, title FROM news WHERE company_id=?", (company_id,)):
        if q.similar(q.name_key(r["title"]), q.name_key(title)) > 0.93:
            return r["id"], False
    nid = conn.execute(
        "INSERT INTO news(company_id, title, url, publisher, published_at, summary, categories, source_id, "
        "first_seen_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (company_id, q.clean_spaces(title), url, publisher, published_at, summary,
         ", ".join(categories or []) or None, source_id, now_iso())).lastrowid
    _log(conn, company_id, "company", company_id, "noticia", "nueva noticia", None, q.clean_spaces(title), None,
         None, source_id, "investigación automática", None)
    return nid, True


# --------------------------------------------------------------------------- CRM
def add_activity(conn, company_id, kind, content=None, activity_date=None, meeting_at=None,
                 follow_up_date=None, executive_id=None):
    aid = conn.execute(
        "INSERT INTO activities(company_id, executive_id, kind, content, activity_date, meeting_at, "
        "follow_up_date, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (company_id, executive_id, kind, content, activity_date or now_iso()[:10], meeting_at or None,
         follow_up_date or None, now_iso())).lastrowid
    refresh_follow_up(conn, company_id)
    return aid


def refresh_follow_up(conn, company_id):
    row = conn.execute("SELECT MIN(follow_up_date) AS d FROM activities WHERE company_id=? AND done=0 "
                       "AND follow_up_date IS NOT NULL", (company_id,)).fetchone()
    conn.execute("UPDATE companies SET next_follow_up=? WHERE id=?", (row["d"], company_id))


def set_status(conn, company_id, status, note=None):
    if status not in q.CRM_STATUSES:
        raise ValueError("Estado CRM inválido")
    c = get_company(conn, company_id)
    if c["crm_status"] == status:
        return
    with transaction(conn):
        touch_company(conn, company_id, crm_status=status)
        add_activity(conn, company_id, "Cambio de estado",
                     f"{c['crm_status']} → {status}" + (f". {note}" if note else ""))
        _log(conn, company_id, "company", company_id, "crm_status", "estado CRM", c["crm_status"], status,
             None, None, None, "manual", None)


# --------------------------------------------------------------------------- trabajos
def create_run(conn, kind, company_id=None, params=None):
    return conn.execute(
        "INSERT INTO research_runs(kind, company_id, params, status, created_at) VALUES(?,?,?,?,?)",
        (kind, company_id, json.dumps(params or {}, ensure_ascii=False), "en cola", now_iso())).lastrowid


def run_log(conn, run_id, message):
    conn.execute("UPDATE research_runs SET log = log || ? WHERE id=?", (f"[{now_iso()[11:19]}] {message}\n", run_id))
