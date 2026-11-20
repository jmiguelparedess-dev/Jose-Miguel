"""Internet simulada para las pruebas. Todas las empresas, personas y dominios son FICTICIOS (.test)."""
import os
import sys
from urllib.parse import urlencode

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from serrate_bi import create_app  # noqa: E402
from serrate_bi.db import connect  # noqa: E402
from serrate_bi.research import http  # noqa: E402

SITE = "https://ejemploandina.test"

HOME = f"""<html><head><title>Constructora Ejemplo Andina | Edificios y condominios en Santa Cruz</title>
<meta name="description" content="Constructora Ejemplo Andina: construcción de edificios residenciales y condominios en Santa Cruz de la Sierra.">
</head><body><nav><a href="/">Inicio</a><a href="/nosotros">Nosotros</a><a href="/proyectos">Proyectos</a>
<a href="/contacto">Contacto</a><a href="https://www.facebook.com/ejemploandina">FB</a></nav>
<h1>Constructora Ejemplo Andina S.R.L.</h1><p>Más de 20 años de construcción en Bolivia.</p>
<img src="logo@2x.png"><footer>Escríbanos: ventas@ejemploandina.test ·
<a href="https://www.linkedin.com/company/ejemplo-andina-test/">LinkedIn</a></footer></body></html>"""

CONTACT = """<html><head><title>Contacto</title></head><body><h1>Contacto</h1>
<p>Dirección: Av. Cristo Redentor 1234, Santa Cruz de la Sierra, Bolivia</p>
<p>Teléfono: (3) 3456789</p><p>WhatsApp: +591 71234567</p>
<p><a href="mailto:info@ejemploandina.test">info@ejemploandina.test</a></p>
<p>Código de proyecto 20231234 y RUC 1029384756</p></body></html>"""

ABOUT = """<html><head><title>Nosotros</title></head><body><h1>Nuestro equipo</h1>
<p>Gerente General: Ing. Mariela Quispe Rojas — correo mquispe@ejemploandina.test</p>
<p>La empresa fue fundada en 2004 en Santa Cruz.</p>
<p>Gerente Técnico: Arq. Pablo Soria Vaca</p>
<a href="https://www.linkedin.com/in/mariela-quispe-test/">Perfil</a></body></html>"""

PROJECTS = """<html><head><title>Proyectos</title></head><body><h1>Proyectos</h1>
<h3>Torre Horizonte</h3><p>Edificio de 18 pisos en Equipetrol, Santa Cruz de la Sierra. En preventa.</p>
<h3>Condominio Las Palmeras</h3><p>Condominio cerrado en Urubó.</p></body></html>"""

NEWS_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Constructora Ejemplo Andina lanza la Torre Horizonte con inversión de $us 12 millones - Diario Ficticio</title>
<link>https://diarioficticio.test/torre-horizonte</link><pubDate>{d1}</pubDate>
<source url="https://diarioficticio.test">Diario Ficticio</source>
<description>La Constructora Ejemplo Andina inició la preventa de la Torre Horizonte en Santa Cruz.</description></item>
<item><title>Compradores denuncian retraso en condominio de Constructora Ejemplo Andina - Prensa Test</title>
<link>https://prensatest.test/reclamo</link><pubDate>{d2}</pubDate>
<source url="https://prensatest.test">Prensa Test</source>
<description>Reclamo por incumplimiento de plazos en Santa Cruz.</description></item>
<item><title>Otra empresa sin relación anuncia resultados - Diario Ficticio</title>
<link>https://diarioficticio.test/otra</link><pubDate>{d1}</pubDate><source url="https://diarioficticio.test">Diario Ficticio</source></item>
</channel></rss>"""

DISCOVERY_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Desarrolladora Cumbre Test inicia la Urbanización Valle Alto en Cochabamba - Diario Ficticio</title>
<link>https://diarioficticio.test/valle-alto</link><pubDate>{d1}</pubDate><source url="https://diarioficticio.test">Diario Ficticio</source>
<description>La Desarrolladora Cumbre Test invertirá en la Urbanización Valle Alto, Cochabamba.</description></item>
</channel></rss>"""

EMPTY_RSS = '<?xml version="1.0"?><rss><channel></channel></rss>'


def ddg_page(results):
    items = "".join(
        f'<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg={u}">{t}</a>'
        f'<a class="result__snippet">{s}</a></div>' for t, u, s in results)
    return f"<html><body>{items}</body></html>"


class FakeWeb:
    def __init__(self):
        from datetime import date, timedelta
        from email.utils import format_datetime
        from datetime import datetime, timezone
        d1 = format_datetime(datetime.now(timezone.utc) - timedelta(days=20))
        d2 = format_datetime(datetime.now(timezone.utc) - timedelta(days=60))
        self.pages = {SITE: HOME, SITE + "/contacto": CONTACT, SITE + "/nosotros": ABOUT,
                      SITE + "/proyectos": PROJECTS}
        self.news = NEWS_RSS.format(d1=d1, d2=d2)
        self.discovery_news = DISCOVERY_RSS.format(d1=d1)
        self.calls = []

    def __call__(self, url, params=None, headers=None, respect_robots=False):
        self.calls.append((url, params))
        q = (params or {}).get("q", "")
        if "duckduckgo" in url:
            if "Ejemplo Andina" in q and "linkedin.com/company" in q:
                res = [("Constructora Ejemplo Andina | LinkedIn", "https://bo.linkedin.com/company/ejemplo-andina-test",
                        "Constructora Ejemplo Andina. Construcción. Santa Cruz.")]
            elif "Ejemplo Andina" in q and "linkedin.com/in" in q:
                res = [("Mariela Quispe Rojas - Gerente General - Constructora Ejemplo Andina | LinkedIn",
                        "https://bo.linkedin.com/in/mariela-quispe-test", "Constructora Ejemplo Andina · Santa Cruz")]
            elif "Ejemplo Andina" in q:
                res = [("Constructora Ejemplo Andina - Inicio", SITE + "/",
                        "Constructora en Santa Cruz, Bolivia.")]
            elif "constructora" in q.lower() and "Santa Cruz" in q:
                res = [("Constructora Ejemplo Andina | Edificios en Santa Cruz", SITE + "/",
                        "Constructora de edificios en Santa Cruz de la Sierra, Bolivia"),
                       ("Las 10 mejores constructoras - Facebook", "https://www.facebook.com/x", "Constructora"),
                       ("Receta de cocina", "https://cocina.test/", "nada que ver")]
            else:
                res = []
            return http.Response(url, 200, ddg_page(res), "text/html")
        if "news.google.com" in url:
            if "Ejemplo Andina" in q:
                return http.Response(url, 200, self.news, "application/rss+xml")
            if "Cochabamba" in q and "urbanización" in q.lower():
                return http.Response(url, 200, self.discovery_news, "application/rss+xml")
            return http.Response(url, 200, EMPTY_RSS, "application/rss+xml")
        if "bing.com" in url:
            return http.Response(url, 200, EMPTY_RSS, "application/rss+xml")
        key = url.rstrip("/")
        if key in self.pages:
            return http.Response(url, 200, self.pages[key], "text/html; charset=utf-8")
        return http.Response(url, 404, "not found", "text/html")


@pytest.fixture
def web(monkeypatch):
    fw = FakeWeb()
    http.set_fetch_impl(fw)
    monkeypatch.setattr(http, "MIN_INTERVAL", 0)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    yield fw
    http.set_fetch_impl(None)


@pytest.fixture
def app(tmp_path, web):
    a = create_app(db_path=str(tmp_path / "test.db"), start_jobs=False, testing=True)
    yield a


@pytest.fixture
def conn(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def client(app):
    return app.test_client()
