"""Generación de contactos, CRM, interfaz web, trabajos y exportación."""
import io
import re

import pytest
from openpyxl import load_workbook

from serrate_bi import outreach, repo
from serrate_bi.db import set_setting
from serrate_bi.research import pipeline


def _investigated(conn):
    cid, _ = repo.create_company(conn, "Constructora Ejemplo Andina", source_id=repo.manual_source(conn))
    pipeline.investigate_company(conn, cid)
    return cid


def test_outreach_refuses_generic(conn):
    cid, _ = repo.create_company(conn, "Constructora Sin Datos")
    from serrate_bi import analysis
    analysis.analyze_and_store(conn, cid)
    with pytest.raises(outreach.OutreachError):
        outreach.generate(conn, cid)
    assert conn.execute("SELECT COUNT(*) n FROM outreach_drafts").fetchone()["n"] == 0


def test_outreach_personalized_drafts(conn, web):
    cid = _investigated(conn)
    set_setting(conn, "sender_name", "Abogado de Prueba")
    ids, basis = outreach.generate(conn, cid)
    drafts = {d["channel"]: d for d in conn.execute("SELECT * FROM outreach_drafts WHERE company_id=?", (cid,))}
    assert set(drafts) == {"email", "linkedin", "meeting", "pitch"}
    email = drafts["email"]["body"]
    assert "Mariela Quispe Rojas" in email
    assert "Ejemplo Andina" in email or "Torre Horizonte" in email or "condominio" in email.lower()
    assert "Abogado de Prueba" in email
    assert len(drafts["linkedin"]["body"]) <= 300
    assert all(d["status"] == "borrador" for d in drafts.values())
    assert basis["recipient"] == "mquispe@ejemploandina.test"
    # El argumento comercial cita evidencia real registrada.
    titles = [n["title"] for n in conn.execute("SELECT title FROM news")]
    assert any(t in drafts["pitch"]["body"] for t in titles) or "Torre Horizonte" in drafts["pitch"]["body"]


def test_web_flow(client, app, conn, web):
    # Alta manual, sin investigación automática.
    r = client.post("/empresas/nueva", data={"name": "Constructora Ejemplo Andina", "city": "Santa Cruz de la Sierra"})
    assert r.status_code == 302
    cid = int(re.search(r"/empresas/(\d+)", r.headers["Location"]).group(1))
    # Duplicado detectado.
    r = client.post("/empresas/nueva", data={"name": "CONSTRUCTORA EJEMPLO ANDINA S.R.L."}, follow_redirects=True)
    assert "ya está registrada" in r.get_data(as_text=True)
    assert conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"] == 1
    # INVESTIGAR MÁS -> trabajo en cola; se ejecuta de forma síncrona en la prueba.
    run_id = client.post(f"/empresas/{cid}/investigar").get_json()["run_id"]
    app.jobs.run_now(run_id)
    run = client.get(f"/trabajos/{run_id}").get_json()
    assert run["status"] == "terminado", run["log"]
    page = client.get(f"/empresas/{cid}").get_data(as_text=True)
    for txt in ("Mariela Quispe Rojas", "Torre Horizonte", "Verificado", "INVESTIGAR MÁS", "GENERAR CONTACTO"):
        assert txt in page
    # Edición manual con historial.
    client.post(f"/empresas/{cid}/campo", data={"field": "phone", "value": "33111222", "confidence": "verificado",
                                                "note": "Confirmado por teléfono"})
    assert repo.get_company(conn, cid)["phone"] == "+591 33111222"
    assert conn.execute("SELECT COUNT(*) n FROM change_log WHERE field='phone' AND origin='manual'").fetchone()["n"] == 1
    # Generar contacto, aprobar y marcar como enviado (registro manual, nunca automático).
    client.post(f"/empresas/{cid}/contacto", data={})
    d = conn.execute("SELECT * FROM outreach_drafts WHERE channel='email'").fetchone()
    client.post(f"/borradores/{d['id']}", data={"status": "aprobado"})
    page = client.get(f"/empresas/{cid}").get_data(as_text=True)
    assert "mailto:mquispe@ejemploandina.test" in page
    client.post(f"/borradores/{d['id']}", data={"status": "enviado manualmente"})
    c = repo.get_company(conn, cid)
    assert c["crm_status"] == "Contactado"
    # CRM: estado, actividad con seguimiento y reunión.
    client.post(f"/empresas/{cid}/estado", data={"status": "Reunión solicitada"})
    client.post(f"/empresas/{cid}/actividad", data={"kind": "Llamada", "content": "Pidió propuesta",
                                                    "follow_up_date": "2030-01-15", "meeting_at": "2030-01-20T10:00"})
    assert repo.get_company(conn, cid)["next_follow_up"] == "2030-01-15"
    for url in ("/", "/crm", "/empresas?potential=alto", "/empresas?incomplete=1", "/actualizaciones",
                "/investigacion", "/configuracion"):
        assert client.get(url).status_code == 200, url
    dash = client.get("/").get_data(as_text=True)
    assert "Constructora Ejemplo Andina" in dash
    # Exportación a Excel con hojas separadas.
    r = client.get("/exportar.xlsx")
    wb = load_workbook(io.BytesIO(r.data))
    assert {"Empresas", "Ejecutivos", "Proyectos", "Noticias", "Actividad CRM", "Datos y fuentes",
            "Historial de cambios", "Fuentes"} <= set(wb.sheetnames)
    rows = list(wb["Empresas"].values)
    assert rows[1][1] == "Constructora Ejemplo Andina"
    assert "Pendiente" in [str(v) for v in rows[1]] or rows[1][2]  # campos faltantes se marcan
    assert wb["Datos y fuentes"].max_row > 5


def test_discovery_endpoint_and_weekly_scheduler(client, app, conn, web):
    run_id = client.post("/investigacion/descubrir", data={"cities": ["Cochabamba"], "sectors": ["Desarrollo inmobiliario"]}).get_json()["run_id"]
    app.jobs.run_now(run_id)
    assert client.get(f"/trabajos/{run_id}").get_json()["status"] == "terminado"
    assert conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"] >= 1
    # El programador semanal encola una actualización cuando corresponde, y no la duplica.
    r1 = app.jobs.check_weekly()
    r2 = app.jobs.check_weekly()
    assert r1 and r1 == r2
    app.jobs.run_now(r1)
    assert app.jobs.check_weekly() is None  # ya se ejecutó esta semana


def test_persistence_across_restarts(tmp_path, web):
    from serrate_bi import create_app
    from serrate_bi.db import connect
    path = str(tmp_path / "persist.db")
    a1 = create_app(db_path=path, start_jobs=False, testing=True)
    a1.test_client().post("/empresas/nueva", data={"name": "Constructora Persistente"})
    a2 = create_app(db_path=path, start_jobs=False, testing=True)
    assert "Constructora Persistente" in a2.test_client().get("/empresas").get_data(as_text=True)
    assert connect(path).execute("SELECT COUNT(*) n FROM companies").fetchone()["n"] == 1


def test_external_messages_never_cite_conflict_news(conn, web):
    cid = _investigated(conn)
    outreach.generate(conn, cid)
    d = {r["channel"]: r["body"] for r in conn.execute("SELECT channel, body FROM outreach_drafts")}
    for ch in ("email", "linkedin", "meeting"):
        assert "denuncian" not in d[ch].lower(), ch
        assert "Torre Horizonte" in d[ch], ch            # gancho: proyecto con inversión documentada
    assert "denuncian" in d["pitch"].lower() and "NO mencionar" in d["pitch"]
