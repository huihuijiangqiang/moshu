from pathlib import Path

from db.models_long_generation import GenerationSegment


def test_generation_segment_lease_columns_match_migration_contract():
    columns = GenerationSegment.__table__.c
    assert columns.lease_owner.type.length == 100
    assert columns.lease_expires_at.type.timezone is True
    assert columns.heartbeat_at.type.timezone is True
    migration = Path(__file__).parents[1] / "alembic" / "versions" / "040_long_generation_leases.py"
    source = migration.read_text(encoding="utf-8")
    for column in ("lease_owner", "lease_expires_at", "heartbeat_at"):
        assert f'Column("{column}"' in source
        assert f'drop_column("generation_segments", "{column}")' in source
