from datetime import UTC, datetime

from content_factory.domain.web_queries import (
    INTENT_LEXICON_VERSION,
    INTENT_ORDER,
    QUERY_PROTOCOL_VERSION,
    QUERY_SEED,
    PlannedQuery,
    WebQueryProtocol,
    classify_intent,
    normalize,
    plan_web_queries,
    protocol_from_dict,
    protocol_to_dict,
    rank_intent_phrases,
    run_index_for,
    select_queries,
)


def test_classify_intent_each_class() -> None:
    assert classify_intent("como fazer vela artesanal") == "aprender"
    assert classify_intent("por que minha resina rachou") == "resolver"
    assert classify_intent("tendência de velas 2026") == "tendencia"
    assert classify_intent("quanto custa para vender sabonete") == "comercial"


def test_classify_intent_precedence_first_class_wins() -> None:
    # "como" (aprender) e "preço" (comercial) → primeira na ordem documentada
    assert classify_intent("como definir preço de vela") == "aprender"


def test_classify_intent_no_marker() -> None:
    assert classify_intent("vela artesanal") is None
    assert classify_intent("resina epóxi") is None


def test_rank_is_deterministic_by_seed() -> None:
    signals = [f"como fazer item {i}" for i in range(10)]
    a = rank_intent_phrases(signals, seed=QUERY_SEED)
    b = rank_intent_phrases(list(reversed(signals)), seed=QUERY_SEED)
    assert a == b  # independente da ordem de entrada


def test_rank_independent_of_weight_order() -> None:
    # a função só vê frases: nenhum peso influencia o sorteio
    signals = ["como A", "como B", "como C", "como D"]
    assert rank_intent_phrases(signals, seed=1) == rank_intent_phrases(signals, seed=1)


def test_select_queries_three_per_class_plus_seed() -> None:
    signals = (
        [f"como fazer {i}" for i in range(5)]
        + [f"por que {i}" for i in range(5)]
        + [f"tendência {i}" for i in range(5)]
        + [f"preço {i}" for i in range(5)]
    )
    queries, missing = select_queries(signals, ["vela artesanal", "resina", "kit"])
    assert missing == {}
    intents = [q.intent for q in queries]
    for intent in INTENT_ORDER:
        assert intents.count(intent) == 3
    assert intents.count("seed") == 1
    seed = next(q for q in queries if q.intent == "seed")
    assert seed.query == "vela artesanal OR resina OR kit"
    assert seed.query_id == "q1:seed:00"


def test_select_queries_class_with_fewer_than_three_records_missing() -> None:
    queries, missing = select_queries(["como fazer vela", "tutorial de resina"], [])
    assert missing["aprender"] == 1
    assert missing["resolver"] == 3
    assert [q.intent for q in queries] == ["aprender", "aprender"]
    assert all(q.intent != "seed" for q in queries)  # sem keywords → sem seed


def test_select_queries_veto_uses_next_from_draw() -> None:
    signals = [f"como fazer {i}" for i in range(6)]
    full, _ = select_queries(signals, [])
    veto_target = full[0].query
    vetoed, _ = select_queries(signals, [], vetoes={normalize(veto_target): "não é do segmento"})
    chosen = [q.query for q in vetoed if q.intent == "aprender"]
    assert veto_target not in chosen
    assert len(chosen) == 3


def test_plan_web_queries_cycles_and_covers_all() -> None:
    protocol = [PlannedQuery(f"q1:seed:{i:02d}", f"q{i}", "seed") for i in range(13)]
    seen: set[str] = set()
    for run_index in range(5):  # ceil(13/3)
        batch = plan_web_queries(protocol, run_index, 3)
        assert len(batch) == 3
        seen.update(q.query_id for q in batch)
    assert len(seen) == 13


def test_plan_web_queries_empty_protocol() -> None:
    assert plan_web_queries([], 0, 3) == []


def test_run_index_for_is_2_per_day() -> None:
    morning = run_index_for(datetime(2026, 10, 18, 8, 0, tzinfo=UTC))
    evening = run_index_for(datetime(2026, 10, 18, 20, 0, tzinfo=UTC))
    next_day = run_index_for(datetime(2026, 10, 19, 8, 0, tzinfo=UTC))
    assert evening - morning == 1
    assert next_day - evening == 1


def test_protocol_roundtrip() -> None:
    protocols, missing = select_queries([f"como {i}" for i in range(4)], ["vela"])
    proto = protocol_from_dict(
        protocol_to_dict(
            WebQueryProtocol(
                segment_slug="velas-artesanais",
                version=QUERY_PROTOCOL_VERSION,
                seed=QUERY_SEED,
                lexicon_version=INTENT_LEXICON_VERSION,
                queries=protocols,
                missing=missing,
            )
        )
    )
    assert proto.segment_slug == "velas-artesanais"
    assert proto.queries == protocols
    assert proto.seed == QUERY_SEED
