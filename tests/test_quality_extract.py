from serrate_bi import quality as q
from serrate_bi.research import extract


def test_name_keys_and_dedupe_keys():
    assert q.name_key("Constructora Ejemplo Andina S.R.L.") == q.name_key("constructora ejemplo andina srl")
    assert q.core_key("Constructora Ejemplo Andina S.R.L.") == "ejemplo andina"
    assert q.core_key("Ejemplo Andina Ltda.") == "ejemplo andina"
    assert q.name_key("Inmobiliaria Peña & Cía. S.A.") == "inmobiliaria pena y cia"


def test_phone_normalization():
    assert q.normalize_phone("(3) 3456789") is None or q.normalize_phone("33456789") == "+591 33456789"
    assert q.normalize_phone("+591 71234567") == "+591 71234567"
    assert q.normalize_phone("00591-2-2123456") == "+591 22123456"
    assert q.normalize_phone("123") is None


def test_city_and_sector_detection():
    assert q.detect_city("Oficinas en Santa Cruz de la Sierra, Bolivia") == ("Santa Cruz de la Sierra", "Santa Cruz")
    assert q.detect_city("Proyecto en Cochabamba") == ("Cochabamba", "Cochabamba")
    assert q.detect_city("Sin ciudad") == (None, None)
    assert q.classify_sector("Desarrolladora de condominios y edificios") == "Desarrollo inmobiliario"
    assert q.classify_sector("Construcción de carreteras y puentes") == "Infraestructura"
    assert q.classify_sector("Venta de zapatos") is None


def test_emails_only_literal_and_filtered():
    html = ('<a href="mailto:Info@Empresa.test?subject=x">x</a> ventas@empresa.test '
            '<img src="logo@2x.png"> user@example.com')
    assert extract.extract_emails(html) == ["info@empresa.test", "ventas@empresa.test"]


def test_phones_require_context():
    text = "<p>Teléfono: (3) 3456789</p><p>NIT 1029384756 código 20231234</p><p>+591 71234567</p>"
    phones = extract.extract_phones(text)
    assert "+591 71234567" in phones
    assert all("1029384756" not in p and "20231234" not in p for p in phones)


def test_executive_extraction_explicit_only():
    text = "Gerente General: Ing. Mariela Quispe Rojas\nNuestro equipo de Santa Cruz es el mejor."
    ms = extract.extract_executives(text)
    assert [(m.name, m.position) for m in ms] == [("Mariela Quispe Rojas", "Gerente General")]
    assert extract.extract_executives("Construimos edificios en Santa Cruz de la Sierra") == []
    ms = extract.extract_executives("Juan Pablo Torrez, Gerente General de la firma, dijo que…")
    assert ms and ms[0].name == "Juan Pablo Torrez"


def test_linkedin_title_parsing():
    assert extract.parse_linkedin_title("Mariela Quispe Rojas - Gerente General - Constructora X | LinkedIn") == \
        ("Mariela Quispe Rojas", "Gerente General", "Constructora X")
    assert extract.parse_linkedin_title("Constructora X | LinkedIn") is None


def test_projects_and_company_names_from_news():
    t = "Desarrolladora Cumbre Test inicia la Urbanización Valle Alto en Cochabamba"
    assert ("Urbanización Valle Alto", "Urbanización") in extract.extract_projects(t)
    assert "Desarrolladora Cumbre Test" in extract.extract_company_names(t)
    assert "Conflicto / litigio" in extract.classify_news("Compradores denuncian incumplimiento")


def test_emails_near_name_not_guessed():
    text = "Gerente General: Ing. Mariela Quispe Rojas — correo mquispe@x.test\n" + "x" * 400 + "otro@x.test"
    assert extract.emails_near(text, "Mariela Quispe Rojas", 150) == ["mquispe@x.test"]
