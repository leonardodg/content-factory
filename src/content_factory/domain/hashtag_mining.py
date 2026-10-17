"""Mineração de hashtags (R11) — pura.

Fonte: só os vídeos do segmento no TOP 25% do `viral-v2`. Peso = soma do
`viral-v2` dos vídeos que usam a tag (frequência PONDERADA). Passa pelo R1
em modo hashtag (`filter_hashtag`: âncora/bloqueio por substring, sem idioma).
Só as `TOP_K` melhores por plataforma ficam ativas no pool.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from content_factory.domain.term_filter import TermFilterConfig, filter_hashtag

HASHTAG_MINE_VERSION = "hashtag-mine-v1"
TOP_FRACTION = 0.25
TOP_K = 10
_HASHTAG_RE = re.compile(r"#(\w{2,50})", re.UNICODE)


@dataclass(frozen=True)
class HashtagSource:
    video_id: int
    platform: str
    viral_v2: float | None
    raw: Mapping[str, object]
    title: str = ""
    description: str = ""


@dataclass(frozen=True)
class MinedHashtag:
    tag: str
    platform: str
    weight: float
    videos: int


def _snippet_tags(raw: Mapping[str, object]) -> list[str]:
    snippet = raw.get("snippet")
    if not isinstance(snippet, Mapping):
        return []
    tags = cast(Mapping[str, object], snippet).get("tags")
    if not isinstance(tags, list):
        return []
    return [str(t) for t in cast(list[object], tags)]


def extract_hashtags(source: HashtagSource) -> list[str]:
    """`snippet.tags` (sem espaço) + `#tags` do título/descrição, minúsculas e sem `#`."""
    tags = [t.casefold().replace(" ", "") for t in _snippet_tags(source.raw)]
    for text in (source.title, source.description):
        tags += [m.casefold() for m in _HASHTAG_RE.findall(text)]
    return list(dict.fromkeys(t for t in tags if t))


def mine(sources: Sequence[HashtagSource], config: TermFilterConfig) -> list[MinedHashtag]:
    scored = [s for s in sources if s.viral_v2 is not None]
    if not scored:
        return []
    values = sorted(s.viral_v2 for s in scored if s.viral_v2 is not None)
    cutoff = values[int((1 - TOP_FRACTION) * len(values))]
    weight: dict[tuple[str, str], float] = {}
    videos: dict[tuple[str, str], int] = {}
    for source in scored:
        if source.viral_v2 is None or source.viral_v2 < cutoff:
            continue
        for tag in extract_hashtags(source):
            if not filter_hashtag(tag, config).accepted:
                continue
            key = (source.platform, tag)
            weight[key] = weight.get(key, 0.0) + source.viral_v2
            videos[key] = videos.get(key, 0) + 1
    ranked = sorted(
        (
            MinedHashtag(tag=t, platform=p, weight=round(w, 6), videos=videos[(p, t)])
            for (p, t), w in weight.items()
        ),
        key=lambda h: (h.platform, -h.weight, h.tag),
    )
    out: list[MinedHashtag] = []
    per_platform: dict[str, int] = {}
    for h in ranked:
        if per_platform.get(h.platform, 0) < TOP_K:
            out.append(h)
            per_platform[h.platform] = per_platform.get(h.platform, 0) + 1
    return out
