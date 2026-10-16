"""F4 — avaliação pareada `base-fase-h` x `web-v1` com IC e veredito.

As duas funções ordenam o MESMO conjunto: os itens `kept` anotados do
segmento (exclui o estrato de descartados). O empate é desfeito por
`video_id`. Toda métrica sai com `n` e IC 95%.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from content_factory.app.ports import WebScoreRepository
from content_factory.app.web_rank import rank_baseline
from content_factory.domain.errors import DomainError
from content_factory.domain.models import Segment
from content_factory.domain.text import normalize
from content_factory.domain.web_eval import (
    INVALID_MARKERS,
    Interval,
    WebAnnotation,
    filter_coverage,
    paired_gain_interval,
    precision_interval,
    spearman,
    verdict,
)
from content_factory.domain.web_queries import INTENT_ORDER
from content_factory.domain.web_relevance import (
    WebPageInput,
    WebScore,
    web_score,
)

ANNOTATION_HEADER = ["item_id", "tema", "intencao", "utilidade", "invalido", "motivo"]

# célula de `invalido` que significa "NÃO é inválido" (o anotador escreveu algo
# para dizer "válido"); comparado após `normalize`
_NOT_INVALID = frozenset({"", "0", "nao", "n", "no", "false", "valido", "ok"})


class InvalidAnnotation(DomainError):
    """Célula de anotação fora do protocolo (erro de digitação, marcador novo)."""


def _invalid_marker(cell: str | None, item_id: str) -> str | None:
    """Marcador de inválido do protocolo, `None` (válido) ou erro.

    Só os marcadores de `INVALID_MARKERS` invalidam o item; "0"/"não"/vazio
    dizem que ele é válido. Qualquer outra coisa (ex.: "anucio") é rejeitada
    em vez de virar "inválido" calado e mudar o veredito.
    """
    norm = normalize(cell or "")
    if norm in _NOT_INVALID:
        return None
    if norm in INVALID_MARKERS:
        return norm
    raise InvalidAnnotation(
        f"invalido={cell!r} em {item_id}: use vazio ou um de {', '.join(INVALID_MARKERS)}"
    )


@dataclass
class WebEvalReport:
    ok: bool
    segment: str
    kept_annotated: int
    p5: Interval
    p10: Interval
    gain_p10: Interval
    coverage: float | None
    verdict: str
    correlation: float | None
    useful_top10: float | None
    ranking_v1: list[str]
    ranking_base: list[str]


def _label_int(value: str | None, *, default: int = 0) -> int:
    """Inteiro do rótulo; célula vazia ou não-numérica cai no `default`."""
    text = (value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def read_annotations(path: Path) -> dict[str, WebAnnotation]:
    """Lê o CSV anotado (item_id + rótulos). Linhas sem rótulo são ignoradas."""
    annotations: dict[str, WebAnnotation] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            item_id = (rec.get("item_id") or "").strip()
            tema = (rec.get("tema") or "").strip()
            if not item_id or tema == "":
                continue
            invalido = _invalid_marker(rec.get("invalido"), item_id)
            annotations[item_id] = WebAnnotation(
                item_id=item_id,
                tema=_label_int(tema),
                intencao=_label_int(rec.get("intencao")),
                utilidade=_label_int(rec.get("utilidade")),
                invalido=invalido,
                motivo=(rec.get("motivo") or "").strip(),
            )
    return annotations


def _ordered_videos(by_video: dict[int, WebScore], videos: list[int]) -> list[WebScore]:
    ordered = sorted(
        (by_video[v] for v in videos if v in by_video),
        key=lambda s: (-(s.score if s.score is not None else -1e9), s.video_id),
    )
    return ordered


async def web_evaluate(
    segment: Segment,
    repo: WebScoreRepository,
    annotations: dict[str, WebAnnotation],
    *,
    as_of: datetime,
) -> WebEvalReport:
    """Avaliação pareada com os dados como estavam em `as_of` (data da amostra).

    Sem `dedupe_titles` aqui: ele escolhe o representante pelo próprio score,
    então cada função ficaria com páginas diferentes. As duas ordenam
    exatamente os mesmos itens anotados.
    """
    pages = await repo.pages_for_segment(segment.id, list(segment.keywords), until=as_of)
    by_url = {p.canonical_url: p for p in pages}
    url_by_video = {p.video_id: p.canonical_url for p in pages}
    v1_by_video = {p.video_id: web_score(p, now=as_of) for p in pages}
    base_by_video = {s.video_id: s for s in rank_baseline(pages)}

    # só os itens `kept` anotados (o estrato de descartados não entra na comparação)
    annotated_kept = [item for item in annotations if item in by_url]
    kept_videos = [by_url[item].video_id for item in annotated_kept]
    relevant = {item: ann.relevant for item, ann in annotations.items()}

    v1_ids = [url_by_video[s.video_id] for s in _ordered_videos(v1_by_video, kept_videos)]
    base_ids = [url_by_video[s.video_id] for s in _ordered_videos(base_by_video, kept_videos)]

    p5 = precision_interval(v1_ids, relevant, 5)
    p10 = precision_interval(v1_ids, relevant, 10)
    gain = paired_gain_interval(base_ids, v1_ids, relevant, 10)

    kept_legit = sum(1 for item in annotated_kept if annotations[item].legitimate)
    discarded_legit = sum(
        1 for item in annotations if item not in by_url and annotations[item].legitimate
    )
    coverage = filter_coverage(kept_legit, discarded_legit)

    intents = {by_url[item].query_intent for item in annotated_kept}
    intents_without_data = len(annotated_kept) >= 40 and any(
        intent not in intents for intent in INTENT_ORDER
    )

    correlation = _correlation(v1_by_video, by_url, annotations)
    useful_top10 = _useful_top10(v1_ids, annotations)

    result = verdict(
        kept_annotated=len(annotated_kept),
        p5=p5,
        p10=p10,
        gain_p10=gain,
        coverage=coverage,
        intents_without_data=intents_without_data,
    )
    return WebEvalReport(
        ok=True,
        segment=segment.slug,
        kept_annotated=len(annotated_kept),
        p5=p5,
        p10=p10,
        gain_p10=gain,
        coverage=coverage,
        verdict=result,
        correlation=correlation,
        useful_top10=useful_top10,
        ranking_v1=v1_ids,
        ranking_base=base_ids,
    )


def _correlation(
    by_video: dict[int, WebScore],
    by_url: dict[str, WebPageInput],
    annotations: dict[str, WebAnnotation],
) -> float | None:
    scores: list[float] = []
    labels: list[float] = []
    for item, ann in annotations.items():
        page = by_url.get(item)
        if page is None:
            continue
        score = by_video.get(page.video_id)
        if score is None or score.score is None:
            continue
        scores.append(score.score)
        labels.append(float(ann.tema + ann.intencao + ann.utilidade))
    if len(scores) < 2:
        return None
    return spearman(scores, labels)


def _useful_top10(ranking: list[str], annotations: dict[str, WebAnnotation]) -> float | None:
    top = ranking[:10]
    if len(top) < 10:
        return None
    useful = sum(
        1 for item in top if annotations.get(item) is not None and annotations[item].useful
    )
    return useful / 10
