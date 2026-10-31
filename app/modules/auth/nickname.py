"""Ник ученика: проверка формы и фильтр мата.

Ник показывается на сайте вместо имени из Telegram: в шапке, профиле, рейтинге.
Не задан — показывается имя из Telegram. Ники могут повторяться.

Правила: 3–20 символов; буквы (любого алфавита), цифры, пробел, «_» и «-»; хотя бы
одна буква или цифра; без мата. Пробелы по краям убираются, подряд — сливаются в один. Фильтр простой — он отсекает очевидное, в том числе
с заменой букв (xyй, п1зд…) и транслитом (pizda, huy). Что проскочит — администратор сбросит кнопкой
в рейтинге (DELETE /auth/users/{id}/nickname).
"""

import re

MIN_LENGTH = 3
MAX_LENGTH = 20

_ALLOWED = re.compile(r"[\w-]+(?: [\w-]+)*")

# Корни, которые встречаются почти только в мате. Сравниваем после нормализации
# (латиница и цифры, похожие на русские буквы, → русские; ё → е; без «_» и «-»).
# Двусмысленные корни не берём: «манда» есть в «команда», «бля» — в «рубля»,
# «пидар» — в «скипидар», «сучк» — в фамилии «Сучков».
_BAD_ROOTS = (
    "хуй", "хуе", "хуя", "хуи", "хую", "пизд", "пезд", "заеб", "уебок", "уебищ", "долбоеб",
    "бляд", "блят", "сука", "сучка", "сучар", "мудак", "мудил", "пидор", "пидарас", "пидр", "гандон", "гондон",
    "шлюх", "залуп", "дрочи", "дроче",
)
# «еб…» — мат, если перед ним не «л» или «р»: хлебать, колебать, погребать, скребок.
_BAD_EB = re.compile(r"(?<![лр])еб(ан|ат|ал|ло|уч|нут)")
_BAD_LATIN = ("fuck", "shit", "bitch", "cunt", "whore", "nigg", "faggot")

# Латиница и цифры, которыми подменяют русские буквы.
_LOOKALIKE = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р",
    "t": "т", "x": "х", "y": "у", "u": "и", "i": "и", "n": "п", "3": "з", "0": "о", "1": "и",
    "6": "б", "4": "ч", "ё": "е",
})


# Мат латиницей транслитом (pizda, huy, ebal, blyat).
_TRANSLIT_PAIRS = (("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("yo", "е"),
                   ("ya", "я"), ("yu", "ю"), ("ts", "ц"))
_TRANSLIT = str.maketrans({
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и", "j": "й", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф",
    "h": "х", "x": "х", "y": "й", "c": "к", "w": "в", "q": "к", "ё": "е", "0": "о", "1": "и", "3": "з",
})


def _translit(text: str) -> str:
    for latin, russian in _TRANSLIT_PAIRS:
        text = text.replace(latin, russian)
    return text.translate(_TRANSLIT)


class NicknameError(ValueError):
    pass


def _has_bad_word(nickname: str) -> bool:
    flat = nickname.lower().replace("_", "").replace("-", "").replace(" ", "")
    if any(word in flat for word in _BAD_LATIN):
        return True
    for cyrillic in (flat.translate(_LOOKALIKE), _translit(flat)):
        if any(root in cyrillic for root in _BAD_ROOTS) or _BAD_EB.search(cyrillic):
            return True
    return False


def clean(value: str | None) -> str | None:
    """Проверить ник. Пустой или None — «вернуть имя из Telegram» (None).

    Неподходящий ник → NicknameError с понятным сообщением.
    """
    if value is None:
        return None
    nickname = " ".join(value.split())
    if not nickname:
        return None
    if not MIN_LENGTH <= len(nickname) <= MAX_LENGTH:
        raise NicknameError(f"Имя — от {MIN_LENGTH} до {MAX_LENGTH} символов")
    if not _ALLOWED.fullmatch(nickname):
        raise NicknameError("В имени можно использовать только буквы, цифры, пробел, «_» и «-»")
    if not any(ch.isalnum() for ch in nickname):
        raise NicknameError("В имени должна быть хотя бы одна буква или цифра")
    if _has_bad_word(nickname):
        raise NicknameError("Такое имя выбрать нельзя")
    return nickname
