from content_factory.domain.viral_v2 import (
    VIRAL_V2_VERSION,
    WEIGHTS_V2,
    ViralInput,
    percentile_ranks,
    score_population,
)


def _row(
    vid: int,
    *,
    platform: str = "youtube",
    velocity: float | None = 10.0,
    engagement: float | None = 5.0,
    views: int | None = 1000,
    followers: int | None = 10_000,
) -> ViralInput:
    return ViralInput(vid, platform, 1, velocity, engagement, views, followers)


def test_version_e_pesos_somam_1() -> None:
    assert VIRAL_V2_VERSION == "viral-v2"
    assert round(sum(WEIGHTS_V2.values()), 6) == 1.0


def test_percentis_pior_0_melhor_1_e_empate_na_media() -> None:
    assert percentile_ranks({1: 10.0, 2: 20.0, 3: 30.0}) == {1: 0.0, 2: 0.5, 3: 1.0}
    assert percentile_ranks({1: 5.0, 2: 5.0}) == {1: 0.5, 2: 0.5}


def test_populacao_pequena_nao_tem_total() -> None:
    assert all(s.total is None for s in score_population([_row(1), _row(2)]))


def test_min_population_parametrizavel() -> None:
    rows = [_row(i) for i in range(1, 4)]
    assert all(s.total is not None for s in score_population(rows, min_population=3))


def test_grupos_separados_por_plataforma() -> None:
    rows = [_row(i) for i in range(1, 7)] + [_row(i, platform="instagram") for i in range(10, 16)]
    out = score_population(rows)
    assert len(out) == 12 and all(s.population_n == 6 for s in out)


def test_maior_velocidade_maior_pontuacao_com_mesmo_resto() -> None:
    out = {
        s.video_id: s for s in score_population([_row(i, velocity=float(i)) for i in range(1, 8)])
    }
    assert (out[7].total or 0.0) > (out[1].total or 0.0)


def test_componente_ausente_renormaliza_pesos() -> None:
    rows = [_row(i) for i in range(1, 7)] + [_row(99, velocity=None)]
    out = {s.video_id: s for s in score_population(rows)}
    assert out[99].total is not None and out[99].velocity is None
    used = out[99].explanation["components_used"]
    assert isinstance(used, list) and "velocity" not in used


def test_velocidade_zero_e_valor_nao_ausencia() -> None:
    """QA real (17/10): `_log1p` devolvia None para 0 — o vídeo PARADO perdia o
    componente de velocidade, os pesos renormalizavam e ele podia pontuar
    acima de quem cresceu pouco. Zero é medida (o pior percentil), não falta."""
    rows = [_row(1, velocity=0.0), *(_row(i, velocity=float(i)) for i in range(2, 7))]
    out = {s.video_id: s for s in score_population(rows)}
    assert out[1].velocity == 0.0  # presente, no pior percentil
    used = out[1].explanation["components_used"]
    assert isinstance(used, list) and "velocity" in used
    # parado < quem cresceu, com o resto igual
    assert (out[1].total or 0.0) < (out[2].total or 0.0)
