"""Cliente HTTP respetuoso: identifica al agente, respeta robots.txt, limita la frecuencia por dominio.
`fetch` es el único punto de salida a la red (las pruebas lo reemplazan por respuestas simuladas)."""
import threading
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlparse

import requests

USER_AGENT = ("Mozilla/5.0 (compatible; SerratePazBI/1.0; investigacion comercial con fuentes publicas)")
TIMEOUT = 15
MIN_INTERVAL = 1.0  # segundos entre peticiones al mismo dominio

_lock = threading.Lock()
_last_hit = {}
_robots = {}


@dataclass
class Response:
    url: str
    status: int
    text: str
    content_type: str = ""

    @property
    def ok(self):
        return 200 <= self.status < 300


class FetchError(Exception):
    pass


def _throttle(host):
    with _lock:
        wait = _last_hit.get(host, 0) + MIN_INTERVAL - time.time()
        _last_hit[host] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def allowed_by_robots(url) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=8)
            if r.status_code >= 400:
                rp = None
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def _do_get(url, params=None, headers=None, respect_robots=False) -> Response:
    if respect_robots and not allowed_by_robots(url):
        raise FetchError(f"robots.txt no permite consultar {url}")
    host = urlparse(url).netloc
    _throttle(host)
    h = {"User-Agent": USER_AGENT, "Accept-Language": "es-BO,es;q=0.9"}
    h.update(headers or {})
    try:
        r = requests.get(url, params=params, headers=h, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        raise FetchError(f"No se pudo consultar {url}: {e.__class__.__name__}") from e
    ctype = r.headers.get("Content-Type", "")
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return Response(url=r.url, status=r.status_code, text=r.text[:2_000_000], content_type=ctype)


# Punto de extensión reemplazable (tests).
_impl = _do_get


def fetch(url, params=None, headers=None, respect_robots=False) -> Response:
    return _impl(url, params=params, headers=headers, respect_robots=respect_robots)


def set_fetch_impl(fn):
    global _impl
    _impl = fn or _do_get
