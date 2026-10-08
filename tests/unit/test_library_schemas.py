from __future__ import annotations

import pytest
from pydantic import ValidationError

from pa_booking.api.library_schemas import COMMENT_MAX, CommentIn, RatingIn


@pytest.mark.parametrize("score", [0, 6, -1])
def test_score_outside_1_5_rejected(score: int) -> None:
    with pytest.raises(ValidationError):
        RatingIn(score=score)


@pytest.mark.parametrize("score", [1, 5])
def test_score_bounds_accepted(score: int) -> None:
    assert RatingIn(score=score).score == score


def test_comment_text_bounds() -> None:
    assert CommentIn(text="  Хорошо  ").text == "Хорошо"
    assert len(CommentIn(text="я" * COMMENT_MAX).text) == COMMENT_MAX
    for bad in ["", "   \n ", "я" * (COMMENT_MAX + 1)]:
        with pytest.raises(ValidationError):
            CommentIn(text=bad)
