from app.schemas.database import DatabaseSignalStats


def test_database_signal_stats_keeps_copy_api_field() -> None:
    stats = DatabaseSignalStats(
        total=4,
        copy_count=1,
        skip=1,
        exit=1,
        observe=1,
        last_created_at=None,
    )

    assert stats.model_dump(by_alias=True)["copy"] == 1
