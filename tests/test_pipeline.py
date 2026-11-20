"""Investigación de punta a punta sobre la internet simulada."""
import json

from serrate_bi import quality as q
from serrate_bi import repo
from serrate_bi.research import pipeline


def _new(conn, name="Constructora Ejemplo Andina"):
    cid, _ = repo.create_company(conn, name, origin="manual", source_id=repo.manual_source(conn))
    return cid


def test_investigate_company_full(conn, web):
    cid = _new(conn)
    stats = pipeline.investigate_company(conn, cid)
    c = repo.get_company(conn, cid)
    st = repo.field_states(conn, "company", cid)
    # Sitio encontrado por búsqueda y luego verificado al leerlo.
    assert c["website"] == "https://ejemploandina.test"
    assert st["website"]["confidence"] == q.VERIFICADO
    # Correos publicados (se prioriza el del dominio); nunca imágenes.
    emails = {r["value"] for r in conn.execute("SELECT value FROM facts WHERE field='email' AND entity_type='company'")}
    assert {"ventas@ejemploandina.test", "info@ejemploandina.test"} <= emails
    assert not any("2x" in e for e in emails)
    assert c["phone"] in ("+591 33456789", "+591 71234567")
    phones = {r["value"] for r in conn.execute("SELECT value FROM facts WHERE field='phone'")}
    assert phones <= {"+591 33456789", "+591 71234567"}
    assert c["linkedin_url"] == "https://www.linkedin.com/company/ejemplo-andina-test"
    assert c["city"] == "Santa Cruz de la Sierra" and c["department"] == "Santa Cruz"
    assert c["sector"] in ("Construcción", "Desarrollo inmobiliario")
    # Ejecutivo explícito en el sitio oficial, con correo publicado junto a su nombre.
    execs = {e["full_name"]: e for e in repo.executives_for(conn, cid)}
    assert "Mariela Quispe Rojas" in execs
    m = execs["Mariela Quispe Rojas"]
    assert m["position"] == "Gerente General"
    assert m["email"] == "mquispe@ejemploandina.test"
    assert m["linkedin_url"] == "https://www.linkedin.com/in/mariela-quispe-test"
    assert repo.field_states(conn, "executive", m["id"])["full_name"]["confidence"] == q.VERIFICADO
    # Al gerente técnico no se le asigna ningún correo (no está publicado).
    assert execs["Pablo Soria Vaca"]["email"] is None
    # Proyectos y noticias, filtrando las que no mencionan a la empresa.
    pnames = {p["name"] for p in conn.execute("SELECT name FROM projects WHERE company_id=?", (cid,))}
    assert {"Torre Horizonte", "Condominio Las Palmeras"} <= pnames
    titles = [n["title"] for n in conn.execute("SELECT title FROM news WHERE company_id=?", (cid,))]
    assert len(titles) == 2 and not any("Otra empresa" in t for t in titles)
    cats = " ".join(n["categories"] or "" for n in conn.execute("SELECT categories FROM news"))
    assert "Conflicto / litigio" in cats and "Inversión" in cats
    # Estado y análisis.
    assert c["crm_status"] == "Investigado" and c["last_researched_at"]
    a = json.loads(c["analysis_json"])
    assert 1 <= a["score"] <= 100 and a["score"] >= 70
    assert any(n["area"] == "Litigios y arbitraje" for n in a["needs"])
    assert a["approach"]["specific"]
    assert stats["ejecutivos_nuevos"] >= 1
    # Cada dato vigente tiene fuente y fecha.
    for r in conn.execute("SELECT * FROM field_state"):
        assert r["source_id"] and r["verified_at"]


def test_reinvestigation_does_not_duplicate_and_logs_changes(conn, web):
    cid = _new(conn)
    pipeline.investigate_company(conn, cid, run_id=repo.create_run(conn, "investigar", cid))
    n_proj = conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"]
    n_news = conn.execute("SELECT COUNT(*) n FROM news").fetchone()["n"]
    n_exec = conn.execute("SELECT COUNT(*) n FROM executives").fetchone()["n"]
    # El sitio cambia de teléfono y se publica un proyecto nuevo.
    from tests.conftest import SITE
    web.pages[SITE + "/contacto"] = web.pages[SITE + "/contacto"].replace("(3) 3456789", "(3) 3999888").replace(
        "+591 71234567", "+591 76543210")
    web.pages[SITE + "/proyectos"] += "<h3>Edificio Mirador Sur</h3>"
    pipeline.investigate_company(conn, cid, run_id=repo.create_run(conn, "investigar", cid))
    assert conn.execute("SELECT COUNT(*) n FROM news").fetchone()["n"] == n_news
    assert conn.execute("SELECT COUNT(*) n FROM executives").fetchone()["n"] == n_exec
    assert conn.execute("SELECT COUNT(*) n FROM projects").fetchone()["n"] == n_proj + 1
    c = repo.get_company(conn, cid)
    assert c["phone"] in ("+591 33999888", "+591 76543210")
    ch = conn.execute("SELECT * FROM change_log WHERE field='phone' AND change_type='modificado'").fetchone()
    assert ch is not None and ch["old_value"] in ("+591 33456789", "+591 71234567")
    # El teléfono anterior no se pierde.
    assert conn.execute("SELECT COUNT(*) n FROM facts WHERE value='+591 33456789'").fetchone()["n"] >= 1


def test_no_data_invented_when_nothing_found(conn, web):
    cid = _new(conn, "Empresa Fantasma Inexistente")
    pipeline.investigate_company(conn, cid)
    c = repo.get_company(conn, cid)
    for f in ("website", "email", "phone", "linkedin_url", "city"):
        assert c[f] is None
    assert repo.executives_for(conn, cid) == []
    assert not json.loads(c["analysis_json"])["approach"]["specific"]
    missing = q.missing_fields(c, False)
    assert "Correo corporativo" in missing and "Gerente general / ejecutivo principal" in missing


def test_discovery_and_dedup(conn, web):
    stats = pipeline.discover(conn, cities=["Santa Cruz", "Cochabamba"], investigate_new=False)
    names = {r["name"] for r in conn.execute("SELECT name FROM companies")}
    assert "Desarrolladora Cumbre Test" in names
    assert any("Ejemplo Andina" in n for n in names)
    assert not any("Facebook" in n or "Receta" in n for n in names)
    cumbre = conn.execute("SELECT * FROM companies WHERE name='Desarrolladora Cumbre Test'").fetchone()
    assert cumbre["city"] == "Cochabamba"
    assert conn.execute("SELECT name FROM projects WHERE company_id=?", (cumbre["id"],)).fetchone()["name"] == \
        "Urbanización Valle Alto"
    total = conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"]
    stats2 = pipeline.discover(conn, cities=["Santa Cruz", "Cochabamba"], investigate_new=False)
    assert stats2["empresas_nuevas"] == 0 and stats2["empresas_existentes"] >= 2
    assert conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"] == total


def test_weekly_update(conn, web):
    pipeline.discover(conn, cities=["Santa Cruz"], investigate_new=True)
    stats = pipeline.weekly_update(conn, discover_new=False)
    assert "cambios_registrados" in stats
    from serrate_bi.db import get_setting
    assert get_setting(conn, "last_weekly_run")


def test_personal_email_not_stored_as_corporate(conn, web):
    cid = _new(conn)
    pipeline.investigate_company(conn, cid)
    corp = {r["value"] for r in conn.execute(
        "SELECT value FROM facts WHERE field='email' AND entity_type='company'")}
    assert "mquispe@ejemploandina.test" not in corp
    st = repo.field_states(conn, "company", cid)["website"]
    assert st["confidence"] == q.VERIFICADO
    src = conn.execute("SELECT source_type FROM sources WHERE id=?", (st["source_id"],)).fetchone()
    assert src["source_type"] == "sitio oficial"
