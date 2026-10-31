"""Glicko-2 — рейтинг с «уверенностью» (Марк Гликман, glicko.net/glicko/glicko2.pdf).

Как Эло, но у каждого рейтинга есть RD — насколько ему можно верить. Пока ответов
мало, RD большой и рейтинг меняется резко (калибровка); с каждым ответом RD
уменьшается, изменения становятся спокойнее. Волатильность (vol) — насколько
рейтинг «скачет» сам по себе.

Здесь только математика, без базы: rate() получает рейтинг и список «матчей»
и возвращает новый рейтинг. Ученик и задача для этой функции одинаковы.
"""

import math
from dataclasses import dataclass

SCALE = 173.7178  # 400 / ln(10): переход между шкалой Эло (1500 ± …) и внутренней шкалой Glicko-2
TAU = 0.5  # как быстро может меняться волатильность (Гликман советует 0.3–1.2)
EPSILON = 1e-6


@dataclass(frozen=True)
class Rating:
    rating: float = 1500.0
    rd: float = 350.0
    vol: float = 0.06


def expected(rating: float, opponent: float) -> float:
    """Шанс «победить» (решить задачу) без учёта уверенности — та же формула, что у Эло."""
    return 1 / (1 + 10 ** ((opponent - rating) / 400))


def _g(phi: float) -> float:
    return 1 / math.sqrt(1 + 3 * phi**2 / math.pi**2)


def rate(player: Rating, games: list[tuple[Rating, float]], tau: float = TAU) -> Rating:
    """Новый рейтинг после матчей одного периода: games = [(соперник, результат 0..1), …]."""
    mu = (player.rating - 1500) / SCALE
    phi = player.rd / SCALE
    if not games:
        return Rating(player.rating, min(SCALE * math.sqrt(phi**2 + player.vol**2), 350.0), player.vol)

    v_inv = 0.0
    delta_sum = 0.0
    for opponent, score in games:
        mu_j = (opponent.rating - 1500) / SCALE
        g = _g(opponent.rd / SCALE)
        e = 1 / (1 + math.exp(-g * (mu - mu_j)))
        v_inv += g**2 * e * (1 - e)
        delta_sum += g * (score - e)
    v = 1 / v_inv
    delta = v * delta_sum

    # Новая волатильность: корень уравнения f(x) = 0 (алгоритм Иллинойс, шаг 5 у Гликмана).
    a = math.log(player.vol**2)

    def f(x: float) -> float:
        ex = math.exp(x)
        return ex * (delta**2 - phi**2 - v - ex) / (2 * (phi**2 + v + ex) ** 2) - (x - a) / tau**2

    big_a = a
    if delta**2 > phi**2 + v:
        big_b = math.log(delta**2 - phi**2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        big_b = a - k * tau
    f_a, f_b = f(big_a), f(big_b)
    while abs(big_b - big_a) > EPSILON:
        big_c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
        f_c = f(big_c)
        if f_c * f_b <= 0:
            big_a, f_a = big_b, f_b
        else:
            f_a /= 2
        big_b, f_b = big_c, f_c
    vol = math.exp(big_a / 2)

    phi_star = math.sqrt(phi**2 + vol**2)
    phi_new = 1 / math.sqrt(1 / phi_star**2 + 1 / v)
    mu_new = mu + phi_new**2 * delta_sum
    return Rating(SCALE * mu_new + 1500, SCALE * phi_new, vol)
