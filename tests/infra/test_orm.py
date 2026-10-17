import os

import pytest
from sqlalchemy import Engine, create_engine, inspect

from tests.conftest import requires_infra


@requires_infra
@pytest.mark.integration
def test_all_tables_exist() -> None:
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    names = set(inspect(engine).get_table_names())
    expected = {
        "segments",
        "creators",
        "videos",
        "video_segments",
        "video_metrics",
        "video_scores",
        "transcripts",
        "creative_patterns",
        "ideas",
        "scripts",
        "production_jobs",
        "published_videos",
        "performance_metrics",
        "collect_runs",
    }
    assert expected <= names


def _engine() -> Engine:
    return create_engine(os.environ["TEST_DATABASE_URL"])


def _has_index(engine: Engine, table: str, columns: list[str]) -> bool:
    return any(
        list(idx.get("column_names") or []) == columns for idx in inspect(engine).get_indexes(table)
    )


@requires_infra
@pytest.mark.integration
def test_videos_transcript_id_fk_nullable() -> None:
    # spec §5: videos.transcript_id FK nullable
    engine = _engine()
    cols = {c["name"]: c for c in inspect(engine).get_columns("videos")}
    assert "transcript_id" in cols
    assert cols["transcript_id"]["nullable"] is True


@requires_infra
@pytest.mark.integration
def test_jsonb_gin_indexes() -> None:
    # spec §5: config/raw em JSONB + índice GIN
    engine = _engine()
    for table, column in (("segments", "config"), ("creators", "raw"), ("videos", "raw")):
        idxs = inspect(engine).get_indexes(table)
        assert any(
            list(i.get("column_names") or []) == [column]
            and i.get("dialect_options", {}).get("postgresql_using") == "gin"
            for i in idxs
        ), f"GIN ausente: {table}.{column}"


@requires_infra
@pytest.mark.integration
def test_composite_indexes_for_timeline_reads() -> None:
    # spec §5: índices (platform, published_at) e (video_id, captured_at)
    engine = _engine()
    assert _has_index(engine, "videos", ["platform", "published_at"])
    assert _has_index(engine, "video_metrics", ["video_id", "captured_at"])


def test_tabelas_do_pool_existem() -> None:
    from content_factory.infra.db.orm import KeywordSignalDayRow, SegmentTermRow

    assert SegmentTermRow.__tablename__ == "segment_terms"
    assert KeywordSignalDayRow.__tablename__ == "keyword_signal_days"
    assert {c.name for c in SegmentTermRow.__table__.columns} >= {
        "segment_id",
        "term",
        "origin",
        "kw_rank",
        "trend_score",
        "engagement_score",
        "status",
        "exclude_reason",
        "first_seen_at",
        "last_seen_at",
        "rules_version",
    }


def test_tabelas_de_atribuicao_e_log_de_buscas() -> None:
    from content_factory.infra.db.orm import SearchQueryRow, VideoDiscoveryRow

    assert {
        "video_id",
        "segment_id",
        "platform",
        "term",
        "term_origin",
        "query_id",
        "result_position",
        "run_kind",
        "discovered_at",
    } <= {c.name for c in VideoDiscoveryRow.__table__.columns}
    assert {
        "segment_id",
        "platform",
        "query_id",
        "term",
        "origin",
        "results",
        "kept",
        "quota_used",
        "error_code",
        "started_at",
        "finished_at",
    } <= {c.name for c in SearchQueryRow.__table__.columns}


def test_videos_tem_removed_at() -> None:
    from content_factory.infra.db.orm import VideoRow

    col = VideoRow.__table__.columns["removed_at"]
    assert col.nullable is True
