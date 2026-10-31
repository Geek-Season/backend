"""Ранги и прогноз балла ЕГЭ по PTS.

Ранг — не порог PTS, а прогноз первичного балла: по каждому номеру берём шанс решить
типичную задачу номера (PTS ученика по номеру против медианы рейтингов задач номера),
складываем с весами (19–21 — три балла, 26 и 27 — по два) и переводим по шкале 2026 года.

PTS по номеру стартует с общего PTS ученика и дальше уточняется ответами по номеру.
Номер, по которому ответов ещё нет, считается по общему PTS. Ранг появляется только
после входного пробника, так что к этому моменту ответы есть по всем номерам.
"""

from dataclasses import dataclass

from app.modules.exam.scale import to_test_score
from app.modules.pts.glicko import expected


@dataclass(frozen=True)
class Rank:
    key: str
    title: str  # полное — в профиле
    short: str  # короткое — в рейтинге и шапке
    min_primary: int


# От высшего к низшему. Границы — по шкале перевода: Рыцарь с 6 первичных = 40 тестовых.
RANKS = [
    Rank("immortal", "Immortal", "Immortal", 26),
    Rank("divine", "Божество 24 и 25", "Божество", 21),
    Rank("overlord", "Властелин теории игр", "Властелин", 18),
    Rank("legend", "Легенда экселя", "Легенда", 14),
    Rank("hero", "Герой рекурсии", "Герой", 10),
    Rank("knight", "Рыцарь черепахи", "Рыцарь", 6),
    Rank("recruit", "Рекрут ворда", "Рекрут", 0),
]

FULL_PRIMARY = 29  # максимум первичных баллов на ЕГЭ по информатике
# Сколько первичных баллов даёт номер на экзамене.
POINTS = {"19-21": 3, "26": 2, "27": 2}
# Запас при понижении: ранг снимается, только когда прогноз ниже его границы больше чем на 1 балл.
DEMOTION_MARGIN = 1


def rank_for(primary: int) -> Rank:
    return next(r for r in RANKS if primary >= r.min_primary)


def by_key(key: str | None) -> Rank | None:
    return next((r for r in RANKS if r.key == key), None)


def next_rank(current: Rank | None, primary: int) -> Rank:
    """Ранг по прогнозу с защитой: вверх — сразу, вниз — только с запасом DEMOTION_MARGIN."""
    target = rank_for(primary)
    if current is None or RANKS.index(target) <= RANKS.index(current):
        return target  # первый ранг или повышение (или тот же)
    if primary < current.min_primary - DEMOTION_MARGIN:
        return target
    return current


def next_up(rank: Rank) -> Rank | None:
    """Следующий ранг выше (None — уже Immortal)."""
    index = RANKS.index(rank)
    return RANKS[index - 1] if index > 0 else None


@dataclass(frozen=True)
class Forecast:
    primary_exact: float  # сумма шансов, до округления
    primary: int
    test_score: int
    rank: Rank


def forecast(number_pts: dict[str, float], overall_pts: float, typical: dict[str, float]) -> Forecast:
    """number_pts — {код номера: PTS по номеру}; overall_pts — общий PTS (для номеров без ответов);
    typical — {код номера: типичный рейтинг его задач}."""
    total = sum(
        POINTS.get(code, 1) * expected(number_pts.get(code, overall_pts), task_rating)
        for code, task_rating in typical.items()
    )
    primary = int(total + 0.5)
    return Forecast(total, primary, to_test_score(primary), rank_for(primary))
