"""F4 `app.web_eval`: avaliação pareada + relatório com IC e veredito."""

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.stubs import FakeWebScores

from content_factory.app.web_eval import (
    ANNOTATION_HEADER,
    InvalidAnnotation,
    read_annotations,
    web_evaluate,
)
from content_factory.domain.models import Segment
from content_factory.domain.web_eval import WebAnnotation
from content_factory.domain.web_relevance import WebPageInput

AS_OF = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
SEG = Segment(id=1, slug="velas", name="Velas", platforms=["web"], keywords=["vela"])


def _page(vid: int, query_intent: str = "aprender") -> WebPageInput:
    return WebPageInput(
        video_id=vid,
        title=f"Vela artesanal tecnica {vid}",
        snippet="snippet de vela artesanal com detalhe",
        keywords=list(SEG.keywords),
        canonical_url=f"https://site{vid}.example/p/{vid}",
        canonical_ok=True,
        query_intent=query_intent,
    )


async def test_web_evaluate_pairing_coverage_and_verdict() -> None:
    pages = [_page(i) for i in range(1, 21)]
    repo = FakeWebScores(pages)
    annotations = {
        p.canonical_url: WebAnnotation(item_id=p.canonical_url, tema=2, intencao=1, utilidade=1)
        for p in pages
    }
    # 1 legítima descartada (estrato) → coverage < 1
    annotations["https://descartado.example/x"] = WebAnnotation(
        item_id="https://descartado.example/x", tema=1, intencao=0, utilidade=0
    )

    report = await web_evaluate(SEG, repo, annotations, as_of=AS_OF)

    assert report.kept_annotated == 20
    assert report.verdict == "insuficiente"  # 20 < 40 itens anotados
    assert report.p10.n == 20 and report.gain_p10.n == 20
    # as duas funções ordenam exatamente o mesmo conjunto (pareado)
    assert set(report.ranking_v1) == set(report.ranking_base)
    assert len(report.ranking_v1) == 20
    assert report.coverage == 20 / 21


async def test_web_evaluate_excludes_unannotated_pages() -> None:
    pages = [_page(i) for i in range(1, 4)]
    repo = FakeWebScores(pages)
    # só 2 dos 3 anotados; o ranking pareado cobre só os anotados
    annotations = {
        pages[0].canonical_url: WebAnnotation(
            item_id=pages[0].canonical_url, tema=2, intencao=2, utilidade=2
        ),
        pages[1].canonical_url: WebAnnotation(
            item_id=pages[1].canonical_url, tema=0, intencao=0, utilidade=0
        ),
    }

    report = await web_evaluate(SEG, repo, annotations, as_of=AS_OF)

    assert report.kept_annotated == 2
    assert set(report.ranking_v1) == {pages[0].canonical_url, pages[1].canonical_url}


def test_read_annotations_skips_unlabelled_rows(tmp_path: Path) -> None:
    path = tmp_path / "anotado.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ANNOTATION_HEADER)
        writer.writerow(["https://a.example", "2", "1", "1", "", "ok"])
        writer.writerow(["https://b.example", "", "", "", "", ""])  # sem tema: ignorada
        writer.writerow(["", "2", "2", "2", "", ""])  # sem item_id: ignorada

    annotations = read_annotations(path)

    assert set(annotations) == {"https://a.example"}
    assert annotations["https://a.example"].tema == 2
    assert annotations["https://a.example"].relevant is True


def test_read_annotations_tolerates_non_integer_labels(tmp_path: Path) -> None:
    path = tmp_path / "anotado.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ANNOTATION_HEADER)
        writer.writerow(["https://a.example", "2", "1.0", "dois", "", "ok"])

    annotations = read_annotations(path)

    a = annotations["https://a.example"]
    assert (a.tema, a.intencao, a.utilidade) == (2, 0, 0)


async def test_web_evaluate_ranks_same_items_even_with_repeated_titles() -> None:
    """Pareado: título repetido não pode tirar item de um lado só."""
    pages = [_page(i) for i in range(1, 13)]
    twin = WebPageInput(
        video_id=99,
        title=pages[0].title,  # mesmo título da página 1, outro site
        snippet="outro texto qualquer",
        keywords=list(SEG.keywords),
        canonical_url="https://gemeo.example/p",
        canonical_ok=True,
        query_intent="aprender",
    )
    pages.append(twin)
    annotations = {
        p.canonical_url: WebAnnotation(item_id=p.canonical_url, tema=1, intencao=1, utilidade=1)
        for p in pages
    }

    report = await web_evaluate(SEG, FakeWebScores(pages), annotations, as_of=AS_OF)

    assert report.kept_annotated == 13
    assert sorted(report.ranking_v1) == sorted(report.ranking_base)
    assert len(report.ranking_v1) == 13


async def test_web_evaluate_reads_data_as_of_the_sample() -> None:
    repo = FakeWebScores([_page(1)])
    await web_evaluate(SEG, repo, {}, as_of=AS_OF)
    assert repo.until_asked and set(repo.until_asked) == {AS_OF}


def _write(path: Path, invalido: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ANNOTATION_HEADER)
        writer.writerow(["https://a.example", "2", "1", "1", invalido, ""])


@pytest.mark.parametrize("cell", ["", "0", "nao", "Não", "false", " "])
def test_read_annotations_valid_markers_mean_not_invalid(tmp_path: Path, cell: str) -> None:
    path = tmp_path / "a.csv"
    _write(path, cell)
    assert read_annotations(path)["https://a.example"].invalido is None


@pytest.mark.parametrize("cell", ["anuncio", "Anúncio", "vazia", "duplicata", "inacessível"])
def test_read_annotations_accepts_known_invalid_markers(tmp_path: Path, cell: str) -> None:
    path = tmp_path / "a.csv"
    _write(path, cell)
    ann = read_annotations(path)["https://a.example"]
    assert ann.invalido is not None and ann.relevant is False


def test_read_annotations_rejects_unknown_invalid_marker(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    _write(path, "anucio")  # erro de digitação não pode virar "inválido" calado
    with pytest.raises(InvalidAnnotation, match="anucio"):
        read_annotations(path)
