"""Rutas web (interfaz y endpoints JSON para los trabajos en segundo plano)."""
import json
from datetime import date, datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)

from . import analysis, export, outreach
from . import quality as q
from . import repo
from .db import get_setting, now_iso, set_setting, transaction
from .research import claude_engine

bp = Blueprint("main", __name__)


def register_filters(app):
    @app.template_filter("fecha")
    def fecha(v, with_time=False):
        if not v:
            return "—"
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            return v
        return dt.strftime("%d/%m/%Y %H:%M" if with_time and "T" in v else "%d/%m/%Y")

    @app.template_filter("fromjson")
    def fromjson(v):
        return json.loads(v) if v else None

    @app.context_processor
    def globals_():
        return {"CRM_STATUSES": q.CRM_STATUSES, "SECTORS": q.SECTORS, "DEPARTMENTS": q.DEPARTMENTS,
                "COMPANY_FIELDS": q.COMPANY_FIELDS, "EXECUTIVE_FIELDS": q.EXECUTIVE_FIELDS,
                "CONFIDENCE_HELP": q.CONFIDENCE_HELP, "ACTIVITY_KINDS": q.ACTIVITY_KINDS,
                "CHANNELS": outreach.CHANNELS, "claude_available": claude_engine.available(),
                "today": date.today().isoformat()}


def _company_or_404(cid):
    c = repo.get_company(g.db, cid)
    if not c:
        abort(404)
    return c


# =========================================================================== DASHBOARD
@bp.route("/")
def dashboard():
    db = g.db
    total = db.execute("SELECT COUNT(*) n FROM companies WHERE archived=0").fetchone()["n"]
    by_city = db.execute("SELECT COALESCE(department,'Pendiente') k, COUNT(*) n FROM companies WHERE archived=0 "
                         "GROUP BY k ORDER BY n DESC").fetchall()
    by_sector = db.execute("SELECT COALESCE(sector,'Pendiente') k, COUNT(*) n FROM companies WHERE archived=0 "
                           "GROUP BY k ORDER BY n DESC").fetchall()
    by_status = {r["crm_status"]: r["n"] for r in db.execute(
        "SELECT crm_status, COUNT(*) n FROM companies WHERE archived=0 GROUP BY crm_status")}
    priority = db.execute("SELECT * FROM companies WHERE archived=0 AND score >= 70 AND crm_status NOT IN "
                          "('Cliente','Descartado') ORDER BY score DESC LIMIT 8").fetchall()
    pending_contact = db.execute("SELECT * FROM companies WHERE archived=0 AND crm_status IN "
                                 "('Pendiente de contacto','Investigado') ORDER BY score DESC LIMIT 8").fetchall()
    meetings = db.execute("SELECT * FROM companies WHERE archived=0 AND crm_status IN "
                          "('Reunión solicitada','Reunión confirmada') ORDER BY updated_at DESC LIMIT 8").fetchall()
    incomplete = repo.list_companies(db, incomplete=True, sort="score")
    recent = db.execute("SELECT * FROM companies WHERE archived=0 AND last_researched_at IS NOT NULL "
                        "ORDER BY last_researched_at DESC LIMIT 8").fetchall()
    followups = db.execute("SELECT a.*, c.name AS company FROM activities a JOIN companies c ON c.id=a.company_id "
                           "WHERE a.done=0 AND a.follow_up_date IS NOT NULL AND a.follow_up_date <= ? "
                           "ORDER BY a.follow_up_date LIMIT 10",
                           ((date.today() + timedelta(days=7)).isoformat(),)).fetchall()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
    changes_week = db.execute("SELECT COUNT(*) n FROM change_log WHERE changed_at >= ?", (week_ago,)).fetchone()["n"]
    news_recent = db.execute("SELECT n.*, c.name AS company FROM news n JOIN companies c ON c.id=n.company_id "
                             "ORDER BY n.first_seen_at DESC, n.published_at DESC LIMIT 6").fetchall()
    return render_template("dashboard.html", total=total, by_city=by_city, by_sector=by_sector, by_status=by_status,
                           priority=priority, pending_contact=pending_contact, meetings=meetings,
                           incomplete=incomplete[:8], n_incomplete=len(incomplete), recent=recent,
                           followups=followups, changes_week=changes_week, news_recent=news_recent,
                           last_weekly=get_setting(db, "last_weekly_run"))


# =========================================================================== EMPRESAS
def _filters():
    a = request.args
    return {k: a.get(k) or None for k in ("city", "department", "sector", "status", "potential", "search", "sort")} | {
        "incomplete": a.get("incomplete") == "1"}


@bp.route("/empresas")
def companies():
    f = _filters()
    rows = repo.list_companies(g.db, **{k: v for k, v in f.items() if k != "sort"}, sort=f["sort"] or "score")
    cities = [r["city"] for r in g.db.execute("SELECT DISTINCT city FROM companies WHERE city IS NOT NULL ORDER BY city")]
    return render_template("companies.html", rows=rows, f=f, cities=cities)


@bp.route("/empresas/nueva", methods=["GET", "POST"])
def company_new():
    if request.method == "POST":
        fm = request.form
        name = (fm.get("name") or "").strip()
        if not name:
            flash("El nombre es obligatorio.", "error")
            return redirect(url_for("main.company_new"))
        dup = q.find_duplicate(g.db, name, fm.get("website"))
        if dup:
            flash(f"La empresa ya está registrada como «{dup['name']}». No se creó un duplicado.", "warning")
            return redirect(url_for("main.company_detail", cid=dup["id"]))
        conf = fm.get("confidence") if fm.get("confidence") in (q.VERIFICADO, q.PROBABLE) else q.PROBABLE
        with transaction(g.db):
            sid = repo.manual_source(g.db, fm.get("source_note") or None)
            if fm.get("source_url"):
                sid = repo.upsert_source(g.db, fm["source_url"].strip(), fm.get("source_note") or None, "manual")
            fields = {k: fm.get(k, "").strip() for k in ("activity", "sector", "city", "website", "email", "phone",
                                                         "linkedin_url")}
            if fields.get("phone"):
                fields["phone"] = q.normalize_phone(fields["phone"]) or fields["phone"]
            if fields.get("city"):
                fields["department"] = q.CITY_TO_DEPARTMENT.get(fields["city"]) or fm.get("department") or None
            elif fm.get("department"):
                fields["department"] = fm["department"]
            cid, _ = repo.create_company(g.db, name, origin="manual", source_id=sid, confidence=conf, **fields)
        analysis.analyze_and_store(g.db, cid)
        if fm.get("investigate") == "1":
            current_app.jobs.submit("investigar", cid)
            flash("Empresa registrada. La investigación automática se está ejecutando.", "success")
        else:
            flash("Empresa registrada.", "success")
        return redirect(url_for("main.company_detail", cid=cid))
    return render_template("company_new.html", cities=list(q.CITY_TO_DEPARTMENT))


@bp.route("/empresas/<int:cid>")
def company_detail(cid):
    db = g.db
    c = _company_or_404(cid)
    execs = repo.executives_for(db, cid)
    exec_states = {e["id"]: repo.field_states(db, "executive", e["id"]) for e in execs}
    exec_alts = {e["id"]: repo.alternatives(db, "executive", e["id"]) for e in execs}
    states = repo.field_states(db, "company", cid)
    alts = repo.alternatives(db, "company", cid)
    projects = db.execute("SELECT p.*, s.url AS source_url, s.title AS source_title FROM projects p "
                          "LEFT JOIN sources s ON s.id=p.source_id WHERE company_id=? "
                          "ORDER BY COALESCE(published_at, first_seen_at) DESC", (cid,)).fetchall()
    news = db.execute("SELECT * FROM news WHERE company_id=? ORDER BY published_at IS NULL, published_at DESC",
                      (cid,)).fetchall()
    activities = db.execute("SELECT a.*, e.full_name FROM activities a LEFT JOIN executives e ON e.id=a.executive_id "
                            "WHERE a.company_id=? ORDER BY a.activity_date DESC, a.id DESC", (cid,)).fetchall()
    drafts = db.execute("SELECT d.*, e.full_name FROM outreach_drafts d LEFT JOIN executives e ON e.id=d.executive_id "
                        "WHERE d.company_id=? AND d.status != 'descartado' ORDER BY d.created_at DESC, d.id", (cid,)).fetchall()
    changes = db.execute("SELECT l.*, s.url AS source_url, s.title AS source_title FROM change_log l "
                         "LEFT JOIN sources s ON s.id=l.source_id WHERE l.company_id=? ORDER BY l.id DESC LIMIT 200",
                         (cid,)).fetchall()
    sources = db.execute("SELECT s.*, COUNT(f.id) AS n_facts, MAX(f.observed_at) AS last_used FROM facts f "
                         "JOIN sources s ON s.id=f.source_id WHERE f.company_id=? GROUP BY s.id "
                         "UNION SELECT s.*, 0, NULL FROM sources s WHERE s.id IN ("
                         "SELECT source_id FROM news WHERE company_id=? UNION SELECT source_id FROM projects WHERE company_id=?) "
                         "AND s.id NOT IN (SELECT source_id FROM facts WHERE company_id=? AND source_id IS NOT NULL) "
                         "ORDER BY last_retrieved_at DESC", (cid, cid, cid, cid)).fetchall()
    runs = db.execute("SELECT * FROM research_runs WHERE company_id=? ORDER BY id DESC LIMIT 5", (cid,)).fetchall()
    active_run = next((r for r in runs if r["status"] in ("en cola", "en curso")), None)
    a = json.loads(c["analysis_json"]) if c["analysis_json"] else None
    missing = q.missing_fields(c, bool(execs))
    return render_template("company_detail.html", c=c, execs=execs, exec_states=exec_states, exec_alts=exec_alts,
                           states=states, alts=alts, projects=projects, news=news, activities=activities,
                           drafts=drafts, changes=changes, sources=sources, runs=runs, active_run=active_run,
                           a=a, missing=missing)


@bp.post("/empresas/<int:cid>/campo")
def company_field(cid):
    _company_or_404(cid)
    fm = request.form
    entity_type = fm.get("entity_type", "company")
    entity_id = int(fm.get("entity_id") or cid)
    field = fm.get("field")
    allowed = q.COMPANY_FIELDS if entity_type == "company" else q.EXECUTIVE_FIELDS
    if field not in allowed:
        abort(400)
    if entity_type == "executive":
        ex = g.db.execute("SELECT * FROM executives WHERE id=? AND company_id=?", (entity_id, cid)).fetchone()
        if not ex:
            abort(404)
    value = fm.get("value", "")
    if field == "phone" and value.strip():
        value = q.normalize_phone(value) or value
    conf = fm.get("confidence") if fm.get("confidence") in (q.VERIFICADO, q.PROBABLE) else q.PROBABLE
    note = (fm.get("note") or "").strip() or None
    try:
        with transaction(g.db):
            if fm.get("source_url", "").strip():
                sid = repo.upsert_source(g.db, fm["source_url"].strip(), note, "manual")
                repo.record_fact(g.db, entity_type, entity_id, cid, field, value, conf, sid, "manual", note, force=True)
            else:
                repo.manual_update(g.db, entity_type, entity_id, cid, field, value, conf, note)
            if entity_type == "company" and field == "city" and value.strip() in q.CITY_TO_DEPARTMENT:
                sid = repo.manual_source(g.db, "Derivado de la ciudad")
                repo.record_fact(g.db, "company", cid, cid, "department", q.CITY_TO_DEPARTMENT[value.strip()],
                                 conf, sid, "manual", force=True)
        analysis.analyze_and_store(g.db, cid)
        flash(f"Dato actualizado: {allowed[field]}.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("main.company_detail", cid=cid) + "#datos")


@bp.post("/empresas/<int:cid>/adoptar/<int:fact_id>")
def adopt(cid, fact_id):
    f = g.db.execute("SELECT * FROM facts WHERE id=? AND company_id=?", (fact_id, cid)).fetchone()
    if not f:
        abort(404)
    with transaction(g.db):
        repo.adopt_fact(g.db, fact_id)
    analysis.analyze_and_store(g.db, cid)
    flash("Valor alternativo adoptado como vigente. El anterior queda en el historial.", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#datos")


@bp.post("/empresas/<int:cid>/ejecutivos")
def executive_add(cid):
    _company_or_404(cid)
    fm = request.form
    name = (fm.get("full_name") or "").strip()
    if not name:
        flash("El nombre del ejecutivo es obligatorio.", "error")
        return redirect(url_for("main.company_detail", cid=cid) + "#ejecutivos")
    conf = fm.get("confidence") if fm.get("confidence") in (q.VERIFICADO, q.PROBABLE) else q.PROBABLE
    note = fm.get("note") or None
    with transaction(g.db):
        sid = repo.upsert_source(g.db, fm["source_url"].strip(), note, "manual") if fm.get("source_url", "").strip() \
            else repo.manual_source(g.db, note)
        eid, _ = repo.upsert_executive(g.db, cid, name, fm.get("position") or None, conf, sid, "manual", note)
        for f in ("email", "phone", "linkedin_url"):
            v = (fm.get(f) or "").strip()
            if f == "phone" and v:
                v = q.normalize_phone(v) or v
            if v:
                repo.record_fact(g.db, "executive", eid, cid, f, v, conf, sid, "manual", note, force=True)
    analysis.analyze_and_store(g.db, cid)
    flash("Ejecutivo registrado.", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#ejecutivos")


@bp.post("/empresas/<int:cid>/ejecutivos/<int:eid>/eliminar")
def executive_delete(cid, eid):
    ex = g.db.execute("SELECT * FROM executives WHERE id=? AND company_id=?", (eid, cid)).fetchone()
    if not ex:
        abort(404)
    with transaction(g.db):
        repo._log(g.db, cid, "executive", eid, "ejecutivo", "ejecutivo descartado", ex["full_name"], None, None, None,
                  None, "manual", None)
        g.db.execute("DELETE FROM field_state WHERE entity_type='executive' AND entity_id=?", (eid,))
        g.db.execute("DELETE FROM executives WHERE id=?", (eid,))
    analysis.analyze_and_store(g.db, cid)
    flash(f"Ejecutivo «{ex['full_name']}» descartado (queda registrado en el historial).", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#ejecutivos")


@bp.post("/empresas/<int:cid>/proyectos")
def project_add(cid):
    _company_or_404(cid)
    fm = request.form
    if not (fm.get("name") or "").strip() or not (fm.get("source_url") or "").strip():
        flash("El proyecto requiere nombre y URL de la fuente.", "error")
        return redirect(url_for("main.company_detail", cid=cid) + "#proyectos")
    conf = fm.get("confidence") if fm.get("confidence") in (q.VERIFICADO, q.PROBABLE) else q.PROBABLE
    with transaction(g.db):
        sid = repo.upsert_source(g.db, fm["source_url"].strip(), fm.get("name"), "manual")
        repo.add_project(g.db, cid, fm["name"], fm.get("project_type") or None, fm.get("city") or None,
                         fm.get("description") or None, fm.get("status") or None, conf, sid,
                         fm.get("published_at") or None)
    analysis.analyze_and_store(g.db, cid)
    flash("Proyecto registrado.", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#proyectos")


@bp.post("/empresas/<int:cid>/estado")
def company_status(cid):
    _company_or_404(cid)
    try:
        repo.set_status(g.db, cid, request.form.get("status"), request.form.get("note") or None)
        flash("Estado actualizado.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(request.form.get("next") or url_for("main.company_detail", cid=cid) + "#crm")


@bp.post("/empresas/<int:cid>/actividad")
def activity_add(cid):
    _company_or_404(cid)
    fm = request.form
    kind = fm.get("kind") if fm.get("kind") in q.ACTIVITY_KINDS else "Comentario"
    with transaction(g.db):
        repo.add_activity(g.db, cid, kind, fm.get("content") or None, fm.get("activity_date") or None,
                          fm.get("meeting_at") or None, fm.get("follow_up_date") or None,
                          int(fm["executive_id"]) if fm.get("executive_id") else None)
    flash("Actividad registrada.", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#crm")


@bp.post("/actividades/<int:aid>/hecho")
def activity_done(aid):
    a = g.db.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        abort(404)
    g.db.execute("UPDATE activities SET done=1-done WHERE id=?", (aid,))
    repo.refresh_follow_up(g.db, a["company_id"])
    return redirect(request.form.get("next") or url_for("main.company_detail", cid=a["company_id"]) + "#crm")


@bp.post("/empresas/<int:cid>/investigar")
def company_investigate(cid):
    _company_or_404(cid)
    run_id = current_app.jobs.submit("investigar", cid)
    return jsonify({"run_id": run_id})


@bp.post("/empresas/<int:cid>/analizar")
def company_analyze(cid):
    _company_or_404(cid)
    analysis.analyze_and_store(g.db, cid)
    flash("Análisis comercial recalculado.", "success")
    return redirect(url_for("main.company_detail", cid=cid) + "#analisis")


@bp.post("/empresas/<int:cid>/contacto")
def company_outreach(cid):
    _company_or_404(cid)
    try:
        ids, basis = outreach.generate(g.db, cid, request.form.get("executive_id") or None,
                                       request.form.get("use_claude") == "1")
        flash(f"Se generaron {len(ids)} borradores. Revíselos: nada se envía sin su aprobación.", "success")
        for w in basis["warnings"]:
            flash(w, "warning")
    except outreach.OutreachError as e:
        flash(str(e), "error")
    except Exception as e:  # p. ej. error de la API de Claude
        flash(f"No se pudo generar con Claude: {e}", "error")
    return redirect(url_for("main.company_detail", cid=cid) + "#contacto")


@bp.post("/borradores/<int:did>")
def draft_update(did):
    d = g.db.execute("SELECT * FROM outreach_drafts WHERE id=?", (did,)).fetchone()
    if not d:
        abort(404)
    fm = request.form
    if "body" in fm:
        g.db.execute("UPDATE outreach_drafts SET body=?, subject=? WHERE id=?", (fm["body"], fm.get("subject"), did))
    if fm.get("status"):
        outreach.set_draft_status(g.db, did, fm["status"])
        if fm["status"] == "enviado manualmente":
            kind = {"email": "Correo enviado", "linkedin": "Mensaje LinkedIn"}.get(d["channel"], "Comentario")
            with transaction(g.db):
                repo.add_activity(g.db, d["company_id"], kind,
                                  f"Enviado manualmente por el usuario: {d['subject'] or outreach.CHANNELS[d['channel']]}",
                                  executive_id=d["executive_id"])
            c = repo.get_company(g.db, d["company_id"])
            if c["crm_status"] in ("Nuevo prospecto", "Investigado", "Pendiente de contacto"):
                repo.set_status(g.db, d["company_id"], "Contactado", "Registrado al marcar un borrador como enviado")
    flash("Borrador actualizado.", "success")
    return redirect(url_for("main.company_detail", cid=d["company_id"]) + "#contacto")


@bp.post("/empresas/<int:cid>/archivar")
def company_archive(cid):
    c = _company_or_404(cid)
    g.db.execute("UPDATE companies SET archived=1-archived, updated_at=? WHERE id=?", (now_iso(), cid))
    flash("Empresa archivada." if not c["archived"] else "Empresa restaurada.", "success")
    return redirect(url_for("main.companies") if not c["archived"] else url_for("main.company_detail", cid=cid))


# =========================================================================== INVESTIGACIÓN
@bp.route("/investigacion")
def research():
    runs = g.db.execute("SELECT r.*, c.name AS company FROM research_runs r LEFT JOIN companies c ON c.id=r.company_id "
                        "ORDER BY r.id DESC LIMIT 40").fetchall()
    return render_template("research.html", runs=runs, engine=get_setting(g.db, "research_engine", "auto"))


@bp.post("/investigacion/descubrir")
def research_discover():
    fm = request.form
    cities = fm.getlist("cities") or ["Santa Cruz", "La Paz", "Cochabamba"]
    extra = [c.strip() for c in (fm.get("other_cities") or "").split(",") if c.strip()]
    sectors = [s for s in fm.getlist("sectors") if s in q.SECTORS] or q.SECTORS
    run_id = current_app.jobs.submit("descubrir", params={
        "cities": cities + extra, "sectors": sectors, "extra_query": fm.get("extra_query") or None,
        "investigate_new": fm.get("investigate_new") == "1", "max_new": int(fm.get("max_new") or 10)})
    return jsonify({"run_id": run_id})


@bp.route("/trabajos/<int:run_id>")
def run_status(run_id):
    r = g.db.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        abort(404)
    return jsonify({k: r[k] for k in r.keys()})


# =========================================================================== CRM
@bp.route("/crm")
def crm():
    rows = g.db.execute("SELECT * FROM companies WHERE archived=0 ORDER BY score DESC").fetchall()
    columns = {s: [r for r in rows if r["crm_status"] == s] for s in q.CRM_STATUSES}
    followups = g.db.execute("SELECT a.*, c.name AS company FROM activities a JOIN companies c ON c.id=a.company_id "
                             "WHERE a.done=0 AND a.follow_up_date IS NOT NULL ORDER BY a.follow_up_date").fetchall()
    meetings = g.db.execute("SELECT a.*, c.name AS company FROM activities a JOIN companies c ON c.id=a.company_id "
                            "WHERE a.meeting_at IS NOT NULL AND a.meeting_at >= ? ORDER BY a.meeting_at",
                            (date.today().isoformat(),)).fetchall()
    return render_template("crm.html", columns=columns, followups=followups, meetings=meetings)


# =========================================================================== ACTUALIZACIONES
@bp.route("/actualizaciones")
def updates():
    changes = g.db.execute("SELECT l.*, c.name AS company, s.url AS source_url FROM change_log l "
                           "JOIN companies c ON c.id=l.company_id LEFT JOIN sources s ON s.id=l.source_id "
                           "WHERE l.change_type != 'nuevo' OR l.field IN ('proyecto','noticia') "
                           "ORDER BY l.id DESC LIMIT 300").fetchall()
    weekly_runs = g.db.execute("SELECT * FROM research_runs WHERE kind='semanal' ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("updates.html", changes=changes, weekly_runs=weekly_runs,
                           weekly_enabled=get_setting(g.db, "weekly_enabled", "1") == "1",
                           last_weekly=get_setting(g.db, "last_weekly_run"))


@bp.post("/actualizaciones/ejecutar")
def updates_run():
    run_id = current_app.jobs.submit("semanal", params={"discover_new": request.form.get("discover_new") == "1"})
    return jsonify({"run_id": run_id})


# =========================================================================== CONFIGURACIÓN
@bp.route("/configuracion", methods=["GET", "POST"])
def settings():
    keys = ["sender_name", "sender_position", "sender_email", "sender_phone", "research_engine", "weekly_enabled"]
    if request.method == "POST":
        for k in keys:
            v = request.form.get(k)
            if k == "weekly_enabled":
                v = "1" if v == "1" else "0"
            if k == "research_engine" and v not in ("auto", "web", "claude"):
                v = "auto"
            set_setting(g.db, k, (v or "").strip())
        flash("Configuración guardada.", "success")
        return redirect(url_for("main.settings"))
    import os
    vals = {k: get_setting(g.db, k, "") for k in keys}
    vals["weekly_enabled"] = vals["weekly_enabled"] or "1"
    vals["research_engine"] = vals["research_engine"] or "auto"
    env = {"ANTHROPIC_API_KEY": claude_engine.available(), "BRAVE_API_KEY": bool(os.environ.get("BRAVE_API_KEY")),
           "GOOGLE_CSE": bool(os.environ.get("GOOGLE_CSE_KEY") and os.environ.get("GOOGLE_CSE_CX"))}
    counts = {t: g.db.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t in
              ("companies", "executives", "projects", "news", "sources", "facts", "activities", "change_log")}
    return render_template("settings.html", vals=vals, env=env, counts=counts, db_path=current_app.config["DB_PATH"],
                           model=claude_engine.MODEL)


# =========================================================================== EXPORTAR
@bp.route("/exportar.xlsx")
def export_xlsx():
    f = _filters()
    ids = None
    if any(v for k, v in f.items() if k != "sort"):
        ids = [r["id"] for r in repo.list_companies(g.db, **{k: v for k, v in f.items() if k != "sort"})]
    buf = export.build_workbook(g.db, ids)
    name = f"serrate_paz_prospectos_{date.today().isoformat()}.xlsx"
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
