"""Измерения для Fitter и Renderer: высота блока буллетов, ширины колонок и высоты строк таблицы.
Renderer вызывает те же функции, поэтому геометрия в PPTX совпадает с расчётом Fitter-а."""
from __future__ import annotations

import re
from dataclasses import dataclass

from deckgen.contracts import BBox, TableSpec
from deckgen.template import metrics
from deckgen.template.metrics import EMU_PER_PT
from deckgen.template.textstyle import (BULLET_MARL_EMU, CELL_MAR_LR_EMU, CELL_MAR_TB_EMU, LN_SPC,
                                        PARA_SPACE_RATIO, TABLE_LN_SPC, TEXT_INSETS_EMU)

NUMERIC_RE = re.compile(r"^[\s<>≥≤~±+\-−–—]*\d[\d\s.,]*\s*(%|‰|\*+)?\s*$")


def is_numeric(cell: str) -> bool:
    """Число/процент/“—” — выравниваем вправо."""
    s = cell.strip()
    return bool(s) and (s in {"—", "–", "-"} or bool(NUMERIC_RE.match(s)))


# ---------- текст ----------
def text_area_pt(zone: BBox, insets=TEXT_INSETS_EMU) -> tuple[float, float]:
    l, t, r, b = insets
    return (zone.w - l - r) / EMU_PER_PT, (zone.h - t - b) / EMU_PER_PT


def bullets_height_pt(bullets: list[str], width_pt: float, font_pt: float, bulleted: bool = True) -> float:
    if not bullets:
        return 0.0
    w = width_pt - (BULLET_MARL_EMU / EMU_PER_PT if bulleted else 0)
    lines = sum(metrics.count_lines(b, w, font_pt) for b in bullets)
    return lines * metrics.line_height_pt(font_pt, LN_SPC) + (len(bullets) - 1) * PARA_SPACE_RATIO * font_pt


def text_fits(lines_of_text: list[str], zone: BBox, font_pt: float, bulleted: bool = True) -> bool:
    w, h = text_area_pt(zone)
    return bullets_height_pt(lines_of_text, w, font_pt, bulleted) <= h


def title_lines(title: str, zone: BBox, font_pt: float, insets=TEXT_INSETS_EMU) -> tuple[int, int]:
    """(нужно строк, помещается строк) для заголовка."""
    w, h = text_area_pt(zone, insets)
    need = metrics.count_lines(title, w, font_pt, bold=False)
    fit = max(1, int(h // metrics.line_height_pt(font_pt, LN_SPC)))
    return need, fit


# ---------- таблицы ----------
def table_grid(t: TableSpec) -> tuple[list[list[str]], int]:
    """Сетка ячеек (шапка + строки, выровненные по числу колонок пустыми строками) и число строк шапки."""
    ncols = max([len(t.columns)] + [len(r) for r in t.rows] + [1])
    pad = lambda r: list(r) + [""] * (ncols - len(r))
    grid = ([pad(t.columns)] if t.columns else []) + [pad(r) for r in t.rows]
    header_n = min(max(t.header_rows, 0), len(grid)) if t.columns else 0
    return grid, header_n


@dataclass
class TableLayout:
    col_w: list[int]       # EMU
    row_h: list[int]       # EMU
    font_pt: float
    fits_width: bool       # все слова помещаются в колонки без разрыва

    @property
    def height(self) -> int:
        return sum(self.row_h)


def _cell_pad_pt() -> float:
    return 2 * CELL_MAR_LR_EMU / EMU_PER_PT


def layout_table(grid: list[list[str]], header_n: int, width_emu: int, font_pt: float) -> TableLayout:
    ncols = len(grid[0]) if grid else 1
    W = width_emu / EMU_PER_PT
    pad = _cell_pad_pt() + 2  # +2pt запас на рамки
    natural = [0.0] * ncols
    minimal = [0.0] * ncols
    for ri, row in enumerate(grid):
        bold = ri < header_n
        for ci, cell in enumerate(row):
            for part in (cell or "").split("\n"):
                natural[ci] = max(natural[ci], metrics.text_width_pt(part, font_pt, bold) / metrics.WIDTH_SAFETY + pad)
                for word in part.split():
                    minimal[ci] = max(minimal[ci], metrics.text_width_pt(word, font_pt, bold) / metrics.WIDTH_SAFETY + pad)
    natural = [max(n, pad + font_pt) for n in natural]
    minimal = [max(m, pad + font_pt) for m in minimal]
    if sum(natural) <= W:
        k = W / sum(natural)
        widths = [n * k for n in natural]
    elif sum(minimal) <= W:
        extra = W - sum(minimal)
        want = [n - m for n, m in zip(natural, minimal)]
        tot = sum(want) or 1.0
        widths = [m + extra * w / tot for m, w in zip(minimal, want)]
    else:
        k = W / sum(minimal)
        widths = [m * k for m in minimal]
    fits_width = sum(minimal) <= W + 0.01
    col_w = [int(w * EMU_PER_PT) for w in widths]
    col_w[-1] += width_emu - sum(col_w)  # добиваем округление
    lh = metrics.line_height_pt(font_pt, TABLE_LN_SPC)
    row_h = []
    for ri, row in enumerate(grid):
        bold = ri < header_n
        n = max(metrics.count_lines(c or " ", (cw / EMU_PER_PT) - _cell_pad_pt(), font_pt, bold)
                for c, cw in zip(row, col_w))
        row_h.append(int((n * lh) * EMU_PER_PT + 2 * CELL_MAR_TB_EMU + 2 * EMU_PER_PT))
    return TableLayout(col_w=col_w, row_h=row_h, font_pt=font_pt, fits_width=fits_width)
