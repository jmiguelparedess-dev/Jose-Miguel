"""Reglas de procedencia: no se pierden datos, se registra cada cambio, la confianza se maneja con reglas."""
from serrate_bi import quality as q
from serrate_bi import repo


def _company(conn):
    sid = repo.upsert_source(conn, "https://directorio.test/a", "Directorio", "buscador")
    cid, created = repo.create_company(conn, "Constructora Prueba S.R.L.", source_id=sid, confidence=q.PROBABLE, origin="descubrimiento automático",
                                       phone="+591 70000001")
    assert created
    return cid


def test_duplicate_detection(conn):
    cid = _company(conn)
    cid2, created = repo.create_company(conn, "CONSTRUCTORA PRUEBA SRL")
    assert not created and cid2 == cid
    cid3, created = repo.create_company(conn, "Prueba Ltda.")
    assert not created and cid3 == cid
    repo.record_fact(conn, "company", cid, cid, "website", "https://prueba.test", q.VERIFICADO, None, "manual")
    cid4, created = repo.create_company(conn, "Otro nombre comercial", website="www.prueba.test/contacto")
    assert not created and cid4 == cid


def test_alternative_values_kept_and_corroboration(conn):
    cid = _company(conn)
    s2 = repo.upsert_source(conn, "https://prensa.test/n", "Nota", "noticia")
    # Mismo valor desde otro dominio -> se eleva a verificado.
    assert repo.record_fact(conn, "company", cid, cid, "phone", "+591 70000001", q.PROBABLE, s2, "auto") == "mejorado"
    st = repo.field_states(conn, "company", cid)["phone"]
    assert st["confidence"] == q.VERIFICADO
    # Valor distinto con confianza menor -> alternativo; el vigente no cambia.
    assert repo.record_fact(conn, "company", cid, cid, "phone", "+591 79999999", q.PROBABLE, s2, "auto") == "alternativo"
    assert repo.get_company(conn, cid)["phone"] == "+591 70000001"
    alts = repo.alternatives(conn, "company", cid)
    assert [a["value"] for a in alts["phone"]] == ["+591 79999999"]
    kinds = [r["change_type"] for r in conn.execute("SELECT change_type FROM change_log WHERE field='phone'")]
    assert kinds == ["nuevo", "confianza elevada", "valor alternativo"]


def test_higher_confidence_replaces_but_manual_is_protected(conn):
    cid = _company(conn)
    off = repo.upsert_source(conn, "https://prueba.test/contacto", "Contacto", "sitio oficial")
    assert repo.record_fact(conn, "company", cid, cid, "phone", "+591 33456789", q.VERIFICADO, off, "auto") == "reemplazado"
    assert repo.get_company(conn, cid)["phone"] == "+591 33456789"
    repo.manual_update(conn, "company", cid, cid, "phone", "+591 33111111", q.VERIFICADO, "Confirmado por llamada")
    # Un dato automático nunca pisa un dato manual.
    assert repo.record_fact(conn, "company", cid, cid, "phone", "+591 33456789", q.VERIFICADO, off, "auto") == "alternativo"
    assert repo.get_company(conn, cid)["phone"] == "+591 33111111"
    # Todas las observaciones siguen en la tabla de hechos.
    vals = {r["value"] for r in conn.execute("SELECT value FROM facts WHERE field='phone'")}
    assert vals == {"+591 70000001", "+591 33456789", "+591 33111111"}


def test_same_source_update_in_later_run(conn):
    cid = _company(conn)
    off = repo.upsert_source(conn, "https://prueba.test/contacto", "Contacto", "sitio oficial")
    repo.record_fact(conn, "company", cid, cid, "email", "info@prueba.test", q.VERIFICADO, off, "auto", run_id=1)
    # En la misma ejecución, un segundo correo queda como alternativo.
    assert repo.record_fact(conn, "company", cid, cid, "email", "ventas@prueba.test", q.VERIFICADO, off, "auto",
                            run_id=1) == "alternativo"
    # En una ejecución posterior, si la misma fuente publica otro valor, se actualiza y se registra.
    off2 = repo.upsert_source(conn, "https://prueba.test/", "Inicio", "sitio oficial")
    assert repo.record_fact(conn, "company", cid, cid, "email", "contacto@prueba.test", q.VERIFICADO, off2, "auto",
                            run_id=2) == "reemplazado"
    row = conn.execute("SELECT * FROM change_log WHERE field='email' AND change_type='modificado'").fetchone()
    assert row["old_value"] == "info@prueba.test" and row["new_value"] == "contacto@prueba.test"


def test_adopt_alternative_and_manual_delete(conn):
    cid = _company(conn)
    s2 = repo.upsert_source(conn, "https://prensa.test/n", "Nota", "noticia")
    repo.record_fact(conn, "company", cid, cid, "city", "La Paz", q.PROBABLE, s2, "auto")
    repo.record_fact(conn, "company", cid, cid, "city", "Santa Cruz de la Sierra", q.PROBABLE, s2, "auto")
    alt = repo.alternatives(conn, "company", cid)["city"][0]
    repo.adopt_fact(conn, alt["id"])
    assert repo.get_company(conn, cid)["city"] == "Santa Cruz de la Sierra"
    repo.manual_update(conn, "company", cid, cid, "city", "", q.PROBABLE)
    assert repo.get_company(conn, cid)["city"] is None
    assert conn.execute("SELECT COUNT(*) n FROM change_log WHERE change_type='eliminado'").fetchone()["n"] == 1


def test_invalid_confidence_rejected(conn):
    cid = _company(conn)
    import pytest
    with pytest.raises(ValueError):
        repo.record_fact(conn, "company", cid, cid, "city", "La Paz", "seguro", None, "auto")
