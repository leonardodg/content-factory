from datetime import UTC, datetime

from content_factory.domain.search_plan import (
    SEARCH_PLAN_VERSION,
    PoolSnapshot,
    plan_queries,
    run_index_for,
    searches_per_collect,
)

POOL = PoolSnapshot(
    seeds=("a", "b", "c"),
    suggest_by_rank=("s1", "s2", "s3"),
    suggest_by_trend=("s2", "s9"),
    hashtags=("velasdecor",),
)


def test_uma_vaga_por_origem_e_hashtag_com_cerquilha() -> None:
    qs = plan_queries(POOL, "youtube", run_index=0, searches=4)
    assert [(q.origin, q.term) for q in qs] == [
        ("seed", "a"),
        ("suggest", "s1"),
        ("trends", "s2"),
        ("hashtag:youtube", "#velasdecor"),
    ]
    assert len({q.query_id for q in qs}) == 4
    assert all(q.query_id.startswith(f"{SEARCH_PLAN_VERSION}:0:") for q in qs)


def test_vaga_vazia_vai_para_suggest_e_e_gravada_como_suggest() -> None:
    pool = PoolSnapshot(seeds=("a",), suggest_by_rank=("s1", "s2", "s3"))
    qs = plan_queries(pool, "youtube", run_index=0, searches=4)
    assert [q.origin for q in qs] == ["seed", "suggest", "suggest", "suggest"]
    assert len({q.term for q in qs}) == 4


def test_trends_nunca_repete_o_termo_da_vaga_suggest() -> None:
    pool = PoolSnapshot(seeds=("a",), suggest_by_rank=("s1",), suggest_by_trend=("s1", "t2"))
    qs = plan_queries(pool, "youtube", run_index=0, searches=3)
    assert [(q.origin, q.term) for q in qs] == [("seed", "a"), ("suggest", "s1"), ("trends", "t2")]


def test_deterministico_e_rodizio_varia_com_o_indice() -> None:
    assert plan_queries(POOL, "youtube", 5, 4) == plan_queries(POOL, "youtube", 5, 4)
    seed_of = [plan_queries(POOL, "youtube", i, 1)[0].term for i in range(4)]
    assert seed_of == ["a", "b", "c", "a"]


def test_pool_vazio_nao_gera_busca() -> None:
    assert plan_queries(PoolSnapshot(), "youtube", 0, 4) == []


def test_4_coletas_por_dia_caem_em_4_slots() -> None:
    idx = [run_index_for(datetime(2026, 10, 20, h, tzinfo=UTC)) for h in (5, 11, 17, 23)]
    assert len(set(idx)) == 4
    assert run_index_for(datetime(2026, 10, 21, 5, tzinfo=UTC)) - idx[0] == 4


def test_cota_por_env_com_default_youtube_4() -> None:
    assert searches_per_collect("youtube", {}) == 4
    assert searches_per_collect("youtube", {"SEARCHES_PER_COLLECT_YOUTUBE": "3"}) == 3
    assert searches_per_collect("youtube", {"SEARCHES_PER_COLLECT_YOUTUBE": "x"}) == 4
    assert searches_per_collect("instagram", {}) == 1
