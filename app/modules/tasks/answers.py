"""Сравнение ответа ученика с правильным.

В ЕГЭ по информатике ответ бывает:
- числом:              22
- словом/буквами:      zyxw
- несколькими числами: 2 20   (например, в заданиях 17, 18, 26, 27)

Поэтому ответ режем на части по пробелам и сравниваем части по очереди:
- если обе части — числа, сравниваем как числа (4 == 4.0, 0,25 == 0.25);
- иначе сравниваем как текст без учёта регистра (ZYXW == zyxw).
"""

from decimal import Decimal, InvalidOperation

# Самый длинный ответ: таблица номера 25 (2 × 10–12 чисел) в одну строку — до ~200 символов.
# Общий лимит для правильного ответа задачи и ответа ученика (в базе — String(MAX_ANSWER_LENGTH)).
MAX_ANSWER_LENGTH = 255


def _as_number(part: str) -> Decimal | None:
    try:
        number = Decimal(part.replace(",", "."))
    except InvalidOperation:
        return None
    # Decimal понимает "NaN" и "Infinity" — для нас это слова, а не числа.
    return number if number.is_finite() else None


def _parts_equal(given: str, expected: str) -> bool:
    given_number = _as_number(given)
    expected_number = _as_number(expected)
    if given_number is not None and expected_number is not None:
        return given_number == expected_number
    return given.casefold() == expected.casefold()


def answers_match(given: str, expected: str) -> bool:
    given_parts = given.split()
    expected_parts = expected.split()
    if len(given_parts) != len(expected_parts):
        return False
    return all(_parts_equal(g, e) for g, e in zip(given_parts, expected_parts))
