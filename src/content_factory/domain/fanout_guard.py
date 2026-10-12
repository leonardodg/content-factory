from content_factory.domain.models import DepthConfig, KeywordSignal


class FanoutGuard:
    """Guard-rail anti-explosão combinatória de um ciclo de pesquisa.

    Três camadas: dedupe global do ciclo, cap duro de nós visitados,
    cap de fan-out por etapa (pelos de maior peso). Instância NOVA por
    ciclo — não persiste entre execuções (tendências mudam dia a dia).
    """

    def __init__(self, cfg: DepthConfig) -> None:
        self._cfg = cfg
        self._seen: set[str] = set()
        self._visited_count = 0

    def admit(self, keyword: str, depth: int) -> bool:
        if depth > self._cfg.max_depth:
            return False
        if self._visited_count >= self._cfg.max_nodes_per_cycle:
            return False
        key = keyword.casefold().strip()
        if key in self._seen:
            return False
        self._seen.add(key)
        self._visited_count += 1
        return True

    def truncate(self, signals: list[KeywordSignal], stage: str) -> list[KeywordSignal]:
        limit = self._cfg.fanout_per_stage.get(stage)
        if limit is None:
            return list(signals)
        return sorted(signals, key=lambda s: s.weight, reverse=True)[:limit]
