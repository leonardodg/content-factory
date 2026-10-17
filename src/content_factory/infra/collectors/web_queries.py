"""Adaptador por segmento do protocolo `q1` (padrão do SearchBoundYoutube).

Lê `segments/web-queries/<slug>.q1.yaml`, congela as consultas e devolve as
`WEB_QUERIES_PER_RUN` do run atual — a rotação é derivada da data/hora
(4 coletas/dia, um slot por coleta), sem estado: reproduzível entre processos.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import yaml

from content_factory.domain.models import SegmentConfig
from content_factory.domain.web_queries import (
    QUERY_PROTOCOL_VERSION,
    WEB_QUERIES_PER_RUN,
    PlannedQuery,
    WebQueryProtocol,
    plan_web_queries,
    protocol_from_dict,
    run_index_for,
)

logger = logging.getLogger(__name__)

DEFAULT_QUERIES_DIR = Path("segments/web-queries")


def protocol_path(directory: Path, slug: str) -> Path:
    return directory / f"{slug}.{QUERY_PROTOCOL_VERSION}.yaml"


def load_protocol(path: Path) -> WebQueryProtocol:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # sintaxe quebrada não é ValueError: sem isto escapava do `_protocol`
        # e derrubava a coleta web inteira (revisão externa 3.4)
        raise ValueError(f"YAML com sintaxe inválida em {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"protocolo inválido em {path}")
    return protocol_from_dict(cast("dict[str, object]", data))


class FileWebQueries:
    """`WebQuerySource` que lê o YAML `q1` do segmento.

    Cache por (`st_mtime_ns`, tamanho): só o mtime em segundos deixava passar
    uma troca do arquivo dentro da resolução do filesystem (revisão 3.5).
    """

    def __init__(
        self,
        directory: Path = DEFAULT_QUERIES_DIR,
        *,
        per_run: int = WEB_QUERIES_PER_RUN,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._dir = directory
        self._per_run = per_run
        self._clock = clock
        self._cache: dict[str, tuple[tuple[int, int], WebQueryProtocol]] = {}

    def _protocol(self, slug: str) -> WebQueryProtocol | None:
        path = protocol_path(self._dir, slug)
        try:
            stat = path.stat()
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(slug)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            protocol = load_protocol(path)
        except ValueError:
            logger.warning("protocolo web inválido — %s", path, exc_info=True)
            return None
        self._cache[slug] = (signature, protocol)
        return protocol

    def queries_for(self, segment: SegmentConfig) -> list[PlannedQuery] | None:
        protocol = self._protocol(segment.slug)
        if protocol is None or not protocol.queries:
            return None
        return plan_web_queries(protocol.queries, run_index_for(self._clock()), self._per_run)
