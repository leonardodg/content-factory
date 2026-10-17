from datetime import UTC, datetime, timedelta

from content_factory.domain.models import TrendPoint
from content_factory.domain.trends_rank import (
    RATIO_CAP,
    TRENDS_RANK_VERSION,
    plan_trend_batches,
    relative_interest,
    trend_score,
)

DAY = datetime(2026, 10, 20, tzinfo=UTC)


def _daily(kw: str, values: list[float]) -> list[TrendPoint]:
    """Série diária terminando em DAY (último valor = DAY)."""
    n = len(values)
    return [
        TrendPoint(
            keyword=kw,
            period_start=DAY - timedelta(days=n - 1 - i),
            period_granularity="day",
            interest=v,
        )
        for i, v in enumerate(values)
    ]


def test_version() -> None:
    assert TRENDS_RANK_VERSION == "trends-anchor-v1"


def test_lotes_de_4_com_ancora_fixa_e_sem_a_propria_ancora() -> None:
    batches = plan_trend_batches([f"t{i}" for i in range(18)] + ["Vela"], "vela")
    assert len(batches) == 4 and all(len(b.terms) == 4 for b in batches)
    assert all(b.anchor == "vela" for b in batches)


def test_razao_estavel_sem_inclinacao() -> None:
    assert relative_interest(_daily("x", [50] * 28), _daily("a", [100] * 28)) == (0.5, 0.0)


def test_ancora_zero_devolve_none() -> None:
    assert relative_interest(_daily("x", [10, 20]), _daily("a", [0, 0])) == (None, None)
    assert trend_score(None, None) is None


def test_inclinacao_7d_contra_21d_anteriores_limitada() -> None:
    mean, slope = relative_interest(_daily("x", [10] * 21 + [40] * 7), _daily("a", [100] * 28))
    assert mean == 0.4 and slope == 1.0  # quadruplicou → teto 1


def test_razao_tem_teto() -> None:
    mean, _ = relative_interest(_daily("x", [100] * 7), _daily("a", [1] * 7))
    assert mean == RATIO_CAP


def test_alinha_por_data_nao_por_indice() -> None:
    # a âncora não tem o 1º dia: só os 2 dias em comum entram
    mean, _ = relative_interest(_daily("x", [50, 50, 50]), _daily("a", [100, 100]))
    assert mean == 0.5


def test_trend_score_no_intervalo_unitario_e_monotono() -> None:
    assert 0.0 <= (trend_score(RATIO_CAP, 1.0) or 0.0) <= 1.0
    assert (trend_score(0.5, 1.0) or 0.0) > (trend_score(0.5, 0.0) or 0.0)
