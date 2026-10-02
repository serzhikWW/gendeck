"""Лимиты типов слайдов из TemplateCatalog — для промпта и самопроверки плана."""
from __future__ import annotations
from dataclasses import dataclass

from ..contracts import DeckPlan, FieldKind, SlideType, TemplateCatalog

DEFAULT_TITLE_CHARS = 70
DEFAULT_SUBTITLE_CHARS = 100
DEFAULT_BULLETS = 6
DEFAULT_BULLET_CHARS = 120


@dataclass
class SlideLimits:
    slide_type: SlideType
    title_chars: int = DEFAULT_TITLE_CHARS
    has_subtitle: bool = False
    subtitle_chars: int = DEFAULT_SUBTITLE_CHARS
    has_text: bool = False
    max_bullets: int = 0
    bullet_chars: int = 0
    has_table: bool = False

    def describe(self) -> str:
        parts = [f"заголовок ≤ {self.title_chars} симв."]
        if self.has_subtitle:
            parts.append(f"подзаголовок ≤ {self.subtitle_chars} симв.")
        if self.has_table:
            parts.append("таблица источника по source_id")
        if self.has_text:
            parts.append(f"до {self.max_bullets} буллетов по ≤ {self.bullet_chars} симв.")
        return f"- {self.slide_type.value}: " + "; ".join(parts)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _text_capacity(max_chars: int, max_lines: int) -> tuple[int, int]:
    """(макс. буллетов, макс. символов в буллете). Эвристика: буллет занимает ~2 строки поля."""
    if max_lines > 0 and max_chars > 0:
        per_line = max_chars / max_lines
        return _clamp(max_lines // 2, 2, 7), _clamp(int(per_line * 2), 40, 200)
    if max_lines > 0:
        return _clamp(max_lines // 2, 2, 7), DEFAULT_BULLET_CHARS
    if max_chars > 0:
        return DEFAULT_BULLETS, _clamp(max_chars // DEFAULT_BULLETS, 40, 200)
    return DEFAULT_BULLETS, DEFAULT_BULLET_CHARS


def catalog_limits(catalog: TemplateCatalog) -> dict[SlideType, SlideLimits]:
    """Для каждого типа, который есть в шаблоне, — самые «вместительные» лимиты среди его макетов."""
    out: dict[SlideType, SlideLimits] = {}
    for st in SlideType:
        layouts = catalog.by_type(st)
        if not layouts:
            continue
        lim = SlideLimits(slide_type=st)
        titles = [f.max_chars for l in layouts for f in l.fields if f.kind == FieldKind.title and f.max_chars]
        if titles:
            lim.title_chars = max(titles)
        subs = [f for l in layouts for f in l.fields if f.kind == FieldKind.subtitle]
        if subs:
            lim.has_subtitle = True
            lim.subtitle_chars = max((f.max_chars for f in subs), default=0) or DEFAULT_SUBTITLE_CHARS
        texts = [f for l in layouts for f in l.fields if f.kind == FieldKind.text]
        if texts:
            caps = [_text_capacity(f.max_chars, f.max_lines) for f in texts]
            lim.has_text = True
            lim.max_bullets, lim.bullet_chars = max(caps, key=lambda c: c[0] * c[1])
        lim.has_table = any(f.kind == FieldKind.table for l in layouts for f in l.fields)
        out[st] = lim
    return out


def limit_violations(plan: DeckPlan, limits: dict[SlideType, SlideLimits]) -> list[tuple[int, str]]:
    """[(индекс слайда плана, описание)] — нарушения лимитов каталога (Fitter бы это сокращал/разбивал)."""
    out: list[tuple[int, str]] = []
    for i, s in enumerate(plan.slides):
        lim = limits.get(s.type)
        if lim is None:
            continue
        if len(s.title) > lim.title_chars:
            out.append((i, f"заголовок {len(s.title)} симв., лимит {lim.title_chars}"))
        if lim.has_subtitle and len(s.subtitle) > lim.subtitle_chars:
            out.append((i, f"подзаголовок {len(s.subtitle)} симв., лимит {lim.subtitle_chars}"))
        if s.bullets:
            if not lim.has_text:
                out.append((i, f"у типа {s.type.value} нет текстового поля — буллеты не поместятся"))
            else:
                if len(s.bullets) > lim.max_bullets:
                    out.append((i, f"{len(s.bullets)} буллетов, лимит {lim.max_bullets}"))
                long = [len(b) for b in s.bullets if len(b) > lim.bullet_chars]
                if long:
                    out.append((i, f"буллеты длиной {long} симв., лимит {lim.bullet_chars}"))
    return out
