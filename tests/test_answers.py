"""Сравнение ответов — чистая функция, база не нужна."""

import pytest

from app.modules.tasks.answers import answers_match


@pytest.mark.parametrize(
    ("given", "expected", "result"),
    [
        ("4", "4", True),
        ("4.0", "4", True),
        ("0,25", "0.25", True),
        ("5", "4", False),
        ("ZYXW", "zyxw", True),
        ("zyx", "zyxw", False),
        ("2 20", "2 20", True),
        ("2   20", "2 20", True),
        ("20 2", "2 20", False),
        ("2", "2 20", False),
        ("NaN", "NaN", True),  # слово, а не число — не должно падать
        ("sNaN", "4", False),
    ],
)
def test_answers_match(given, expected, result):
    assert answers_match(given, expected) is result
