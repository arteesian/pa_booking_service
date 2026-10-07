from __future__ import annotations

import uuid

from pa_booking.domain.identity import Channel
from pa_booking.domain.roles import Role, roles_for

ADMIN = uuid.uuid4()


def test_listed_huid_in_bot_channel_gets_module_role() -> None:
    assert roles_for(Channel.EXPRESS, ADMIN, module="appointments", admin_huids={ADMIN}) == {
        Role.PSYCHOLOGIST
    }
    assert roles_for(Channel.EXPRESS, ADMIN, module="library", admin_huids={ADMIN}) == {
        Role.LIBRARIAN
    }


def test_unlisted_or_missing_huid_has_no_roles() -> None:
    assert roles_for(Channel.EXPRESS, uuid.uuid4(), module="library", admin_huids={ADMIN}) == set()
    assert roles_for(Channel.EXPRESS, None, module="library", admin_huids={ADMIN}) == set()


def test_lk_channel_never_gets_roles() -> None:
    """Админки в ЛК нет (Р-11): даже HUID из списка роли не даёт."""
    assert roles_for(Channel.LK, ADMIN, module="appointments", admin_huids={ADMIN}) == set()
