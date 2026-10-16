from content_factory.domain.web_eval import (
    Interval,
    WebAnnotation,
    filter_coverage,
    paired_gain_interval,
    precision_at_k,
    precision_interval,
    spearman,
    verdict,
)


def ann(item: str, tema: int, utilidade: int = 0, invalido: str | None = None) -> WebAnnotation:
    return WebAnnotation(
        item_id=item, tema=tema, intencao=1, utilidade=utilidade, invalido=invalido
    )


def test_annotation_positive_rules() -> None:
    assert ann("a", 1, 0).relevant is True
    assert ann("a", 0, 2).relevant is False
    assert ann("a", 2, 2, invalido="anuncio").relevant is False
    assert ann("a", 2, 2, invalido="inacessivel").legitimate is False
    assert ann("a", 1, 0).useful is False
    assert ann("a", 0, 1).useful is True


def test_precision_at_k() -> None:
    ranking = ["a", "b", "c", "d", "e"]
    rel = {"a": True, "b": False, "c": True, "d": True, "e": False}
    assert precision_at_k(ranking, rel, 5) == 0.6
    assert precision_at_k(ranking, rel, 2) == 0.5


def test_precision_at_k_insufficient() -> None:
    assert precision_at_k(["a"], {"a": True}, 5) is None


def test_precision_interval_bounds_point() -> None:
    ranking = [f"i{i}" for i in range(20)]
    rel = {f"i{i}": i < 10 for i in range(20)}
    iv = precision_interval(ranking, rel, 10, resamples=200)
    point, low, high = iv.point, iv.low, iv.high
    assert point == 1.0
    assert point is not None and low is not None and high is not None
    assert low <= point <= high


def test_paired_gain_interval_positive_when_v1_better() -> None:
    pool = [f"i{i}" for i in range(20)]
    rel = {f"i{i}": i < 10 for i in range(20)}
    base = list(reversed(pool))  # P@10 = 0 (não-relevantes na frente)
    v1 = list(pool)  # P@10 = 1 (relevantes i<10 na frente)
    iv = paired_gain_interval(base, v1, rel, 10, resamples=300)
    assert iv.point is not None and iv.point > 0
    assert iv.low is not None and iv.low > 0


def test_filter_coverage() -> None:
    assert filter_coverage(90, 10) == 0.9
    assert filter_coverage(0, 0) is None


def test_spearman_monotonic() -> None:
    import pytest

    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def ok_intervals() -> dict[str, Interval]:
    return {
        "p5": Interval(0.8, 0.6, 0.95, 50),
        "p10": Interval(0.7, 0.5, 0.9, 50),
        "gain": Interval(0.2, 0.05, 0.35, 50),
    }


def test_verdict_aprovado() -> None:
    iv = ok_intervals()
    assert (
        verdict(
            kept_annotated=50,
            p5=iv["p5"],
            p10=iv["p10"],
            gain_p10=iv["gain"],
            coverage=0.95,
        )
        == "aprovado"
    )


def test_verdict_direcional_when_ci_includes_zero() -> None:
    iv = ok_intervals()
    assert (
        verdict(
            kept_annotated=50,
            p5=iv["p5"],
            p10=iv["p10"],
            gain_p10=Interval(0.2, -0.1, 0.4, 50),
            coverage=0.95,
        )
        == "direcional"
    )


def test_verdict_nao_atingido() -> None:
    iv = ok_intervals()
    assert (
        verdict(
            kept_annotated=50,
            p5=Interval(0.4, 0.2, 0.6, 50),
            p10=iv["p10"],
            gain_p10=iv["gain"],
            coverage=0.95,
        )
        == "nao_atingido"
    )


def test_verdict_insuficiente() -> None:
    iv = ok_intervals()
    assert (
        verdict(
            kept_annotated=10,
            p5=iv["p5"],
            p10=iv["p10"],
            gain_p10=iv["gain"],
            coverage=0.95,
        )
        == "insuficiente"
    )
