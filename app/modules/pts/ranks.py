"""Ранги и прогноз балла ЕГЭ по PTS.

Ранг — не порог PTS, а прогноз первичного балла: по каждому номеру берём шанс решить
типичную задачу номера (PTS ученика по номеру против среднего рейтинга задач номера),
складываем с весами (19–21 — три балла, 26 и 27 — по два) и переводим по шкале 2026 года.
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

# Сколько первичных баллов даёт номер на экзамене.
POINTS = {"19-21": 3, "26": 2, "27": 2}
MIN_GAMES_PER_NUMBER = 3  # меньше засчитанных ответов по номеру — номер в прогнозе как 0


def rank_for(primary: int) -> Rank:
    return next(r for r in RANKS if primary >= r.min_primary)


@dataclass(frozen=True)
class Forecast:
    primary_exact: float  # сумма шансов, до округления
    primary: int
    test_score: int
    rank: Rank


def forecast(number_pts: dict[str, tuple[float, int]], typical: dict[str, float]) -> Forecast:
    """number_pts — {код номера: (PTS по номеру, засчитано ответов)}; typical — {код: типичный рейтинг задач}.

    Номера, по которым у ученика меньше MIN_GAMES_PER_NUMBER ответов, считаются нулём:
    ранг показывает то, что ученик уже доказал.
    """
    total = 0.0
    for code, task_rating in typical.items():
        pts, games = number_pts.get(code, (0.0, 0))
        if games >= MIN_GAMES_PER_NUMBER:
            total += POINTS.get(code, 1) * expected(pts, task_rating)
    primary = int(total + 0.5)
    return Forecast(total, primary, to_test_score(primary), rank_for(primary))
