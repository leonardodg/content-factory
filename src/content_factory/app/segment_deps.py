"""Bundle de deps por segmento (Task 10/11; movido de `ports` na Etapa 2 Task 13).

Fica fora de `app/ports.py` porque referencia os `*Deps` dos use-cases:
as portas não podem depender dos use-cases que as consomem (DIP).
"""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from content_factory.app.collect import CollectDeps
from content_factory.app.ports import UnitOfWork, WebScoreRepository
from content_factory.app.remediate import RemediationDeps
from content_factory.app.research_pipeline import ResearchDeps
from content_factory.app.score import ScoreDeps
from content_factory.app.score_v2 import ScoreV2Deps
from content_factory.domain.models import Segment


@dataclass
class SegmentDepsBundle:
    """Deps com escopo de 1 segmento, nascidas da mesma UoW (Task 10).

    Repositórios (`collect`/`score`/`research`) ligam a sessão da UoW;
    o resto (collectors, limiter, providers, cliente Redis) continua
    compartilhado — mas o `lock` é uma instância nova por bundle (o token de
    posse mora na instância; compartilhar a instância entre segmentos faria o
    release tardio de um apagar o lock do outro).
    O caller commita via `uow` (1 commit por segmento, no job).
    """

    uow: UnitOfWork
    collect: CollectDeps
    score: ScoreDeps
    research: ResearchDeps
    # F5: score web próprio (CLI sempre; worker só sob WEB_SCORE_ENABLED).
    web: WebScoreRepository
    # R7: remedição dos vídeos conhecidos, na MESMA UoW do collect.
    remediate: RemediationDeps
    # R9/R11: viral-v2 + hashtags, no score (transação própria, após o fase-h).
    score_v2: ScoreV2Deps


class SegmentDepsFactory(Protocol):
    """Fábrica de bundles por segmento a partir de UoWs próprias (Task 10/11).

    Implementada por `AppDeps` (worker). Cada chamada abre uma UoW própria;
    `async with factory.create_segment_deps(segment) as bundle` delimita a
    transação do segmento.
    """

    def create_segment_deps(
        self, segment: Segment
    ) -> AbstractAsyncContextManager[SegmentDepsBundle]: ...
