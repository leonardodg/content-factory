from content_factory.domain.term_ranking import (
    BOOTSTRAP_SEED,
    MIN_N_ORIGIN,
    MIN_N_TERM,
    TermSample,
    bootstrap_diff_ci,
    compare_origins,
    rank_terms,
)


def _s(term: str, origin: str, n: int, base: float, start: int = 0) -> list[TermSample]:
    return [TermSample(start + i, term, origin, base + i, float(i), 1.0 + i, 0.5) for i in range(n)]


def test_constantes_congeladas() -> None:
    assert (BOOTSTRAP_SEED, MIN_N_TERM, MIN_N_ORIGIN) == (20261016, 5, 30)


def test_mesmo_video_repetido_conta_uma_vez() -> None:
    repetido = [TermSample(1, "vela", "seed", 50.0, 1.0, 1.0, 0.5)] * 16
    out = rank_terms(repetido + _s("vela", "seed", 3, 60, start=100))
    assert out[0].n == 4 and out[0].verdict == "insufficient"


def test_rank_terms_ordena_e_insuficiente_vai_para_o_fim() -> None:
    out = rank_terms(
        _s("forte", "suggest", 10, 80)
        + _s("fraco", "suggest", 10, 1, 100)
        + _s("poucos", "seed", 3, 50, 200)
    )
    assert out[0].key == "forte" and out[-1].verdict == "insufficient"
    assert out[0].top10_fraction is not None and out[0].engagement_score is not None


def test_origem_melhor_pior_direcional_e_insuficiente() -> None:
    def verdict(x_base: float, seed_base: float, n_x: int = 40) -> str:
        samples = _s("x", "suggest", n_x, x_base) + _s("y", "seed", 40, seed_base, 1000)
        return compare_origins(samples)[0].verdict

    assert verdict(60, 10) == "better"
    assert verdict(0, 60) == "worse"
    assert verdict(10, 10) == "directional"
    assert verdict(90, 10, n_x=10) == "insufficient"  # n < 30


def test_sobreposicao_com_seed_e_reportada() -> None:
    out = compare_origins(_s("x", "suggest", 30, 60) + _s("y", "seed", 30, 60))
    assert out[0].overlap == 30


def test_bootstrap_reprodutivel() -> None:
    a, b = [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
    assert bootstrap_diff_ci(a, b) == bootstrap_diff_ci(a, b)
