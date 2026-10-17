"""F1 - adaptador por arquivo do protocolo `q1` (`FileWebQueries`).

Cobre o elo protocolo -> coletor: leitura do YAML por segmento, cache por
mtime, ausencia de arquivo (fallback legado) e protocolo malformado. Sem estes
testes, a ausencia de `segments/` na imagem do worker passava despercebida.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from content_factory.app.web_queries_plan import write_protocol
from content_factory.domain.models import SegmentConfig
from content_factory.domain.web_queries import (
    INTENT_LEXICON_VERSION,
    QUERY_PROTOCOL_VERSION,
    PlannedQuery,
    WebQueryProtocol,
    plan_web_queries,
    run_index_for,
)
from content_factory.infra.collectors.web_queries import FileWebQueries, protocol_path

SEG = SegmentConfig(
    slug="velas-artesanais", name="Velas", platforms=["web"], keywords=["vela artesanal"]
)


def _protocol(slug: str) -> WebQueryProtocol:
    queries = [
        PlannedQuery(
            query_id=f"q1:aprender:{i:02d}", query=f"como fazer vela {i}", intent="aprender"
        )
        for i in range(3)
    ]
    queries.append(PlannedQuery(query_id="q1:seed:00", query="vela artesanal", intent="seed"))
    return WebQueryProtocol(
        segment_slug=slug,
        version=QUERY_PROTOCOL_VERSION,
        seed=20261017,
        lexicon_version=INTENT_LEXICON_VERSION,
        queries=queries,
    )


def test_queries_for_reads_protocol_from_dir(tmp_path: Path) -> None:
    write_protocol(protocol_path(tmp_path, SEG.slug), _protocol(SEG.slug))
    when = datetime(2026, 10, 18, 8, tzinfo=UTC)
    source = FileWebQueries(tmp_path, clock=lambda: when)

    planned = source.queries_for(SEG)

    assert planned is not None
    expected = plan_web_queries(_protocol(SEG.slug).queries, run_index_for(when))
    assert [q.query_id for q in planned] == [q.query_id for q in expected]
    assert all(q.intent in {"aprender", "seed"} for q in planned)


def test_queries_for_missing_protocol_returns_none(tmp_path: Path) -> None:
    # sem protocolo, o coletor cai no legado de 1 consulta (ver web.py)
    assert FileWebQueries(tmp_path).queries_for(SEG) is None


def test_queries_for_invalid_yaml_returns_none(tmp_path: Path) -> None:
    protocol_path(tmp_path, SEG.slug).write_text("queries: nao-e-lista\n", encoding="utf-8")
    assert FileWebQueries(tmp_path).queries_for(SEG) is None


def test_queries_for_reloads_after_mtime_change(tmp_path: Path) -> None:
    path = protocol_path(tmp_path, SEG.slug)
    write_protocol(path, _protocol(SEG.slug))
    source = FileWebQueries(tmp_path)
    assert source.queries_for(SEG) is not None
    before = path.stat().st_mtime

    trimmed = _protocol(SEG.slug)
    trimmed.queries.pop()  # remove o seed
    write_protocol(path, trimmed)
    # Fs com resolucao grossa (1 s) podem manter o mtime no mesmo segundo e o
    # cache nao invalidaria; forca a mudanca para o teste depender do conteudo.
    stamp = before + 10
    os.utime(path, (stamp, stamp))

    assert all(q.intent != "seed" for q in (source.queries_for(SEG) or []))


def test_yaml_com_sintaxe_quebrada_vira_none_e_loga(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Revisão externa 3.4: `yaml.YAMLError` não é `ValueError` e escapava."""
    protocol_path(tmp_path, SEG.slug).write_text('queries: [\n  "aberto\n', encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert FileWebQueries(tmp_path).queries_for(SEG) is None
    assert any("protocolo web inválido" in r.getMessage() for r in caplog.records)


def test_cache_invalida_com_mesmo_mtime_e_tamanho_diferente(tmp_path: Path) -> None:
    """Revisão externa 3.5: troca do arquivo dentro da resolução do mtime."""
    path = protocol_path(tmp_path, SEG.slug)
    write_protocol(path, _protocol(SEG.slug))
    stat = path.stat()
    source = FileWebQueries(tmp_path)
    assert any(q.intent == "seed" for q in source.queries_for(SEG) or [])

    trimmed = _protocol(SEG.slug)
    trimmed.queries.pop()  # remove o seed → arquivo menor
    write_protocol(path, trimmed)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # MESMO mtime em ns
    assert all(q.intent != "seed" for q in (source.queries_for(SEG) or []))
