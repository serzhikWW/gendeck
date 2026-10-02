"""Геометрия зон слайда: куда реально ставится текст и таблица на выбранном макете.

Общая для Fitter (оценка ёмкости) и Renderer (позиционирование), чтобы они не разошлись.
Правило: неиспользуемые поля (IMAGE всегда; TEXT, если текста нет) в той же горизонтальной
полосе отдаются соседней зоне — например, в `table_only1` таблица занимает и место картинки.
"""
from __future__ import annotations

from typing import Optional

from deckgen.contracts import BBox, FieldKind, FieldSpec, LayoutSpec


def fields_of(layout: LayoutSpec, kind: FieldKind) -> list[FieldSpec]:
    return [f for f in layout.fields if f.kind == kind]


def first_field(layout: LayoutSpec, kind: FieldKind) -> Optional[FieldSpec]:
    fs = fields_of(layout, kind)
    return fs[0] if fs else None


def _same_band(a: BBox, b: BBox) -> bool:
    top, bottom = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    return bottom - top >= 0.5 * min(a.h, b.h)


def _union(a: BBox, b: BBox) -> BBox:
    x0, y0 = min(a.x, b.x), min(a.y, b.y)
    x1, y1 = max(a.x + a.w, b.x + b.w), max(a.y + a.h, b.y + b.h)
    return BBox(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def _grow(zone: BBox, donors: list[FieldSpec]) -> BBox:
    for d in donors:
        if _same_band(zone, d.bbox):
            # растём только по горизонтали: высота зоны остаётся своей
            u = _union(zone, d.bbox)
            zone = BBox(x=u.x, y=zone.y, w=u.w, h=zone.h)
    return zone


def content_zones(layout: LayoutSpec, has_text: bool, has_table: bool) -> dict[str, FieldSpec]:
    """Возвращает {'text': FieldSpec, 'table': FieldSpec} с итоговыми bbox (копии полей).
    Если в макете нет TABLE-поля, таблица ставится в зону TEXT (только когда текста нет)."""
    text_f = first_field(layout, FieldKind.text)
    table_f = first_field(layout, FieldKind.table)
    images = fields_of(layout, FieldKind.image)
    out: dict[str, FieldSpec] = {}
    if has_table:
        if table_f is not None:
            donors = list(images) + ([text_f] if text_f is not None and not has_text else [])
            out["table"] = table_f.model_copy(update={"bbox": _grow(table_f.bbox, donors)})
        elif text_f is not None and not has_text:
            out["table"] = text_f.model_copy(update={"bbox": _grow(text_f.bbox, images), "kind": FieldKind.table})
    if has_text and text_f is not None:
        donors = list(images) + ([table_f] if table_f is not None and not has_table else [])
        out["text"] = text_f.model_copy(update={"bbox": _grow(text_f.bbox, donors)})
    return out
