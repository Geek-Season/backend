"""Импорт с kompege.ru: HTML → наш Markdown и сборка тела POST /tasks (без сети)."""

import base64

from scripts.kompege.convert import html_to_markdown
from scripts.kompege.mapping import classify_source, flat_answer, prepare, source_label, video_link

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def md(html: str) -> str:
    return html_to_markdown(html).markdown


def test_paragraphs_bold_and_breaks():
    assert md("<p>Первый <strong>жирный </strong>абзац<br />строка</p><p>Второй</p>") == (
        "Первый **жирный** абзац\nстрока\n\nВторой"
    )


def test_math_delimiters_and_entities():
    assert md(r"<p>Функция \(F=(x&or;y)&and;&not;w\) задана</p>") == r"Функция $F=(x\lor y)\land \neg w$ задана"
    assert md(r"<p>\[ 2^{10} \]</p>") == "$$\n2^{10}\n$$"


def test_markdown_symbols_escaped_outside_math():
    assert md("<p>a*b_c [x] $5 &lt;tag&gt;</p>") == r"a\*b\_c \[x\] \$5 \<tag>"
    assert md("<p>- не список</p><p>1. не нумерация</p>") == "\\- не список\n\n\\1. не нумерация"


def test_kept_tags():
    assert md("<p>10<sub>2</sub> и 2<sup>3</sup>, <u>важно</u></p>") == "10<sub>2</sub> и 2<sup>3</sup>, <u>важно</u>"


def test_table_to_gfm():
    html = (
        '<table border="1" style="width:500px"><colgroup><col style="width:20%"></colgroup><tbody>'
        "<tr><td>x</td><td>y</td><td><strong>F</strong></td></tr>"
        "<tr><td>1</td><td></td><td>a|b</td></tr>"
        "</tbody></table>"
    )
    assert md(html) == "| x | y | **F** |\n|---|---|---|\n| 1 |  | a\\|b |"


def test_table_spans_warn():
    result = html_to_markdown('<table><tr><td colspan="2">x</td></tr></table>')
    assert result.warnings and "colspan" in result.warnings[0]


def test_data_image_becomes_pending():
    src = "data:image/png;base64," + base64.b64encode(PNG).decode()
    result = html_to_markdown(f'<p>Граф:</p><p><img src="{src}" /></p>', image_offset=3)
    assert result.markdown == "Граф:\n\n![](pending:3)"
    assert result.images[0].data == PNG


def test_code_block_keeps_indentation():
    assert md("<pre>def f(n):\n    return n*2</pre>") == "```python\ndef f(n):\n    return n*2\n```"


def test_unknown_tag_keeps_text():
    result = html_to_markdown("<p><blink>мигает</blink></p>")
    assert result.markdown == "мигает" and result.warnings


def test_mapping_helpers():
    assert flat_answer("20065 11921\\n11 5") == "20065 11921 11 5"
    assert flat_answer("2625 127667\n36955 60937") == "2625 127667 36955 60937"
    assert classify_source("Демоверсия 2027") == "demo"
    assert classify_source("Основная волна 19.06.26") == "official"
    assert classify_source("Переcдача 04.07.24") == "official"  # латинская «c» как на сайте
    assert classify_source("Апробация 14.05.26") == "approbation"
    assert classify_source("Джобс 21.12.22") == "author"
    assert source_label("") == "КЕГЭ (kompege.ru)"
    assert source_label("Danov2201") == "КЕГЭ (kompege.ru) · Danov2201"
    assert video_link({"videotype": "yt", "video": "abc", "timecode": "930"}) == "https://youtu.be/abc?t=930"
    assert video_link({"videotype": "rutube", "video": "f00", "timecode": ""}) == "https://rutube.ru/video/f00/"


def test_prepare_task_27_with_files():
    item = {"taskId": 31371, "number": 27, "comment": "Пересдача 08.07.26", "text": "<p>Кластеры</p>",
            "key": "20065 11921\\n11 5", "solve_text": "", "video": "", "files": [
                {"url": "/files/Zb.txt", "name": "27_A.txt"}, {"url": "/files/x.py", "name": "../evil.py"}]}
    p = prepare(item)
    assert p.skip is None
    assert p.payload == {"exam_number": "27", "external_id": "kompege:31371", "source": "official",
                         "source_label": "КЕГЭ (kompege.ru) · Пересдача 08.07.26", "difficulty": 50,
                         "statement": "Кластеры", "explanation": "", "correct_answer": "20065 11921 11 5"}
    assert p.attachments == [{"url": "/files/Zb.txt", "name": "27_A.txt"}]
    assert any("evil.py" in w for w in p.warnings)


def test_prepare_task_19_splits_question():
    item = {"taskId": 1, "number": 19, "comment": "Демоверсия 2027", "key": "29", "video": "v", "videotype": "yt",
            "timecode": "60", "solve_text": "",
            "text": "<p>Два игрока играют в игру.</p><p>Известно, что Ваня выиграл первым ходом. Найдите S.</p>",
            "subTask": [{"number": 20, "key": "28 48", "text": "<p>Вопрос 20</p>"},
                        {"number": 21, "key": "44", "text": "<p>Вопрос 21</p>"}]}
    p = prepare(item)
    assert p.skip is None and not p.warnings
    assert p.payload["exam_number"] == "19-21"
    assert p.payload["statement"] == "Два игрока играют в игру."
    assert p.payload["explanation"] == "Видеоразбор: https://youtu.be/v?t=60"
    assert [(x["number"], x["question"], x["correct_answer"]) for x in p.payload["parts"]] == [
        ("19", "Известно, что Ваня выиграл первым ходом. Найдите S.", "29"),
        ("20", "Вопрос 20", "28 48"),
        ("21", "Вопрос 21", "44"),
    ]


def test_prepare_skips_long_answer():
    assert prepare({"taskId": 2, "number": 25, "text": "<p>x</p>", "key": "12345678 9\n" * 20}).skip is None  # ~200
    p = prepare({"taskId": 2, "number": 26, "text": "<p>x</p>", "key": "1 " * 130})
    assert p.skip and "255" in p.skip


def test_author_from_statement_goes_to_label():
    item = {"taskId": 3, "number": 8, "comment": "", "key": "5",
            "text": '<p><a href="https://shurukha.ru/" target="_blank"><strong>(А. Шуруха)</strong></a> Сколько слов?</p>'}
    p = prepare(item)
    assert p.payload["statement"] == "Сколько слов?"
    assert p.payload["source_label"] == "КЕГЭ (kompege.ru) · А. Шуруха"
    assert source_label("Демоверсия 2027", "Л. Шастин") == "КЕГЭ (kompege.ru) · Л. Шастин · Демоверсия 2027"
    assert prepare({**item, "text": "<p>(2, 3) — позиция</p>"}).payload["statement"] == "(2, 3) — позиция"


def test_question_19_mid_line():
    item = {"taskId": 4, "number": 19, "key": "29",
            "text": "<p>Игра такая. Будем говорить, что стратегия выигрышная. Известно, что Ваня выиграл. Укажите S.</p>",
            "subTask": [{"number": 20, "key": "1", "text": "<p>20?</p>"}, {"number": 21, "key": "2", "text": "<p>21?</p>"}]}
    p = prepare(item)
    assert p.payload["statement"] == "Игра такая. Будем говорить, что стратегия выигрышная."
    assert p.payload["parts"][0]["question"] == "Известно, что Ваня выиграл. Укажите S."


def test_open_variant_is_official():
    assert classify_source("Открытый вариант 2026") == "official"
    assert classify_source("ЕГКР 18.04.26") == "author"


def test_difficulty_mapping():
    base = {"taskId": 5, "number": 3, "text": "<p>x</p>", "key": "1"}
    assert [prepare({**base, "difficulty": d}).payload["difficulty"] for d in (0, 1, 2, 3, None)] == [40, 60, 75, 90, 50]


def test_python_code_block_marked():
    python = "<pre>for s in open('27_A.txt'):\n    x, y = map(float, s.split())\nprint(len(a))</pre>"
    assert md(python).startswith("```python\nfor s in open")
    pascal = "<pre>var i: integer;\nbegin\n  for i := 1 to 10 do writeln(i);\nend.</pre>"
    assert md(pascal).startswith("```\nvar i")
    cpp = "<pre>#include &lt;iostream&gt;\nint main() { for (int i = 0; i &lt; n; i++) cout &lt;&lt; i; }</pre>"
    assert md(cpp).startswith("```\n#include <iostream>")


def test_cyrillic_in_math_wrapped_in_text():
    assert md(r"<p>\(А_1 \times 10\)</p>") == r"$\text{А}_1 \times 10$"


# ---------- Готовые формулы из редактора kompege (Quill + KaTeX/MathML) ----------

QUILL = (
    '<p>Выражение: <span class="ql-formula" data-value="97968x15_{15} + 7x233_{15}">\ufeff<span contenteditable="false">'
    '<span class="katex"><span class="katex-mathml"><math><semantics><mrow><mn>97968</mn><mi>x</mi></mrow>'
    '<annotation encoding="application/x-tex">97968x15_{15} + 7x233_{15}</annotation></semantics></math></span>'
    '<span class="katex-html" aria-hidden="true"><span class="mord">97968</span><span class="mord">x</span></span>'
    "</span></span>\ufeff</span> кратно 14.</p>"
)


def test_quill_formula_uses_tex_once():
    result = html_to_markdown(QUILL)
    assert result.markdown == "Выражение: $97968x15_{15} + 7x233_{15}$ кратно 14."
    assert not result.warnings


def test_katex_without_quill_uses_annotation():
    html = ('<p><span class="katex"><span class="katex-mathml"><math><mi>n</mi>'
            r'<annotation encoding="application/x-tex">n \le 10</annotation></math></span>'
            '<span class="katex-html"><span class="mord">n</span></span></span></p>')
    assert md(html) == r"$n \le 10$"
    assert md("<p><math><mi>x</mi><annotation encoding='application/x-tex'>x^2</annotation></math></p>") == "$x^2$"


def test_katex_render_without_tex_keeps_visible_text():
    # Только отрисовка KaTeX, без MathML: остаётся видимый текст «S», как и раньше.
    html = '<p>значения <span class="katex"><span class="katex-html"><span class="mord mathit">S</span></span></span>, при</p>'
    assert md(html) == "значения S, при"


def test_empty_quill_leftover_keeps_order_and_ignores_data_value():
    # Quill оставляет пустой span с data-value рядом с обычной формулой — дублировать нельзя.
    html = r'<p><span class="ql-formula" data-value="F(x, 0) = x">\(F(x,0)=x\)<br></span>\(F(x,y)=F(y,x\%y)\)</p>'
    assert md(html) == "$F(x,0)=x$\n$F(x,y)=F(y,x\\%y)$"


def test_bom_does_not_hide_author():
    item = {"taskId": 7, "number": 16, "key": "1",
            "text": '<p>\ufeff\ufeff<a href="https://vk.com/x"><strong>(Е. Фокин)</strong></a> Функция</p>'}
    p = prepare(item)
    assert p.payload["source_label"] == "КЕГЭ (kompege.ru) · Е. Фокин"
    assert p.payload["statement"] == "Функция"


def test_question_19_new_beginnings():
    sub = [{"number": 20, "key": "1", "text": "<p>20?</p>"}, {"number": 21, "key": "2", "text": "<p>21?</p>"}]
    for question in ("При каких значениях S Петя выигрывает?", "Кто из игроков выигрывает?",
                     "У кого из игроков есть выигрышная стратегия?", "С какой карты Петя должен начать?"):
        p = prepare({"taskId": 8, "number": 19, "key": "3", "subTask": sub,
                     "text": f"<p>Правила игры.</p><p>{question}</p>"})
        assert p.payload["statement"] == "Правила игры." and not p.warnings, question
        assert p.payload["parts"][0]["question"] == question
