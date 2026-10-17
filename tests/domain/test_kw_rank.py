from content_factory.domain.kw_rank import KW_RANK_VERSION, SuggestHit, rank_suggestions


def test_version() -> None:
    assert KW_RANK_VERSION == "kw-rank-v1"


def test_posicao_1_vale_mais_que_posicao_10() -> None:
    top = rank_suggestions([SuggestHit("vela", "velas", 1)], {})
    low = rank_suggestions([SuggestHit("vela", "velas", 10)], {})
    assert top["vela"] > low["vela"]


def test_recorrencia_entre_sementes_soma() -> None:
    uma = rank_suggestions([SuggestHit("vela", "s1", 1)], {})
    tres = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3")], {})
    assert tres["vela"] > uma["vela"]


def test_recorrencia_satura_no_cap() -> None:
    tres = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3")], {})
    cinco = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3", "s4", "s5")], {})
    assert tres["vela"] == cinco["vela"]


def test_persistencia_entre_dias_soma() -> None:
    hoje = rank_suggestions([SuggestHit("vela", "s1", 1)], {"vela": 1})
    semana = rank_suggestions([SuggestHit("vela", "s1", 1)], {"vela": 7})
    assert semana["vela"] > hoje["vela"]


def test_peso_sempre_no_intervalo_unitario() -> None:
    hits = [SuggestHit("vela", "s1", 1), SuggestHit("vela", "s2", 2)]
    peso = rank_suggestions(hits, {"vela": 7})["vela"]
    assert 0.0 <= peso <= 1.0


def test_termo_sem_dias_conhecidos_assume_1() -> None:
    assert rank_suggestions([SuggestHit("vela", "s1", 1)], {})["vela"] > 0
