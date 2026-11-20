"""Proveedores de búsqueda intercambiables.

Web:      Brave Search API (BRAVE_API_KEY) > Google Programmable Search (GOOGLE_CSE_KEY + GOOGLE_CSE_CX)
          > DuckDuckGo HTML (sin clave).
Noticias: Google News RSS y Bing News RSS (sin clave).
Si un proveedor falla, se intenta el siguiente; los errores quedan registrados en el log del trabajo.
"""
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from . import http


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    published_at: str = None
    publisher: str = None
    extra: dict = field(default_factory=dict)


class ProviderError(Exception):
    pass


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


class DuckDuckGoHTML:
    name = "DuckDuckGo"

    def search(self, query, max_results=10):
        r = http.fetch("https://html.duckduckgo.com/html/", params={"q": query, "kl": "bo-es"})
        if not r.ok:
            raise ProviderError(f"DuckDuckGo respondió {r.status}")
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for res in soup.select(".result"):
            a = res.select_one("a.result__a")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            if "duckduckgo.com/l/" in href:
                qs = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
                href = unquote(qs.get("uddg", [href])[0])
            if "duckduckgo.com/y.js" in href:  # anuncios
                continue
            snip = res.select_one(".result__snippet")
            out.append(SearchResult(_clean(a.get_text()), href, _clean(snip.get_text() if snip else ""), self.name))
            if len(out) >= max_results:
                break
        return out


class BraveSearch:
    name = "Brave Search"

    def __init__(self, key):
        self.key = key

    def search(self, query, max_results=10):
        r = http.fetch("https://api.search.brave.com/res/v1/web/search",
                       params={"q": query, "count": min(max_results, 20), "country": "BO", "search_lang": "es"},
                       headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
        if not r.ok:
            raise ProviderError(f"Brave respondió {r.status}")
        import json
        data = json.loads(r.text)
        return [SearchResult(_clean(it.get("title")), it.get("url"), _clean(BeautifulSoup(
            it.get("description", ""), "html.parser").get_text()), self.name)
            for it in data.get("web", {}).get("results", [])[:max_results] if it.get("url")]


class GoogleCSE:
    name = "Google Programmable Search"

    def __init__(self, key, cx):
        self.key, self.cx = key, cx

    def search(self, query, max_results=10):
        r = http.fetch("https://www.googleapis.com/customsearch/v1",
                       params={"key": self.key, "cx": self.cx, "q": query, "num": min(max_results, 10),
                               "gl": "bo", "lr": "lang_es"})
        if not r.ok:
            raise ProviderError(f"Google CSE respondió {r.status}")
        import json
        data = json.loads(r.text)
        return [SearchResult(_clean(it.get("title")), it.get("link"), _clean(it.get("snippet")), self.name)
                for it in data.get("items", [])[:max_results] if it.get("link")]


def _rss_items(xml_text, provider):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ProviderError(f"RSS inválido de {provider}: {e}")
    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = _clean(item.findtext("link"))
        if not title or not link:
            continue
        pub = item.findtext("pubDate")
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub).date().isoformat()
            except (TypeError, ValueError):
                published = None
        src = item.find("source")
        publisher = _clean(src.text) if src is not None and src.text else None
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        desc = _clean(BeautifulSoup(item.findtext("description") or "", "html.parser").get_text())
        out.append(SearchResult(title, link, desc, provider, published, publisher,
                                {"publisher_url": src.get("url") if src is not None else None}))
    return out


class GoogleNewsRSS:
    name = "Google News"

    def search(self, query, max_results=15):
        r = http.fetch("https://news.google.com/rss/search",
                       params={"q": query, "hl": "es-419", "gl": "BO", "ceid": "BO:es-419"})
        if not r.ok:
            raise ProviderError(f"Google News respondió {r.status}")
        return _rss_items(r.text, self.name)[:max_results]


class BingNewsRSS:
    name = "Bing News"

    def search(self, query, max_results=15):
        r = http.fetch("https://www.bing.com/news/search", params={"q": query, "format": "rss", "cc": "BO"})
        if not r.ok:
            raise ProviderError(f"Bing News respondió {r.status}")
        return _rss_items(r.text, self.name)[:max_results]


def web_providers():
    provs = []
    if os.environ.get("BRAVE_API_KEY"):
        provs.append(BraveSearch(os.environ["BRAVE_API_KEY"]))
    if os.environ.get("GOOGLE_CSE_KEY") and os.environ.get("GOOGLE_CSE_CX"):
        provs.append(GoogleCSE(os.environ["GOOGLE_CSE_KEY"], os.environ["GOOGLE_CSE_CX"]))
    provs.append(DuckDuckGoHTML())
    return provs


def news_providers():
    return [GoogleNewsRSS(), BingNewsRSS()]


def search_with_fallback(providers, query, max_results=10, log=None, on_fail=None):
    """Prueba los proveedores en orden y devuelve los resultados del primero que funcione.
    `on_fail` se invoca si todos fallan (para contabilizar consultas sin respuesta)."""
    errors = []
    for p in providers:
        try:
            res = p.search(query, max_results)
            if log:
                log(f"{p.name}: «{query}» → {len(res)} resultados")
            return res
        except (ProviderError, http.FetchError, ValueError) as e:
            errors.append(f"{p.name}: {e}")
    if log:
        log("Sin resultados para «%s» (%s)" % (query, "; ".join(errors)))
    if on_fail and errors:
        on_fail()
    return []
