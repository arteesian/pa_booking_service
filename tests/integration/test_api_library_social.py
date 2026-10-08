"""Оценки и обсуждения книг через API (спека 2026-10-08)."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryComment
from pa_booking.domain.moscow import MSK
from tests.integration.conftest import LIB_ADMIN, ApiEnv, add_roster, bot_headers, lk_headers

pytestmark = pytest.mark.db

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def add_book(db: Session, *, removed: bool = False) -> int:
    book = LibraryBook(
        genre="Роман",
        author="Булгаков",
        title="Мастер и Маргарита",
        description="—",
        removed_at=datetime(2026, 10, 1, tzinfo=MSK) if removed else None,
    )
    db.add(book)
    db.commit()
    return book.id


def headers(huid: uuid.UUID, name: str | None = None) -> dict[str, str]:
    return bot_headers(huid, module="library", name=name)


def rate(api: ApiEnv, book_id: int, score: int, user: uuid.UUID = USER) -> httpx.Response:
    return api.client.put(
        f"/library/books/{book_id}/rating", json={"score": score}, headers=headers(user)
    )


# --- оценки ---


def test_rating_of_unrated_book(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    r = api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER))
    assert r.status_code == 200
    assert r.json() == {"avg": None, "count": 0, "mine": None}


def test_rerating_replaces_and_mine_is_per_user(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    assert rate(api, book_id, 5).json() == {"avg": 5.0, "count": 1, "mine": 5}
    assert rate(api, book_id, 2).json() == {"avg": 2.0, "count": 1, "mine": 2}
    assert rate(api, book_id, 5, user=OTHER).json() == {"avg": 3.5, "count": 2, "mine": 5}

    r = api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER))
    assert r.json() == {"avg": 3.5, "count": 2, "mine": 2}


@pytest.mark.parametrize("score", [0, 6])
def test_score_out_of_range_is_422(api: ApiEnv, db_session: Session, score: int) -> None:
    assert rate(api, add_book(db_session), score).status_code == 422


def test_catalog_carries_rating(api: ApiEnv, db_session: Session) -> None:
    rated = add_book(db_session)
    unrated = add_book(db_session)
    rate(api, rated, 5)
    rate(api, rated, 4, user=OTHER)

    books = api.client.get("/library/books", headers=headers(USER)).json()

    by_id = {b["id"]: (b["rating_avg"], b["rating_count"]) for b in books}
    assert by_id == {rated: (4.5, 2), unrated: (None, 0)}


def test_loans_carry_book_rating(api: ApiEnv, db_session: Session) -> None:
    """BookOut внутри выдач — с настоящим рейтингом, не null/0 (Review Focus 1)."""
    book_id = add_book(db_session)
    rate(api, book_id, 4, user=OTHER)

    loan = api.client.post(f"/library/books/{book_id}/loan", headers=headers(USER))
    assert loan.status_code == 201
    assert (loan.json()["book"]["rating_avg"], loan.json()["book"]["rating_count"]) == (4.0, 1)

    mine = api.client.get("/library/loans/my", headers=headers(USER)).json()
    assert mine[0]["book"]["rating_count"] == 1

    admin = api.client.get("/library/admin/loans?state=active", headers=headers(LIB_ADMIN)).json()
    assert admin[0]["book"]["rating_avg"] == 4.0


def test_rating_of_removed_book_is_404(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session, removed=True)
    assert (
        api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER)).status_code == 404
    )
    assert rate(api, book_id, 5).status_code == 404
    assert rate(api, 999_999, 5).status_code == 404


def test_lk_user_rates_via_roster_huid(api: ApiEnv, db_session: Session) -> None:
    employee_id = add_roster(db_session, USER, "Иванов Иван")
    book_id = add_book(db_session)
    r = api.client.put(
        f"/library/books/{book_id}/rating", json={"score": 3}, headers=lk_headers(employee_id)
    )
    assert r.status_code == 200
    assert r.json()["mine"] == 3


# --- обсуждение ---


def post_comment(
    api: ApiEnv, book_id: int, text: str, user: uuid.UUID = USER, name: str | None = None
) -> httpx.Response:
    return api.client.post(
        f"/library/books/{book_id}/comments", json={"text": text}, headers=headers(user, name)
    )


def comments(api: ApiEnv, book_id: int, user: uuid.UUID = USER) -> list[dict[str, object]]:
    r = api.client.get(f"/library/books/{book_id}/comments", headers=headers(user))
    assert r.status_code == 200
    result: list[dict[str, object]] = r.json()
    return result


def test_comments_newest_first_with_author_and_mine(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    first = post_comment(api, book_id, "  Понравилось  ", name="Иванов Иван")
    assert first.status_code == 201
    assert first.json()["text"] == "Понравилось"
    api.clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=MSK)
    post_comment(api, book_id, "Скучно", user=OTHER, name="Петров Пётр")

    rows = comments(api, book_id)

    assert [(c["author_name"], c["text"], c["mine"]) for c in rows] == [
        ("Петров Пётр", "Скучно", False),
        ("Иванов Иван", "Понравилось", True),
    ]


def test_blank_comment_is_422(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    assert post_comment(api, book_id, "   ").status_code == 422
    assert post_comment(api, book_id, "я" * 2001).status_code == 422
    assert post_comment(api, book_id, "я" * 2000).status_code == 201


def test_comments_of_removed_book_are_404(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session, removed=True)
    r = api.client.get(f"/library/books/{book_id}/comments", headers=headers(USER))
    assert r.status_code == 404
    assert post_comment(api, book_id, "Текст").status_code == 404


def test_delete_own_comment_hides_it_and_second_delete_is_404(
    api: ApiEnv, db_session: Session
) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Удалю").json()["id"]

    first = api.client.delete(f"/library/comments/{comment_id}", headers=headers(USER))
    second = api.client.delete(f"/library/comments/{comment_id}", headers=headers(USER))

    assert (first.status_code, second.status_code) == (204, 404)
    assert comments(api, book_id) == []


def test_cannot_delete_others_comment(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Моё").json()["id"]

    r = api.client.delete(f"/library/comments/{comment_id}", headers=headers(OTHER))

    assert r.status_code == 404
    assert len(comments(api, book_id)) == 1


def test_librarian_deletes_any_comment(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Спам").json()["id"]

    r = api.client.delete(f"/library/admin/comments/{comment_id}", headers=headers(LIB_ADMIN))

    assert r.status_code == 204
    assert comments(api, book_id) == []
    stored = db_session.get(LibraryComment, comment_id)
    assert stored is not None and stored.removed_by_librarian is True
    again = api.client.delete(f"/library/admin/comments/{comment_id}", headers=headers(LIB_ADMIN))
    assert again.status_code == 404


def test_lk_without_huid_reads_but_cannot_write(api: ApiEnv, db_session: Session) -> None:
    """Review Focus 2: ЛК без привязки eXpress — читать можно, писать нельзя."""
    book_id = add_book(db_session)
    post_comment(api, book_id, "Есть", user=OTHER)
    h = lk_headers(add_roster(db_session, None, "Без Привязки"))

    rating = api.client.get(f"/library/books/{book_id}/rating", headers=h)
    listing = api.client.get(f"/library/books/{book_id}/comments", headers=h)
    put = api.client.put(f"/library/books/{book_id}/rating", json={"score": 5}, headers=h)
    post = api.client.post(f"/library/books/{book_id}/comments", json={"text": "Т"}, headers=h)

    assert rating.json()["mine"] is None
    assert [c["mine"] for c in listing.json()] == [False]
    assert (put.status_code, put.json()["code"]) == (409, "express_not_linked")
    assert (post.status_code, post.json()["code"]) == (409, "express_not_linked")


# --- правка своего комментария ---


def edit_comment(api: ApiEnv, comment_id: int, text: str, user: uuid.UUID = USER) -> httpx.Response:
    return api.client.patch(
        f"/library/comments/{comment_id}", json={"text": text}, headers=headers(user)
    )


def test_edit_own_comment_marks_it_edited(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    created = post_comment(api, book_id, "Черновик", name="Иванов Иван").json()
    assert created["edited"] is False

    r = edit_comment(api, created["id"], "  Итог  ")

    assert r.status_code == 200
    assert (r.json()["text"], r.json()["edited"], r.json()["mine"]) == ("Итог", True, True)
    # Тот же момент; запись зоны может отличаться (свежий объект — МСК, из БД — UTC).
    published = datetime.fromisoformat(r.json()["created_at"])
    assert published == datetime.fromisoformat(created["created_at"])
    assert [(c["text"], c["edited"]) for c in comments(api, book_id)] == [("Итог", True)]


def test_edit_others_or_removed_comment_is_404(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Моё").json()["id"]

    assert edit_comment(api, comment_id, "Чужая правка", user=OTHER).status_code == 404
    api.client.delete(f"/library/comments/{comment_id}", headers=headers(USER))
    assert edit_comment(api, comment_id, "После удаления").status_code == 404
    assert edit_comment(api, 999_999, "Нет такого").status_code == 404


def test_edit_validates_text(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Текст").json()["id"]
    assert edit_comment(api, comment_id, "   ").status_code == 422
    assert edit_comment(api, comment_id, "я" * 2001).status_code == 422


def test_lk_without_huid_cannot_edit(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Текст").json()["id"]
    h = lk_headers(add_roster(db_session, None, "Без Привязки"))

    r = api.client.patch(f"/library/comments/{comment_id}", json={"text": "Т"}, headers=h)

    assert (r.status_code, r.json()["code"]) == (409, "express_not_linked")
