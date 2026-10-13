"""Testes da Task 8 — score-sample + score-correlation.

Use-cases puros: exportação do ranking e correlação de Spearman entre
o rank do sistema (total) e a avaliação manual, SEM scipy (ver nota em
spearman_rank).
"""

import csv
from pathlib import Path

from content_factory.app.score_sample import (
    ScoreSampleRow,
    read_score_csv,
    score_correlation,
    spearman_rank,
    write_score_csv,
)


def _row(video_id: int, total: float, url: str = "https://x") -> ScoreSampleRow:
    return ScoreSampleRow(
        video_id=video_id,
        url=url,
        total=total,
        performance=None,
        velocity=None,
        engagement=None,
        relevance=total / 2,
        recency=total / 2,
        metrics_available=[],
    )


# --- Passo 8.1: use-case de exportação (delegação pura) ---


class _FakeScoreSampleRepo:
    def __init__(self, rows: list[ScoreSampleRow]) -> None:
        self._rows = rows
        self.calls: list[tuple[int, int]] = []

    async def top_scored_for_segment(self, segment_id: int, limit: int) -> list[ScoreSampleRow]:
        self.calls.append((segment_id, limit))
        return self._rows[:limit]


async def test_score_sample_delegates_to_repo() -> None:
    from content_factory.app.score_sample import score_sample

    repo = _FakeScoreSampleRepo([_row(1, 90.0), _row(2, 80.0)])
    out = await score_sample(segment_id=7, limit=50, repo=repo)
    assert repo.calls == [(7, 50)]
    assert [r.video_id for r in out] == [1, 2]


# --- Passo 8.3: CSV roundtrip ---


def test_write_score_csv_roundtrip(tmp_path: Path) -> None:
    rows = [
        _row(1, 90.0, url="https://a"),
        _row(2, 80.0, url="https://b"),
    ]
    out = tmp_path / "sample.csv"
    write_score_csv(rows, out)
    assert out.exists()

    text = out.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert header.split(",") == [
        "video_id",
        "url",
        "total",
        "performance",
        "velocity",
        "engagement",
        "relevance",
        "recency",
        "metrics_available",
        "avaliacao_manual",
    ]

    reloaded = read_score_csv(out)
    assert len(reloaded) == 2
    assert reloaded[0].video_id == 1
    assert reloaded[0].total == 90.0
    # coluna avaliacao_manual fica vazia no export
    assert reloaded[0].avaliacao_manual is None


def test_write_score_csv_keeps_avaliacao_manual_when_present(tmp_path: Path) -> None:
    """Ao reler um CSV já anotado, a anotação sobrevive."""
    rows = [_row(1, 90.0)]
    out = tmp_path / "s.csv"
    write_score_csv(rows, out)
    # anota manualmente: substitui o último campo (vazio) pelo valor
    lines = out.read_text(encoding="utf-8").splitlines()
    data_fields = lines[1].split(",")
    data_fields[-1] = "4"
    out.write_text(lines[0] + "\n" + ",".join(data_fields) + "\n", encoding="utf-8")
    reloaded = read_score_csv(out)
    assert reloaded[0].avaliacao_manual == 4.0


def _annotate(path: Path, values: list[float | None]) -> None:
    """Anota o CSV: substitui o último campo (avaliacao_manual) de cada linha
    de dados. Linhas extras (values mais curto que o arquivo) ficam sem
    anotação — como um humano anotando parcialmente."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out_lines = [lines[0]]
    for i, line in enumerate(lines[1:]):
        fields = line.split(",")
        if i < len(values):
            v = values[i]
            fields[-1] = "" if v is None else str(v)
        out_lines.append(",".join(fields))
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


# --- Passo 8.4: Spearman sem scipy ---


def test_spearman_perfect_correlation() -> None:
    r, n = spearman_rank([90.0, 80.0, 70.0], [5.0, 4.0, 3.0])
    assert abs(r - 1.0) < 1e-9
    assert n == 3


def test_spearman_inverse_correlation() -> None:
    r, n = spearman_rank([90.0, 80.0, 70.0], [1.0, 2.0, 3.0])
    assert abs(r - (-1.0)) < 1e-9
    assert n == 3


def test_spearman_zero_correlation() -> None:
    # ranks ortogonais: [1,2,3,4] vs [2,4,1,3] → r = 0 (Pearson exato)
    r, n = spearman_rank([10.0, 20.0, 30.0, 40.0], [2.0, 4.0, 1.0, 3.0])
    assert n == 4
    assert abs(r) < 1e-9


def test_spearman_with_ties_averages_ranks() -> None:
    # empate em total: ranks [1.5, 1.5, 3] vs [1, 2, 3] → r = 0.866...
    r, n = spearman_rank([50.0, 50.0, 90.0], [1.0, 2.0, 3.0])
    assert n == 3
    assert abs(r - 0.8660254037844387) < 1e-9


def test_score_correlation_annotated_csv(tmp_path: Path) -> None:
    """CSV sintético com correlação perfeita → spearman == 1.0."""
    rows = [
        _row(1, 90.0),
        _row(2, 80.0),
        _row(3, 70.0),
        _row(4, 60.0),
    ]
    path = tmp_path / "annotated.csv"
    write_score_csv(rows, path)
    # anota na ordem perfeita (melhor score = melhor avaliação)
    _annotate(path, [4.0, 3.0, 2.0, 1.0])

    out = score_correlation(path)
    assert out.ok is True
    assert out.n == 4
    assert out.spearman is not None and abs(out.spearman - 1.0) < 1e-9


def test_score_correlation_inverse(tmp_path: Path) -> None:
    rows = [_row(1, 90.0), _row(2, 80.0), _row(3, 70.0)]
    path = tmp_path / "inv.csv"
    write_score_csv(rows, path)
    _annotate(path, [1.0, 2.0, 3.0])

    out = score_correlation(path)
    assert out.ok is True
    assert out.spearman is not None and abs(out.spearman - (-1.0)) < 1e-9


def test_score_correlation_requires_at_least_two_annotated(tmp_path: Path) -> None:
    rows = [_row(1, 90.0), _row(2, 80.0)]
    path = tmp_path / "few.csv"
    write_score_csv(rows, path)
    # só 1 anotado
    _annotate(path, [4.0])

    out = score_correlation(path)
    assert out.ok is False
    assert out.error_code == "not_enough_annotated"


def test_score_correlation_ignores_unannotated_rows(tmp_path: Path) -> None:
    """Linhas sem avaliacao_manual nao entram no calculo."""
    rows = [_row(1, 90.0), _row(2, 80.0), _row(3, 70.0), _row(4, 60.0)]
    path = tmp_path / "partial.csv"
    write_score_csv(rows, path)
    # linha 1 (índice 0) fica sem anotação
    _annotate(path, [None, 4.0, 3.0, 2.0])

    out = score_correlation(path)
    assert out.ok is True
    assert out.n == 3  # só as anotadas


def test_write_score_csv_metrics_available_joined(tmp_path: Path) -> None:
    row = ScoreSampleRow(
        video_id=1,
        url="https://a",
        total=90.0,
        performance=None,
        velocity=12.5,
        engagement=None,
        relevance=45.0,
        recency=45.0,
        metrics_available=["views", "likes"],
    )
    out = tmp_path / "m.csv"
    write_score_csv([row], out)
    reloaded = read_score_csv(out)
    assert reloaded[0].metrics_available == ["views", "likes"]
    assert reloaded[0].performance is None  # vazio no CSV → None
    assert reloaded[0].velocity == 12.5


def test_score_correlation_accepts_raw_csv_path_and_str(tmp_path: Path) -> None:
    """Assinatura aceita Path (CLI) — smoke de tipagem/uso."""
    rows = [_row(1, 90.0), _row(2, 80.0)]
    path = tmp_path / "p.csv"
    write_score_csv(rows, path)
    _annotate(path, [5.0, 4.0])
    out = score_correlation(path)
    assert out.ok is True
    assert out.spearman is not None and abs(out.spearman - 1.0) < 1e-9


# checagem de que o CSV exportado tem coluna final pra anotação
def test_exported_csv_has_empty_annotation_column(tmp_path: Path) -> None:
    out = tmp_path / "e.csv"
    write_score_csv([_row(1, 90.0)], out)
    with out.open(newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
    assert reader[1][-1] == ""  # avaliacao_manual vazia
