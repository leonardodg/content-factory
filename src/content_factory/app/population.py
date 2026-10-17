"""R9 (Bloco 5 Task 6): população no corte → entradas do `viral-v2`.

Compartilhado pelo score do worker (`score_v2`) e pelo relatório no corte
(`term_report`) — a MESMA regra de relevância, velocidade e engajamento nos
dois, senão o relatório não reproduz o score.

- **Relevância vira filtro** (R9), não peso: idioma do vídeo (regra única
  `language_matches`, pt-BR: `pt-PT` sai), sem bloqueio no título e âncora no
  TÍTULO (palavra inteira) ou numa TAG (substring, `filter_hashtag`). A
  descrição não entra: descrição do YouTube tem inglês de rodapé ("Follow me
  ... for more") e derrubava vídeo PT (revisão QA).
- **Velocidade** = Δviews/Δh entre a última medição e a anterior com ≥ 1 h
  (o par já vem do SQL da população); sem par → `None`; queda → 0.
- **Engajamento** = `scoring.engagement_rate`, a mesma fórmula do fase-h.
"""

from collections.abc import Sequence

from content_factory.domain.language import language_matches, wanted_language
from content_factory.domain.models import PopulationRow, Segment, VideoMetrics
from content_factory.domain.scoring import engagement_rate
from content_factory.domain.term_filter import TermFilterConfig, filter_hashtag
from content_factory.domain.text import contains_phrase, word_tokens
from content_factory.domain.viral_v2 import ViralInput


def is_relevant(row: PopulationRow, segment: Segment) -> bool:
    if not language_matches(row.language, wanted_language(segment.search)):
        return False
    title = word_tokens(row.title)
    if any(contains_phrase(title, b) for b in segment.blocked_terms):
        return False
    if not segment.anchor_terms:
        return True
    if any(contains_phrase(title, a) for a in segment.anchor_terms):
        return True
    cfg = TermFilterConfig(
        anchors=tuple(segment.anchor_terms), blocked=tuple(segment.blocked_terms)
    )
    return any(filter_hashtag(tag, cfg).accepted for tag in row.tags)


def _velocity_per_hour(row: PopulationRow) -> float | None:
    if row.views is None or row.prev_views is None or row.prev_captured_at is None:
        return None
    hours = (row.captured_at - row.prev_captured_at).total_seconds() / 3600
    if hours <= 0:
        return None
    return max(row.views - row.prev_views, 0) / hours


def to_viral_input(row: PopulationRow, segment_id: int) -> ViralInput:
    metrics = VideoMetrics(
        views=row.views,
        likes=row.likes,
        comments=row.comments,
        shares=row.shares,
        saves=row.saves,
    )
    return ViralInput(
        video_id=row.video_id,
        platform=row.platform,
        segment_id=segment_id,
        velocity_per_hour=_velocity_per_hour(row),
        engagement_rate=engagement_rate(metrics),
        views=row.views,
        channel_followers=row.followers,
    )


def build_population(
    rows: Sequence[PopulationRow], segment: Segment
) -> tuple[list[ViralInput], int]:
    """(entradas relevantes, quantos saíram pela relevância)."""
    kept = [to_viral_input(r, segment.id) for r in rows if is_relevant(r, segment)]
    return kept, len(rows) - len(kept)
