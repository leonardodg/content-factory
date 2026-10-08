from pathlib import Path
from typing import Any

import yaml

from content_factory.app.ports import SegmentRepository
from content_factory.domain.errors import DomainError
from content_factory.domain.models import Segment, SegmentConfig


def load_segment_file(path: Path) -> SegmentConfig:
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return SegmentConfig.model_validate(data)
    except Exception as exc:  # ValidationError | yaml error
        raise DomainError(f"segmento inválido em {path}: {exc}") from exc


async def upsert_segment(repo: SegmentRepository, path: Path) -> Segment:
    return await repo.upsert(load_segment_file(path))
