from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect

from pa_booking.db.models import Base

pytestmark = pytest.mark.db


def test_migrations_create_every_model_table(pg_engine: Engine) -> None:
    tables = set(inspect(pg_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables


def test_migration_columns_match_models(pg_engine: Engine) -> None:
    insp = inspect(pg_engine)
    for name, table in Base.metadata.tables.items():
        db_cols = {c["name"] for c in insp.get_columns(name)}
        assert db_cols == {c.name for c in table.columns}, name
