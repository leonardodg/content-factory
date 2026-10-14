from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from content_factory.app.ports import ScoreRepository, ScoreUpsertRow, VideoRepository
from content_factory.domain.models import Segment, VideoSnapshot
from content_factory.domain.scoring import viral_score


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ScoreDeps:
    """Deps do score — @dataclass para permitir `dataclasses.replace` por segmento.

    Paridade com ResearchDeps/CollectDeps: o worker monta 1 por UoW trocando
    videos/scores/segment/keywords. Mesmos campos, mesma ordem e mesmo default
    do `__init__` anterior (construção posicional e nomeada preservadas).
    """

    videos: VideoRepository
    scores: ScoreRepository
    segment: Segment
    keywords: list[str]
    # default é o CHAMÁVEL (não default_factory — a factory seria chamada e
    # viraria datetime): mesma semântica do `lambda` do `__init__` anterior.
    clock: Callable[[], datetime] = _utcnow


class ScoreOutcome:
    def __init__(self, ok: bool, partial: bool, scored: int) -> None:
        self.ok = ok
        self.partial = partial
        self.scored = scored


def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower().removeprefix("#") in text)
    return hits / len(keywords)


async def score(deps: ScoreDeps) -> ScoreOutcome:
    now = deps.clock()
    snaps = await deps.videos.latest_for_segment(deps.segment.id)
    # fase pura: todos os ScoreResult calculados antes de qualquer I/O —
    # viral_score é CPU trivial (~20 µs/vídeo, Task 12), sem paralelismo.
    rows = [
        ScoreUpsertRow(
            video_id=snap.video_id,
            segment_id=deps.segment.id,
            result=viral_score(
                snap.metrics,
                relevance_of(snap, deps.keywords),
                now,
                snap.published_at,
                captured_at=snap.captured_at,
                previous_metrics=snap.previous_metrics,
                previous_captured_at=snap.previous_captured_at,
            ),
            computed_at=now,
        )
        for snap in snaps
    ]
    if not rows:
        return ScoreOutcome(ok=True, partial=False, scored=0)
    # 1 round-trip por bloco (não por vídeo); linhas ruins são isoladas
    # dentro do repo — o retorno diz quantas persistiram. Falha do lote
    # inteiro (ex.: conexão) PROPAGA (Etapa 1 Task 11): engolir aqui fazia o
    # commit seguinte levantar PendingRollbackError e o erro real se perdia;
    # o worker loga com traceback e reverte só a UoW do segmento.
    scored = await deps.scores.upsert_many(rows)
    errors = len(rows) - scored
    ok = errors == 0
    return ScoreOutcome(ok=ok, partial=(not ok and scored > 0), scored=scored)
