"""Salvaguardas del motor asistido: datos sin fuente consultada se descartan; los demás se re-verifican."""
from serrate_bi import quality as q
from serrate_bi import repo
from serrate_bi.research import claude_engine, pipeline
from tests.conftest import SITE


def test_parse_json_block():
    assert claude_engine.parse_json_block('texto\n```json\n{"a": 1}\n```') == {"a": 1}
    assert claude_engine.parse_json_block('resultado: {"b": 2} fin') == {"b": 2}
    assert claude_engine.parse_json_block("sin json") == {}


def test_research_company_filters_unsourced(monkeypatch):
    data = {
        "fields": [
            {"field": "phone", "value": "+591 33456789", "source_url": SITE + "/contacto", "quote": "Teléfono"},
            {"field": "email", "value": "gerencia@inventado.test", "source_url": "https://nunca-consultado.test/x"},
        ],
        "executives": [
            {"full_name": "Mariela Quispe Rojas", "position": "Gerente General", "source_url": SITE + "/nosotros"},
            {"full_name": "Persona Inventada", "position": "CEO", "source_url": None},
        ],
        "projects": [], "news": [{"title": "Nota", "url": "https://no-vista.test/n"}],
    }
    monkeypatch.setattr(claude_engine, "_run_with_web",
                        lambda prompt, **kw: (data, {SITE + "/contacto", SITE + "/nosotros"}))
    out = claude_engine.research_company({"name": "Constructora Ejemplo Andina", "website": SITE})
    assert [f["field"] for f in out["fields"]] == ["phone"]
    assert [e["full_name"] for e in out["executives"]] == ["Mariela Quispe Rojas"]
    assert out["news"] == [] and out["discarded"] == 3


def test_claude_results_are_reverified_on_page(conn, web, monkeypatch):
    cid, _ = repo.create_company(conn, "Constructora Ejemplo Andina", origin="descubrimiento automático",
                                 website=SITE)
    repo.record_fact(conn, "company", cid, cid, "website", SITE, q.VERIFICADO, None, "auto")
    monkeypatch.setattr(claude_engine, "research_company", lambda c, log=None: {
        "fields": [{"field": "phone", "value": "+591 33456789", "source_url": SITE + "/contacto", "quote": "Tel"},
                   {"field": "legal_name", "value": "Ejemplo Andina Holding S.A.", "source_url": SITE + "/contacto"},
                   {"field": "sector", "value": "Minería", "source_url": SITE}],
        "executives": [{"full_name": "Mariela Quispe Rojas", "position": "Gerente General",
                        "email": "mquispe@ejemploandina.test", "source_url": SITE + "/nosotros"}],
        "projects": [], "news": [], "discarded": 2})
    ctx = pipeline.RunContext(conn, None)
    pipeline._claude_research(ctx, repo.get_company(conn, cid))
    st = repo.field_states(conn, "company", cid)
    assert st["phone"]["confidence"] == q.VERIFICADO           # aparece textual en el sitio oficial
    assert st["legal_name"]["confidence"] == q.PROBABLE         # no aparece en la página citada
    assert "sector" not in st                                   # valor fuera del catálogo
    ex = repo.executives_for(conn, cid)[0]
    assert ex["email"] == "mquispe@ejemploandina.test"
    assert ctx.stats["descartados_sin_fuente"] == 2


def test_claude_disabled_without_key(conn, web):
    assert not claude_engine.available()
    assert pipeline._engine_mode(conn) == "web"


def test_run_with_web_collects_urls_and_handles_pause(monkeypatch):
    from types import SimpleNamespace as NS
    calls = []
    responses = [
        NS(stop_reason="pause_turn", content=[NS(type="web_search_tool_result",
                                                 content=[NS(url="https://a.test/1"), NS(url="https://b.test/2")])]),
        NS(stop_reason="end_turn", content=[NS(type="web_fetch_tool_result", content=NS(url="https://c.test/3")),
                                            NS(type="text", text='```json\n{"fields": []}\n```')]),
    ]

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            return responses[len(calls) - 1]
    fake = NS(beta=NS(messages=Msgs()), messages=Msgs())
    monkeypatch.setattr(claude_engine, "_client", lambda: fake)
    data, seen = claude_engine._run_with_web("prompt")
    assert data == {"fields": []}
    assert seen == {"https://a.test/1", "https://b.test/2", "https://c.test/3"}
    assert len(calls) == 2 and calls[0]["model"] == claude_engine.MODEL
    assert calls[1]["messages"][-1]["role"] == "assistant"   # turno pausado reenviado para continuar
    assert calls[0]["fallbacks"] == "default"
