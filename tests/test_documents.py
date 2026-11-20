"""Carga de documentos: Excel/CSV, PDF, fotos (OCR simulado). Datos ficticios."""
import io
import re

from openpyxl import Workbook

from serrate_bi import documents, repo
from serrate_bi import quality as q
from serrate_bi.research import claude_engine


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf(lines):
    """PDF mínimo con texto (Helvetica) para pruebas."""
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


def _upload(client, name, data, **form):
    return client.post("/documentos/subir", data={"files": (io.BytesIO(data), name), **form},
                       content_type="multipart/form-data")


def test_header_mapping():
    assert documents.map_header("Razón Social") == "name"
    assert documents.map_header("Correo del contacto") == "exec_email"
    assert documents.map_header("E-mail") == "email"
    assert documents.map_header("Teléfono") == "phone"
    assert documents.map_header("Actividad · confianza") == ""
    assert documents.map_header("Gerente General") == "exec_name"


def test_excel_import_updates_and_creates_without_duplicates(client, conn):
    cid, _ = repo.create_company(conn, "Constructora Ejemplo Andina S.R.L.", origin="descubrimiento automático")
    data = _xlsx([
        ["Listado de clientes 2026"],
        [],
        ["Empresa", "Ciudad", "Correo", "Teléfono", "Gerente General", "Cargo", "Correo del contacto", "Observaciones"],
        ["CONSTRUCTORA EJEMPLO ANDINA", "Santa Cruz", "info@ejemploandina.test", "3-3456789", "Mariela Quispe Rojas",
         "Gerente General", "mquispe@ejemploandina.test", "Cliente desde 2024"],
        ["Inmobiliaria Prueba Norte", "Cochabamba", "correo-invalido", "Pendiente", "", "", "", ""],
        ["", "La Paz", "x@y.test", "", "", "", "", ""],
    ])
    r = _upload(client, "clientes.xlsx", data)
    did = int(re.search(r"/documentos/(\d+)", r.headers["Location"]).group(1))
    page = client.get(f"/documentos/{did}").get_data(as_text=True)
    assert "1</b> empresas nuevas" in page and "1</b> existentes se actualizan" in page
    mapping = {f"m{i}": m for i, m in enumerate(
        ["name", "city", "email", "phone", "exec_name", "exec_position", "exec_email", "notes"])}
    client.post(f"/documentos/{did}", data={**mapping, "action": "import", "confidence": "verificado", "create_new": "1"})
    assert conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"] == 2
    c = repo.get_company(conn, cid)
    assert c["email"] == "info@ejemploandina.test" and c["phone"] == "+591 33456789"
    assert c["city"] == "Santa Cruz de la Sierra" and c["department"] == "Santa Cruz"
    st = repo.field_states(conn, "company", cid)["email"]
    src = conn.execute("SELECT * FROM sources WHERE id=?", (st["source_id"],)).fetchone()
    assert src["source_type"] == "documento" and "clientes.xlsx" in src["title"]
    ex = repo.executives_for(conn, cid)[0]
    assert ex["full_name"] == "Mariela Quispe Rojas" and ex["email"] == "mquispe@ejemploandina.test"
    assert conn.execute("SELECT content FROM activities WHERE kind='Comentario'").fetchone()["content"].endswith(
        "Cliente desde 2024")
    nueva = conn.execute("SELECT * FROM companies WHERE name='Inmobiliaria Prueba Norte'").fetchone()
    assert nueva["email"] is None and nueva["phone"] is None    # inválido / «Pendiente» no se importan
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    assert doc["applied_summary"]


def test_csv_semicolon_latin1(app, conn):
    data = "Razón social;Ciudad;Teléfono\nConstructora Sur Test;Tarija;+591 4 6611223\n".encode("latin-1")
    did, _ = documents.save_upload(conn, app.config["DB_PATH"], "lista.csv", data)
    assert documents.process(conn, app.config["DB_PATH"], did) == "procesado"
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    import json
    sheet = json.loads(doc["table_json"])["sheets"][0]
    assert sheet["mapping"] == ["name", "city", "phone"]
    stats = documents.import_table(conn, doc, sheet, sheet["mapping"], q.PROBABLE)
    assert stats["empresas_nuevas"] == 1
    c = conn.execute("SELECT * FROM companies").fetchone()
    assert c["city"] == "Tarija" and c["phone"] == "+591 46611223"


def test_pdf_proposals_and_apply(client, conn):
    cid, _ = repo.create_company(conn, "Constructora Ejemplo Andina", origin="manual")
    pdf = _pdf(["Constructora Ejemplo Andina - Propuesta", "Juan Carlos Perez Rojas", "Gerente General",
                "jcperez@ejemploandina.test", "Telefono: 3-3998877", "Proyecto: Torre Mirador en Santa Cruz"])
    r = _upload(client, "propuesta.pdf", pdf)
    did = int(re.search(r"/documentos/(\d+)", r.headers["Location"]).group(1))
    page = client.get(f"/documentos/{did}").get_data(as_text=True)
    assert "Juan Carlos Perez Rojas" in page and "jcperez@ejemploandina.test" in page
    assert f'value="{cid}" selected' in page            # sugiere la empresa mencionada
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    props = documents.proposals_from_text(doc["extracted_text"])
    labels = {p["label"] for p in props}
    assert "Teléfono" in labels and any(p["field"] == "project" for p in props)
    exec_email = next(p for p in props if p["target"] == "executive" and p["field"] == "email")
    assert exec_email["exec_name"] == "Juan Carlos Perez Rojas"
    assert not any(p["target"] == "company" and p["value"] == "jcperez@ejemploandina.test" for p in props)
    sel = [str(i) for i, p in enumerate(props)]
    client.post(f"/documentos/{did}/aplicar", data={"sel": sel, "company_id": str(cid), "confidence": "probable"})
    ex = repo.executives_for(conn, cid)[0]
    assert ex["full_name"] == "Juan Carlos Perez Rojas" and ex["position"] == "Gerente General"
    assert ex["email"] == "jcperez@ejemploandina.test"
    assert repo.get_company(conn, cid)["phone"] == "+591 33998877"
    assert conn.execute("SELECT name FROM projects").fetchone()["name"] == "Torre Mirador"
    assert conn.execute("SELECT company_id FROM documents WHERE id=?", (did,)).fetchone()["company_id"] == cid


def test_photo_without_ocr_is_kept_and_flagged(client, conn, monkeypatch):
    monkeypatch.setattr(documents.shutil, "which", lambda name: None)
    r = _upload(client, "tarjeta.jpg", b"\xff\xd8\xff\xe0fakejpeg")
    did = int(re.search(r"/documentos/(\d+)", r.headers["Location"]).group(1))
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    assert doc["status"] == "requiere OCR" and "ANTHROPIC_API_KEY" in doc["message"]
    assert client.get(f"/documentos/{did}/archivo").status_code == 200


def test_photo_with_ocr_new_company(client, conn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(claude_engine, "transcribe", lambda data, mt: (
        "DESARROLLADORA CUMBRE TEST\nAna Lucía Vargas Soliz\nCEO\nana.vargas@cumbre.test\n"
        "Cel. 71122334\nwww.cumbre.test\nCochabamba - Bolivia"))
    r = _upload(client, "tarjeta.png", b"\x89PNGfake")
    did = int(re.search(r"/documentos/(\d+)", r.headers["Location"]).group(1))
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    assert doc["extraction_method"] == "OCR (Claude)"
    props = documents.proposals_from_text(doc["extracted_text"])
    client.post(f"/documentos/{did}/aplicar", data={"sel": [str(i) for i in range(len(props))],
                                                    "new_company": "Desarrolladora Cumbre Test",
                                                    "confidence": "probable"})
    c = conn.execute("SELECT * FROM companies WHERE name='Desarrolladora Cumbre Test'").fetchone()
    assert c["website"] == "https://cumbre.test" and c["phone"] == "+591 71122334" and c["city"] == "Cochabamba"
    ex = repo.executives_for(conn, c["id"])[0]
    assert ex["full_name"] == "Ana Lucía Vargas Soliz" and ex["position"] == "CEO"
    assert ex["email"] == "ana.vargas@cumbre.test"


def test_rejects_unsupported_and_deduplicates(client, conn):
    r = _upload(client, "virus.exe", b"MZ...")
    assert conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 0
    data = _xlsx([["Empresa", "Ciudad"], ["Constructora Uno Test", "La Paz"]])
    _upload(client, "a.xlsx", data)
    _upload(client, "a-copia.xlsx", data)
    assert conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 1
    assert client.get("/documentos").status_code == 200
