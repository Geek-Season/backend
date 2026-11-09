"""HTML задачи kompege.ru → наш Markdown (components/Markdown.jsx на фронте).

Что умеет наш Markdown: GFM (таблицы, списки), переносы строк одиночным \\n,
формулы KaTeX в $…$ и $$…$$, из «сырого» HTML — только <u> <sub> <sup> <mark> <s> <br>.

Что встречается у kompege: <p>, <br>, <strong>, <table> со стилями, формулы MathJax
в \\(…\\) и \\[…\\], картинки прямо в тексте как data:image/png;base64,….

Картинки конвертер не загружает: он ставит в текст ![](pending:N) и возвращает
список картинок, а импорт подставит настоящие /files/… после загрузки — как админка.
"""

import base64
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Теги, которые наш Markdown понимает сам (markdownTags.js) — оставляем как есть.
KEEP_TAGS = {"sub": "sub", "sup": "sup", "u": "u", "ins": "u", "s": "s", "strike": "s", "del": "s", "mark": "mark"}
BOLD = {"strong", "b"}
ITALIC = {"em", "i"}
BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "center", "section", "article"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Теги без смысла для нас: снимаем, текст оставляем, в отчёт не пишем.
TRANSPARENT = {"span", "font", "tbody", "thead", "tfoot", "colgroup", "col", "html", "body", "label", "small", "big",
               "nobr", "wbr", "caption", "figure", "figcaption", "o:p"}
VOID = {"br", "img", "col", "hr", "input", "meta", "link", "wbr"}

# Символы, которые Markdown прочитал бы как разметку. $ — из-за формул KaTeX.
_ESCAPE = re.compile(r"([\\`*_\[\]$<|~])")
_LINE_START = re.compile(r"^(\s*)([#>+\-]|\d+[.)])")
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
# Юникод-символы логики/математики внутри формул → команды LaTeX (KaTeX понимает не все символы).
MATH_SYMBOLS = {"∧": r"\land ", "∨": r"\lor ", "¬": r"\neg ", "≡": r"\equiv ", "→": r"\to ", "⇒": r"\Rightarrow ",
                "↔": r"\leftrightarrow ", "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "×": r"\times ", "·": r"\cdot ",
                "−": "-", "÷": r"\div ", "∙": r"\cdot ", "\xa0": " ", "⊕": r"\oplus "}


@dataclass
class Image:
    """Картинка из текста: либо байты (data:…), либо адрес, который надо скачать."""

    data: bytes | None = None
    src: str | None = None


@dataclass
class Converted:
    markdown: str
    images: list[Image] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_PYTHON = re.compile(
    r"^\s*(def \w+\(|import \w|from \w+ import|for \w+(, *\w+)* in |while .+:\s*$|elif |print\()"
    r"|\b(range|len|open|int|input)\(|\.split\(|\.append\(|lambda ",
    re.M,
)
_NOT_PYTHON = re.compile(
    r"\bbegin\b|\bend[;.]|:=|#include|\bint main\b|\bcout\b|\bcin\b|\bwriteln\b|\bvar\b\s*$"
    r"|\bpublic static\b|\bDim\b|\bconsole\.log\b|;\s*$",
    re.M | re.I,
)


def looks_like_python(code: str) -> bool:
    """Код из разбора — Python? Тогда помечаем блок ```python (язык виден в тексте; пригодится для подсветки).

    Признаки Python — def/for … in/print(/range(…; признаки Паскаля, C++, Java и т. п.
    (begin, :=, #include, «;» в конце строки) — значит, не Python.
    """
    if _NOT_PYTHON.search(code):
        return False
    return len(_PYTHON.findall(code)) >= 2 or bool(re.search(r"^\s*def \w+\(.*\)\s*:", code, re.M))


class _Buffer:
    def __init__(self, tag: str):
        self.tag = tag
        self.parts: list[str] = []

    def text(self) -> str:
        return "".join(self.parts)


class _Table:
    def __init__(self):
        self.rows: list[list[str]] = []
        self.spans = False


class _Converter(HTMLParser):
    def __init__(self, image_offset: int):
        super().__init__(convert_charrefs=True)
        self.stack: list[_Buffer] = [_Buffer("root")]
        self.tables: list[_Table] = []
        self.lists: list[list] = []  # [тег, номер следующего пункта]
        self.images: list[Image] = []
        self.image_offset = image_offset
        self.warnings: list[str] = []
        self.in_math = None  # None или закрывающий разделитель: ")" / "]"
        self.math_start = 0  # с какого куска out.parts началась текущая формула
        self.pre = 0
        self.link: list[str | None] = []
        # Готовая формула из редактора (Quill/KaTeX/MathML): пока открыт её корневой тег, текст
        # внутри не выводим — там отрисовка, а TeX берём из data-value или <annotation>.
        self.formula: dict | None = None  # {"tag", "depth", "tex", "in_annotation"}

    # ---------- вывод ----------

    @property
    def out(self) -> _Buffer:
        return self.stack[-1]

    def emit(self, text: str) -> None:
        self.out.parts.append(text)

    def block_break(self) -> None:
        """Граница абзаца. В ячейке таблицы абзацев нет — там перенос <br>."""
        if self.out.tag in ("td", "th"):
            if self.out.text().strip():
                self.emit("<br>")
            return
        self.emit("\n\n")

    def at_line_start(self) -> bool:
        text = self.out.text()
        return not text.strip(" ") or text.endswith("\n")

    # ---------- текст ----------

    def handle_data(self, data: str) -> None:
        if self.formula is not None:
            if self.formula["in_annotation"]:
                self.formula["annotation"] += data
            else:
                self.formula["text"] += data
                self.formula["events"].append(data)
            return
        data = data.replace("\ufeff", "")  # невидимые BOM, которые Quill ставит вокруг формул
        if self.pre:
            self.emit(data)
            return
        data = _SPACES.sub(" ", data.replace("\xa0", " "))
        # Делим текст на обычный и формулы; формула может начаться в одном куске текста, а кончиться в другом.
        pos = 0
        while pos < len(data):
            if self.in_math is None:
                found = re.search(r"\\\(|\\\[", data[pos:])
                if not found:
                    self.emit_text(data[pos:])
                    break
                self.emit_text(data[pos:pos + found.start()])
                self.in_math = ")" if found.group() == r"\(" else "]"
                self.emit("$" if self.in_math == ")" else "\n\n$$\n")
                self.math_start = len(self.out.parts)
                pos += found.end()
            else:
                end = data.find("\\" + self.in_math, pos)
                chunk = data[pos:] if end < 0 else data[pos:end]
                self.emit(self.math(chunk))
                if end < 0:
                    break
                self.close_math()
                pos = end + 2

    def close_math(self) -> None:
        # Внутри $…$ не должно быть пробелов по краям — иначе remark-math не узнает формулу.
        parts = self.out.parts
        body = "".join(parts[self.math_start:]).strip()
        del parts[self.math_start:]
        parts.append(body)
        self.emit("$" if self.in_math == ")" else "\n$$\n\n")
        self.in_math = None

    def math(self, text: str) -> str:
        for symbol, latex in MATH_SYMBOLS.items():
            text = text.replace(symbol, latex)
        # Кириллица в формуле ($А_1$): KaTeX ругается на неё вне \text{} — оборачиваем.
        text = re.sub(r"[А-Яа-яЁё]+", lambda m: rf"\text{{{m.group()}}}", text)
        if self.out.tag in ("td", "th"):
            text = text.replace("|", r"\|")  # GFM режет ячейки по |, даже внутри формулы
        return text

    def emit_text(self, text: str) -> None:
        if not text:
            return
        text = _ESCAPE.sub(r"\\\1", text)
        if self.at_line_start():
            text = _LINE_START.sub(r"\1\\\2", text, count=1)
        self.emit(text)

    # ---------- теги ----------

    # ---------- готовые формулы (Quill: <span class="ql-formula" data-value="TeX">, KaTeX, MathML) ----------

    def formula_start(self, tag: str, attrs: dict) -> bool:
        """Тег открывает готовую формулу? Тогда начинаем её захват и возвращаем True."""
        classes = (attrs.get("class") or "").split()
        if tag == "span" and "ql-formula" in classes:
            tex = attrs.get("data-value")
        elif (tag == "span" and "katex" in classes) or tag == "math":
            tex = None  # возьмём из <annotation encoding="application/x-tex">
        else:
            return False
        self.formula = {"tag": tag, "depth": 1, "tex": tex, "in_annotation": False, "annotation": "", "text": "",
                        # Отрисована ли формула внутри. Quill оставляет пустые <span class="ql-formula"
                        # data-value="n = 1"><br></span> рядом с обычным \(n=1\) — их data-value дублирует текст.
                        "rendered": tag == "math" or "katex" in classes,
                        "events": []}  # текст и <br> по порядку — чтобы вывести остаток как есть
        return True

    def formula_tag(self, tag: str, attrs: dict | None) -> None:
        """Тег внутри захватываемой формулы: только следим за вложенностью и <annotation>."""
        f = self.formula
        if attrs is not None and (tag == "math" or "katex" in (attrs.get("class") or "").split()):
            f["rendered"] = True
        if tag == "annotation":
            f["in_annotation"] = attrs is not None and "tex" in (attrs.get("encoding") or "")
            return
        if tag == f["tag"]:
            f["depth"] += 1 if attrs is not None else -1
        if f["depth"] == 0:
            self.formula = None
            tex = (f["tex"] if f["tex"] is not None else f["annotation"]).strip()
            if not f["rendered"]:
                # Пустой остаток редактора: формулы в нём нет, выводим его содержимое как обычный span.
                for event in f["events"]:
                    if event is None:
                        self.handle_starttag("br", [])
                    else:
                        self.handle_data(event)
            elif tex:
                self.emit("$" + self.math(tex) + "$")
            elif f["text"].strip():
                # Исходного TeX нет (только отрисовка KaTeX, например «S») — оставляем видимый текст, как раньше.
                self.handle_data(f["text"])
            else:
                self.warn("формула без исходного TeX и текста — пропущена, проверить вручную")

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if self.formula is not None:
            if tag == "br":
                self.formula["events"].append(None)
            elif tag not in VOID:
                self.formula_tag(tag, attrs)
            return
        if self.formula_start(tag, attrs):
            return
        if tag == "br":
            self.emit("<br>" if self.out.tag in ("td", "th") else "\n")
        elif tag == "img":
            self.image(attrs.get("src") or "")
        elif tag == "hr":
            self.emit("\n\n---\n\n")
        elif tag in BLOCKS:
            self.block_break()
            if tag in HEADINGS:
                self.stack.append(_Buffer(tag))
        elif tag in BOLD or tag in ITALIC or tag in KEEP_TAGS or tag == "a":
            if tag == "a":
                self.link.append(attrs.get("href"))
            self.stack.append(_Buffer(tag))
        elif tag == "table":
            self.block_break()
            self.tables.append(_Table())
        elif tag == "tr":
            if self.tables:
                self.tables[-1].rows.append([])
        elif tag in ("td", "th"):
            if attrs.get("colspan", "1") != "1" or attrs.get("rowspan", "1") != "1":
                if self.tables:
                    self.tables[-1].spans = True
            self.stack.append(_Buffer(tag))
        elif tag in ("ul", "ol"):
            self.block_break()
            self.lists.append([tag, int(attrs.get("start") or 1)])
        elif tag == "li":
            kind = self.lists[-1] if self.lists else ["ul", 1]
            indent = "   " * max(len(self.lists) - 1, 0)
            if kind[0] == "ol":
                marker = f"{kind[1]}. "
                kind[1] += 1
            else:
                marker = "- "
            self.emit(("\n" if self.out.text().strip() else "") + indent + marker)
        elif tag == "pre":
            self.block_break()
            self.stack.append(_Buffer("pre"))
            self.pre += 1
        elif tag == "code":
            if not self.pre:
                self.stack.append(_Buffer("code"))
        elif tag in TRANSPARENT:
            pass
        else:
            self.warn(f"неизвестный тег <{tag}> — оставлен только текст")

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if self.formula is not None:
            if tag == "annotation":
                self.formula["in_annotation"] = False
            else:
                self.formula_tag(tag, None)
            return
        if tag in HEADINGS:
            self.close_inline(tag)
            self.block_break()
        elif tag in BLOCKS:
            self.block_break()
        elif tag in BOLD or tag in ITALIC or tag in KEEP_TAGS or tag == "a":
            self.close_inline(tag)
        elif tag in ("td", "th"):
            if self.out.tag in ("td", "th"):
                cell = self.stack.pop().text()
                cell = re.sub(r"(<br>\s*)+$", "", cell.replace("\n", " ")).strip()
                if self.tables and self.tables[-1].rows:
                    self.tables[-1].rows[-1].append(cell)
        elif tag == "table":
            if self.tables:
                self.emit(self.render_table(self.tables.pop()))
                self.block_break()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.block_break()
        elif tag == "pre":
            if self.pre and self.out.tag == "pre":
                self.pre -= 1
                code = self.stack.pop().text().strip("\n")
                language = "python" if looks_like_python(code) else ""
                self.emit(f"```{language}\n{code}\n```")
                self.block_break()
        elif tag == "code":
            if not self.pre and self.out.tag == "code":
                code = self.stack.pop().text().replace("\\", "")  # экранирование внутри `…` не нужно
                self.emit(f"`{code}`" if code.strip() else code)

    def close_inline(self, tag: str) -> None:
        if self.out.tag != tag:
            return  # кривой HTML: закрывающий тег без открывающего — пропускаем
        inner = self.stack.pop().text()
        # «** текст **» Markdown не считает жирным: пробелы выносим наружу.
        lead = inner[: len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        body = inner.strip()
        if not body:
            self.emit(inner)
            return
        if tag in BOLD or tag in HEADINGS:
            body = f"**{body}**"
        elif tag in ITALIC:
            body = f"*{body}*"
        elif tag in KEEP_TAGS:
            name = KEEP_TAGS[tag]
            body = f"<{name}>{body}</{name}>"
        elif tag == "a":
            href = self.link.pop() if self.link else None
            if href and href.startswith(("http://", "https://")):
                body = f"[{body}]({href})"
        self.emit(lead + body + trail)

    def image(self, src: str) -> None:
        match = re.match(r"data:image/[\w.+-]+;base64,(.*)", src, re.S)
        if match:
            try:
                image = Image(data=base64.b64decode(match.group(1)))
            except ValueError:
                self.warn("картинка data: не раскодировалась — пропущена")
                return
        elif src:
            image = Image(src=src)
        else:
            return
        self.images.append(image)
        self.emit(f"![](pending:{self.image_offset + len(self.images) - 1})")

    def render_table(self, table: _Table) -> str:
        rows = [row for row in table.rows if row]
        if not rows:
            return ""
        if table.spans:
            self.warn("в таблице есть объединённые ячейки (colspan/rowspan) — проверить вручную")
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
        lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
        return "\n".join(lines)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def finish(self) -> str:
        if self.in_math is not None:
            self.warn("формула без закрывающего разделителя")
            self.close_math()
        while len(self.stack) > 1:  # незакрытые теги: оставляем текст как есть
            text = self.stack.pop().text()
            self.emit(text)
        return self.stack[0].text()


_LIST_ITEM = re.compile(r"^\s+(-|\d+\.) ")


def _tidy(text: str) -> str:
    """Убрать пробелы по краям строк (кроме кода и вложенных списков) и лишние пустые строки."""
    lines, in_code = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            lines.append(line.strip())
        elif in_code:
            lines.append(line.rstrip())
        else:
            lines.append(line.rstrip() if _LIST_ITEM.match(line) else line.strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_markdown(html: str, image_offset: int = 0) -> Converted:
    """Перевести HTML в Markdown. image_offset — с какого номера нумеровать pending:N (сквозная нумерация)."""
    parser = _Converter(image_offset)
    parser.feed(html or "")
    parser.close()
    text = _tidy(parser.finish())
    return Converted(markdown=text.strip(), images=parser.images, warnings=parser.warnings)
