import os

import pytest
from sqlalchemy import create_engine, inspect

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
