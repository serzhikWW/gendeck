"""Метрики текста Liberation Sans: ширина строки, перенос по словам, оценка ёмкости зоны.

Используется профайлером (max_chars/max_lines в каталоге) и Fitter-ом (точная проверка
"влезает ли текст" и ширины колонок таблиц). Всё детерминировано.

Поиск шрифта: $DECKGEN_FONT_PATH -> системный Liberation Sans -> Arial (метрически
совместим с Liberation Sans, ширины глифов совпадают) -> грубая оценка 0.55 em/символ.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import ImageFont

EMU_PER_PT = 12700
EMU_PER_INCH = 914400

# Межстрочный интервал Liberation Sans (ascent+descent) в долях кегля при lnSpc=100%.
LINE_HEIGHT_EM = 1.15
# Запас на расхождение рендеров (LibreOffice / Р7 / PowerPoint переносят чуть по-разному).
WIDTH_SAFETY = 0.94
_MEASURE_PT = 100  # кегль, на котором меряем ширины (потом масштабируем линейно)

_REGULAR = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/LiberationSans-Regular.ttf",
    "/Library/Fonts/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]
_BOLD = [p.replace("Regular", "Bold").replace("arial.ttf", "arialbd.ttf").replace("Arial.ttf", "Arial_Bold.ttf")
         for p in _REGULAR]


def _find(candidates: list[str], env: str) -> Optional[str]:
    override = os.environ.get(env)
    if override and Path(override).is_file():
        return override
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


@lru_cache(maxsize=2)
def _font(bold: bool) -> Optional[ImageFont.FreeTypeFont]:
    path = _find(_BOLD, "DECKGEN_FONT_BOLD_PATH") if bold else None
    path = path or _find(_REGULAR, "DECKGEN_FONT_PATH")
    if not path:
        return None
    return ImageFont.truetype(path, _MEASURE_PT)


def font_source() -> str:
    """Какой файл шрифта реально используется для метрик (для логов/отчёта)."""
    f = _font(False)
    return f.path if f is not None else "heuristic(0.55em)"


@lru_cache(maxsize=20000)
def _width_at_measure(text: str, bold: bool) -> float:
    f = _font(bold)
    if f is None:
        return len(text) * 0.55 * _MEASURE_PT * (1.06 if bold else 1.0)
    return f.getlength(text)


def text_width_pt(text: str, size_pt: float, bold: bool = False) -> float:
    """Ширина строки (без переносов) в пунктах."""
    return _width_at_measure(text, bold) * size_pt / _MEASURE_PT


def wrap(text: str, width_pt: float, size_pt: float, bold: bool = False) -> list[str]:
    """Жадный перенос по словам (как в LibreOffice/Р7). Слово длиннее строки режется по символам.
    Явные переводы строк сохраняются."""
    avail = width_pt * WIDTH_SAFETY
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        line = ""
        for w in words:
            cand = f"{line} {w}" if line else w
            if text_width_pt(cand, size_pt, bold) <= avail:
                line = cand
                continue
            if line:
                out.append(line)
            # слово само по себе длиннее строки — режем по символам
            while text_width_pt(w, size_pt, bold) > avail and len(w) > 1:
                cut = len(w) - 1
                while cut > 1 and text_width_pt(w[:cut], size_pt, bold) > avail:
                    cut -= 1
                out.append(w[:cut])
                w = w[cut:]
            line = w
        out.append(line)
    return out


def count_lines(text: str, width_pt: float, size_pt: float, bold: bool = False) -> int:
    return max(1, len(wrap(text, width_pt, size_pt, bold)))


def line_height_pt(size_pt: float, ln_spc: float = 1.0) -> float:
    return size_pt * LINE_HEIGHT_EM * ln_spc


# Типичный русский деловой текст: используем для средней ширины символа при оценке ёмкости.
_SAMPLE = ("Выработка электроэнергии за 2024 год составила 19,1 млрд кВт·ч, что на 3,2% выше "
           "уровня прошлого года; установленная мощность станций — 5 120 МВт.")


def avg_char_width_pt(size_pt: float, bold: bool = False) -> float:
    return text_width_pt(_SAMPLE, size_pt, bold) / len(_SAMPLE)


def capacity(w_emu: int, h_emu: int, size_pt: float, ln_spc: float = 1.0,
             insets_emu: tuple[int, int, int, int] = (91440, 45720, 91440, 45720),
             bold: bool = False) -> tuple[int, int]:
    """Оценка ёмкости прямоугольника: (max_chars, max_lines).
    insets = (left, top, right, bottom) в EMU — по умолчанию стандартные поля текстовой рамки."""
    l, t, r, b = insets_emu
    w_pt = max(0.0, (w_emu - l - r) / EMU_PER_PT)
    h_pt = max(0.0, (h_emu - t - b) / EMU_PER_PT)
    max_lines = max(1, math.floor(h_pt / line_height_pt(size_pt, ln_spc)))
    per_line = max(1, math.floor(w_pt * WIDTH_SAFETY / avg_char_width_pt(size_pt, bold)))
    # ~85% — переносы по словам оставляют хвосты строк пустыми
    return int(per_line * max_lines * 0.85), max_lines
