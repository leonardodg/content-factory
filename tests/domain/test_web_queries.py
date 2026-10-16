from datetime import UTC, datetime

from content_factory.domain.web_queries import (
    INTENT_LEXICON,
    INTENT_LEXICON_VERSION,
    INTENT_ORDER,
    QUERY_PROTOCOL_VERSION,
    QUERY_SEED,
    PlannedQuery,
    WebQueryProtocol,
    classify_intent,
    intent_coverage,
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


def test_classify_intent_matches_whole_words_only() -> None:
    assert classify_intent("ferro de passar resina") is None  # "erro" em "ferro"
    assert classify_intent("peça incomoda na resina") is None  # "moda" em "incomoda"
    assert classify_intent("acomodar velas na caixa") is None  # "como" em "acomodar"
    assert classify_intent("vela não acende") == "resolver"
    assert classify_intent("resina passo a passo") == "aprender"
    assert classify_intent("vela dá dinheiro?") == "comercial"


# --- intent-v2: marcadores ancorados no vocabulário real do Suggest ---


def test_intent_lexicon_version_is_v2() -> None:
    assert INTENT_LEXICON_VERSION == "intent-v2"


def test_intent_coverage_counts_every_class_in_stable_order() -> None:
    phrases = [
        "como fazer vela",
        "vela nao acende",
        "vela tendencia",
        "vela preco",
        "frase solta",
        "outra frase solta",
    ]
    assert intent_coverage(phrases) == {
        "aprender": 1,
        "resolver": 1,
        "tendencia": 1,
        "comercial": 1,
        "sem_intencao": 2,
    }
    assert list(intent_coverage([])) == [
        "aprender",
        "resolver",
        "tendencia",
        "comercial",
        "sem_intencao",
    ]


def test_intent_markers_are_conjunctions() -> None:
    # contrato do léxico: cada marcador é uma tupla de frases (todas exigidas);
    # marcador de 1 frase = indicador direto, de 2+ = indicador contextual.
    for intent in INTENT_ORDER:
        markers = INTENT_LEXICON[intent]
        assert markers, f"classe sem marcadores: {intent}"
        for marker in markers:
            assert isinstance(marker, tuple)
            assert marker and all(isinstance(phrase, str) for phrase in marker)


def test_aprender_positive_and_negative() -> None:
    assert classify_intent("como fazer velas perfumadas") == "aprender"
    assert classify_intent("sabonete artesanal passo a passo") == "aprender"
    assert classify_intent("soap making for beginners") == "aprender"
    assert classify_intent("handmade soap recipes") == "aprender"
    assert classify_intent("resina para artesanato como usar") == "aprender"
    # negativo: "como" só vale como palavra inteira
    assert classify_intent("acomodar velas na caixa") is None


def test_resolver_matches_doubt_problem_and_comparison() -> None:
    assert classify_intent("cera de soja para que serve") == "resolver"
    assert classify_intent("soy wax candles good or bad") == "resolver"
    assert classify_intent("scented candles bad for you") == "resolver"
    assert classify_intent("soy wax candles safe") == "resolver"
    assert classify_intent("candle wick too short") == "resolver"
    assert classify_intent("cold process soap vs hot process soap") == "resolver"
    assert classify_intent("cera de soja e parafina é a mesma coisa") == "resolver"
    assert classify_intent("flores encapsuladas em resina significado") == "resolver"
    assert classify_intent("saboaria artesanal anvisa") == "resolver"
    assert classify_intent("vela não acende") == "resolver"


def test_resolver_rejects_plain_mentions() -> None:
    # menção simples de material não é dúvida, problema ou comparação
    assert classify_intent("cera de soja") is None
    assert classify_intent("resina epóxi transparente") is None
    assert classify_intent("base glicerinada transparente") is None


def test_tendencia_requires_context_for_ambiguous_words() -> None:
    # palavra ambígua isolada NÃO classifica (ideias/natal/2026/moda)
    assert classify_intent("handmade candles ideas") is None
    assert classify_intent("homemade soap ideas") is None
    assert classify_intent("resin art ideas") is None
    assert classify_intent("vela artesanal de natal") is None
    assert classify_intent("sabonetes artesanais de natal") is None
    # combinação com atualidade/sazonalidade/popularidade classifica
    assert classify_intent("ideias de vela para 2026") == "tendencia"
    assert classify_intent("handmade soap ideas for natal") == "tendencia"
    assert classify_intent("sabonetes artesanais mais vendidos") == "tendencia"
    assert classify_intent("tendência de velas artesanais") == "tendencia"


def test_comercial_positive_and_negative() -> None:
    assert classify_intent("velas artesanais da dinheiro") == "comercial"
    assert classify_intent("resina para artesanato onde comprar") == "comercial"
    assert classify_intent("melt and pour soap base wholesale") == "comercial"
    assert classify_intent("soy wax candles wholesale") == "comercial"
    assert classify_intent("resin keychains price") == "comercial"
    assert classify_intent("saboaria artesanal atacado") == "comercial"
    # negativo: "design"/"valor" de peça não são preço por si sós
    assert classify_intent("resin keychains design") is None
