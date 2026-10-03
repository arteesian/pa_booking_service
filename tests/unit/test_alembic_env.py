from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[2]


def test_dsn_with_percent_encoded_password_reaches_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``%40`` в пароле — штатный DSN; падать должно на подключении, а не в ConfigParser.

    Порт 1 закрыт: до БД не дойдём, но и ``ValueError: invalid interpolation`` быть
    не должно.
    """
    monkeypatch.setenv("PA_BOOKING_DATABASE_URL", "postgresql+psycopg://u:p%40ss@127.0.0.1:1/db")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    with pytest.raises(OperationalError):
        command.upgrade(cfg, "head")
