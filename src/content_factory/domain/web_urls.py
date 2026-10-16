"""F0 — filtro de anúncio e normalização de URL, puros (sem I/O).

Princípio: uma página só é descartada com **sinal verificável**. Parâmetro
na URL, marketplace ou "oferta" no título não são sinal. A lista de sinais
é fechada e versionada (`AD_RULES_VERSION`); a normalização tem lista
fechada de parâmetros de rastreio (`CANON_VERSION`).
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

AD_RULES_VERSION = "ads-v1"
CANON_VERSION = "canon-v1"

# (motivo, sufixo de host, prefixo de caminho). Host casa por igualdade OU
# subdomínio (`x.bing.com` casa `bing.com`). Lista FECHADA: só redirecionamento
# publicitário verificável.
_AD_RULES: tuple[tuple[str, str, str], ...] = (
    ("ad:bing_aclick", "bing.com", "/aclick"),
    ("ad:bing_aclk", "bing.com", "/aclk"),
    ("ad:ddg_yjs", "duckduckgo.com", "/y.js"),
    ("ad:googleadservices", "googleadservices.com", "/"),
    ("ad:doubleclick", "doubleclick.net", "/"),
    ("ad:google_aclk", "google.com", "/aclk"),
)

# Marcação explícita de publicidade do provedor fica PREPARADA e INATIVA: o
# `ddgs.text` devolve só title/href/body, sem flag de anúncio. Quando (se) o
# provedor expuser, basta preencher o nome do campo no `raw`.
AD_MARKER_FIELD: str | None = None

# Lista FECHADA de parâmetros de rastreio. Qualquer outro parâmetro é
# PRESERVADO: `?id=`, `?p=`, `?q=` podem mudar o conteúdo da página.
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "msclkid", "mc_eid", "srsltid", "ref_src"})


@dataclass(frozen=True)
class CanonicalUrl:
    """URL canônica + `ok` (False = inválida, devolvida como veio)."""

    url: str
    ok: bool


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def ad_signal(url: str, title: str = "", body: str = "") -> str | None:
    """Devolve o motivo verificável de anúncio, ou `None`.

    `title`/`body` entram por contrato para a regra de marcação explícita
    (`AD_MARKER_FIELD`), hoje inativa — o `ddgs` não expõe flag.
    """
    _ = AD_MARKER_FIELD, title, body
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    path = parts.path or "/"
    for reason, suffix, prefix in _AD_RULES:
        if _host_matches(host, suffix) and path.startswith(prefix):
            return reason
    return None


def _canonical_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (k, v)
        for k, v in pairs
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    kept.sort()
    return urlencode(kept)


def canonicalize(url: str) -> CanonicalUrl:
    """Normaliza a URL: host minúsculo, sem fragmento, sem rastreio.

    Idempotente. URL inválida volta como veio com `ok=False`, sem levantar.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return CanonicalUrl(url, False)
    if not parts.scheme or not parts.netloc or not host:
        return CanonicalUrl(url, False)
    netloc = host if port is None else f"{host}:{port}"
    scheme = parts.scheme.lower()
    query = _canonical_query(parts.query)
    return CanonicalUrl(urlunsplit((scheme, netloc, parts.path, query, "")), True)


def canonical_url(url: str) -> str:
    """Só a string canônica (planilha/documentação). Ver `canonicalize`."""
    return canonicalize(url).url


def domain_of(url: str) -> str:
    """Domínio (host minúsculo) da URL canônica; `""` se inválida."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
