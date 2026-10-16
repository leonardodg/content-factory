"""F4 `app.web_eval`: avaliação pareada + relatório com IC e veredito."""

import csv
from pathlib import Path

from tests.stubs import FakeWebScores

from content_factory.app.web_eval import ANNOTATION_HEADER, read_annotations, web_evaluate
from content_factory.domain.models import Segment
from content_factory.domain.web_eval import WebAnnotation
from content_factory.domain.web_relevance import WebPageInput

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

    report = await web_evaluate(SEG, repo, annotations)

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

    report = await web_evaluate(SEG, repo, annotations)

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
