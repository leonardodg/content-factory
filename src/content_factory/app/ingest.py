import re

import httpx

from content_factory.app.ports import SsrfGuard
from content_factory.domain.errors import DomainError
from content_factory.domain.models import CandidateVideo

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_NAME_DESC_RE = re.compile(r"""name\s*=\s*["']description["']""", re.IGNORECASE)
_META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _guard_unset(url: str) -> None:
    """Fail-closed: sem guard injetada nunca busca — o wire é obrigatório.

    A guarda real (`infra/ssrf.assert_public_http`) é injetada pela
    composition root (CLI); default não pode ser o guard real porque isso
    exigiria que `app/` importasse `infra/`.
    """
    raise DomainError(f"guarda SSRF não injetada (wire obrigatório): {url}")


def _extract(html: str) -> tuple[str, str]:
    """Extrai `<title>` e `meta[name=description]` (qualquer ordem de atributos)."""
    title = ""
    found = _TITLE_RE.search(html)
    if found:
        title = found.group(1).strip()
    description = ""
    for tag in _META_RE.finditer(html):
        if _META_NAME_DESC_RE.search(tag.group(0)):
            content = _META_CONTENT_RE.search(tag.group(0))
            if content:
                description = content.group(1).strip()
            break
    return title, description


async def _fetch(url: str, transport: httpx.AsyncBaseTransport | None) -> str:
    """GET com timeout 10 s, no máx. 2 MB (corte em aiter_bytes), sem redirect."""
    async with (
        httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as client,
        client.stream("GET", url) as response,
    ):
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            if received >= MAX_BYTES:
                break
            piece = chunk[: MAX_BYTES - received]
            chunks.append(piece)
            received += len(piece)
        return b"".join(chunks).decode("utf-8", errors="replace")


async def ingest_url(
    url: str,
    transport: httpx.AsyncBaseTransport | None = None,
    guard: SsrfGuard | None = None,
) -> CandidateVideo:
    """Valida URL (guarda SSRF injetada), baixa e devolve candidato platform="web"."""
    (guard or _guard_unset)(url)
    html = await _fetch(url, transport)
    title, description = _extract(html)
    return CandidateVideo(
        platform="web",
        platform_video_id=url,
        url=url,
        creator_handle="",
        title=title,
        description=description,
    )
