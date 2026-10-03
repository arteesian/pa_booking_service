from __future__ import annotations

from pa_booking.domain.roles import Role, parse_roles


def test_parses_csv_and_drops_unknown() -> None:
    assert parse_roles("psychologist, operator ,librarian") == {
        Role.PSYCHOLOGIST,
        Role.LIBRARIAN,
    }


def test_empty_header_means_no_roles() -> None:
    assert parse_roles(None) == frozenset()
    assert parse_roles("") == frozenset()


def test_admin_is_not_our_role() -> None:
    """admin другие роли неявно не покрывает (CLAUDE.md): для нас его нет."""
    assert parse_roles("admin") == frozenset()
