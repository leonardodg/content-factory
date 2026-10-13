from datetime import datetime

from content_factory.domain.models import ScoreResult, VideoMetrics

WEIGHTS = {
    "performance": 0.25,
    "velocity": 0.20,
    "engagement": 0.15,
    "relevance": 0.20,
    "recency": 0.20,
}
FORMULA_VERSION = "fase-h"  # bump quando WEIGHTS ou a fórmula de um componente mudar

# MVP — calibrar com dados reais (roadmap Fase G/H): escala 0-100 sem
# calibração empírica ainda, igual ao performance (views/1M*100).
VELOCITY_VIEWS_PER_HOUR_CAP = 1000.0


def momentum_score(
    metrics: VideoMetrics,
    previous_metrics: VideoMetrics | None,
    captured_at: datetime | None,
    previous_captured_at: datetime | None,
) -> float | None:
    """Crescimento de `views` entre as duas capturas mais recentes, em views/hora.

    `None` quando não há par de capturas comparável: vídeo só tem uma
    captura, alguma delas não reportou `views`, ou o intervalo entre
    capturas não é positivo (relógio incoerente/mesma captura).
    """
    if previous_metrics is None or captured_at is None or previous_captured_at is None:
        return None
    if metrics.views is None or previous_metrics.views is None:
        return None
    delta_hours = (captured_at - previous_captured_at).total_seconds() / 3600
    if delta_hours <= 0:
        return None
    delta_views = metrics.views - previous_metrics.views
    if delta_views <= 0:
        return 0.0
    return min(delta_views / delta_hours / VELOCITY_VIEWS_PER_HOUR_CAP * 100, 100.0)


def engagement_rate(m: VideoMetrics) -> float | None:
    """Taxa de engajamento ponderada. `None` se views OU toda submétrica ausente.

    Cobertura parcial (ex.: YouTube não reporta shares/saves) soma só o que
    está disponível, SEM extrapolar pelo peso das submétricas ausentes —
    extrapolar mudaria a escala do número (ver REVISÃO: renormalizar por
    peso 1/2/3/4 infla ~3x um vídeo com só likes/comments conhecidos) e
    exigiria calibração com dados reais, fora de escopo desta fase
    (roadmap Fase G: "recalibração de pesos" pendente, não no escuro).
    """
    if m.views is None:
        return None  # métrica ausente — não é zero
    if m.views <= 0:
        return 0.0
    submetrics = (m.likes, m.comments, m.shares, m.saves)
    if all(v is None for v in submetrics):
        return None  # nenhum sinal de engajamento reportado — não é zero
    weighted = (m.likes or 0) * 1 + (m.comments or 0) * 2 + (m.shares or 0) * 3 + (m.saves or 0) * 4
    return round(weighted / m.views * 100, 2)


def trend_decay(published_at: datetime | None, now: datetime) -> str:
    if published_at is None:
        return "none"
    age_days = (now - published_at).days
    if age_days <= 7:
        return "low"
    if age_days <= 30:
        return "medium"
    return "high"


def viral_score(
    m: VideoMetrics,
    relevance: float,
    now: datetime,
    published_at: datetime | None,
    captured_at: datetime | None = None,
    previous_metrics: VideoMetrics | None = None,
    previous_captured_at: datetime | None = None,
) -> ScoreResult:
    # Fase G: métrica None = ausente → excluída da média (renormaliza pesos),
    # métrica 0 = reportada como zero → conta como zero de verdade.
    components: dict[str, float | None] = {}

    if m.views is not None:
        components["performance"] = round(min(m.views / 1_000_000 * 100, 100.0), 2)
    else:
        components["performance"] = None

    eng = engagement_rate(m)
    components["engagement"] = round(min(eng * 10, 100.0), 2) if eng is not None else None

    # Fase H: velocity mede crescimento real entre snapshots — sem par
    # comparável é None (excluído da média), nunca idade inventada.
    momentum = momentum_score(m, previous_metrics, captured_at, previous_captured_at)
    components["velocity"] = round(momentum, 2) if momentum is not None else None

    decay = trend_decay(published_at, now)
    components["recency"] = {"none": 50.0, "low": 100.0, "medium": 50.0, "high": 15.0}[decay]
    components["relevance"] = min(max(relevance, 0.0), 1.0) * 100

    # média ponderada SÓ dos componentes disponíveis (None excluído)
    available = {k: v for k, v in components.items() if v is not None}
    if available:
        weight_sum = sum(WEIGHTS[k] for k in available)
        total = round(sum(available[k] * WEIGHTS[k] for k in available) / weight_sum, 2)
    else:
        total = 0.0

    # zero reportado (não ausente) em todas as métricas E relevância zero → 0.0
    if m.views == 0 and m.likes == 0 and relevance == 0:
        total = 0.0

    # cobertura: quais métricas o snapshot tinha de verdade (auditável)
    metrics_available = [
        name
        for name, val in (
            ("views", m.views),
            ("likes", m.likes),
            ("comments", m.comments),
            ("shares", m.shares),
            ("saves", m.saves),
        )
        if val is not None
    ]

    return ScoreResult(
        total=total,
        performance=components["performance"],
        velocity=components["velocity"],  # já pode ser None — não força 0.0
        engagement=components["engagement"],
        relevance=components["relevance"] or 0.0,
        recency=components["recency"] or 0.0,
        trend_decay=decay,
        metrics_available=metrics_available,
    )
