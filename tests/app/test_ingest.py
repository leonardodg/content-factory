import httpx
import pytest

from content_factory.app.ingest import ingest_url
from content_factory.domain.errors import DomainError


def test_app_package_does_not_import_infra() -> None:
    """Regra absoluta: app/ nunca importa infra/ (guard é porta, wire é da CLI).

    AST em vez de sys.modules: infra já está em sys.modules por causa de
    outros testes/CLI (resultado dependente de ordem); import-linter não está
    configurado no repo — parse do fonte é determinístico e não cai na rede.
    """
    import ast
    from pathlib import Path

    import content_factory.app as app_pkg

    app_dir = Path(app_pkg.__file__).parent
    offenders: list[str] = []
    for path in sorted(app_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("content_factory.infra")
            ):
                offenders.append(f"{path.name}:{node.lineno} from {node.module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{node.lineno} import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("content_factory.infra")
                )
    assert not offenders, f"app importa infra: {offenders}"


def _no_guard(url: str) -> None:
    """Guarda SSRF desligada: a fronteira HTTP já é mockada nestes testes."""
    _ = url


@pytest.mark.asyncio
async def test_ingest_extracts_title() -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Minha vela</title>"
                "<meta name='description' content='feito a mao'></head></html>"
            ),
        )

    cand = await ingest_url(
        "https://ex.com/vela", transport=httpx.MockTransport(_route), guard=_no_guard
    )
    assert cand.title == "Minha vela"
    assert cand.platform == "web" and cand.url == "https://ex.com/vela"


@pytest.mark.asyncio
async def test_ingest_caps_body_at_2mb() -> None:
    """Corpo além de 2 MB é cortado — meta description lá no fim não chega."""

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head><title>ok</title></head><body>"
                + "x" * 2_100_000
                + "<meta name='description' content='depois do limite'></body></html>"
            ),
        )

    cand = await ingest_url(
        "https://ex.com/big", transport=httpx.MockTransport(_route), guard=_no_guard
    )
    assert cand.title == "ok"
    assert cand.description == ""


@pytest.mark.asyncio
async def test_ingest_extracts_meta_description() -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head>"
                "<meta content='feito a mao' name='description'>"
                "<title>Outra vela</title></head></html>"
            ),
        )

    cand = await ingest_url(
        "https://ex.com/vela2", transport=httpx.MockTransport(_route), guard=_no_guard
    )
    assert cand.description == "feito a mao"
    assert cand.title == "Outra vela"


@pytest.mark.asyncio
async def test_ingest_blocks_private() -> None:
    # guarda real injetada (mesmo wire da CLI): 127.0.0.1 → DomainError ANTES de fetch
    from content_factory.app.ingest import ingest_url as go
    from content_factory.infra.ssrf import assert_public_http

    with pytest.raises(DomainError):
        await go("http://127.0.0.1:9/x", guard=assert_public_http)


@pytest.mark.asyncio
async def test_ingest_without_guard_fails_closed() -> None:
    """Default é fail-closed: guard esquecida nunca busca sem validação."""
    with pytest.raises(DomainError, match="guarda SSRF não injetada"):
        await ingest_url("https://ex.com/qualquer")
