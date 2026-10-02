"""План без LLM (деградация): секции -> слайды с буллетами-предложениями источника, таблицы -> table-слайды.
Текст копируется дословно, поэтому числа не искажаются; evidence — те же фрагменты источника."""
from __future__ import annotations
import re
from typing import Optional

from ..contracts import DeckPlan, IngestResult, Issue, SlideSpec, SlideType, TableSpec, TemplateCatalog
from .limits import DEFAULT_BULLETS, catalog_limits
from .numbers import sentences
from .sanitize import sanitize

CONT = " (продолжение)"


_TABLE_PREFIX = re.compile(r"^\s*Таблица\s+\d+[.:]?\s*", re.I)


def _bullets(text: str, captions: set[str]) -> list[str]:
    """Предложения секции без строк таблиц и без подписей таблиц (они станут заголовками table-слайдов)."""
    lines = [l for l in (text or "").splitlines()
             if not l.lstrip().startswith("|") and l.strip() not in captions]
    return sentences("\n".join(lines))


def _table_title(caption: str, fallback: str) -> str:
    return _TABLE_PREFIX.sub("", caption).strip() or caption.strip() or fallback


def _default_catalog() -> TemplateCatalog:
    from ..contracts import BBox, FieldKind, FieldSpec, LayoutSpec
    f = lambda n, k: FieldSpec(name=n, kind=k, bbox=BBox(x=0, y=0, w=0, h=0))
    return TemplateCatalog(template_id="default", adapter="convention", slide_w=0, slide_h=0, layouts=[
        LayoutSpec(layout_id=t.value, slide_type=t, fields=[f("TITLE_FIELD", FieldKind.title)]
                   + ([f("TEXT_FIELD", FieldKind.text)] if t in (SlideType.slide, SlideType.table) else [])
                   + ([f("TABLE_FIELD", FieldKind.table)] if t == SlideType.table else []))
        for t in SlideType])


def plan_without_llm(src: IngestResult, catalog: Optional[TemplateCatalog] = None) -> DeckPlan:
    catalog = catalog or _default_catalog()
    limits = catalog_limits(catalog)
    max_b = limits[SlideType.slide].max_bullets if SlideType.slide in limits else DEFAULT_BULLETS
    tables = {t.table_id: t for t in src.tables}

    sections = list(src.sections)
    first_line = next((l.strip("# ").strip() for l in src.raw_text.splitlines() if l.strip()), "Презентация")
    deck_title = next((s.heading for s in sections if s.heading), first_line)[:150]
    subtitle = ""
    if sections and sections[0].heading == deck_title and not sections[0].table_ids:
        t = sections[0].text.strip()
        if t and len(t) <= 200 and len(t.splitlines()) <= 2:
            subtitle = t.splitlines()[0].strip()
            sections = sections[1:]
        elif not t:
            sections = sections[1:]

    slides = [SlideSpec(type=SlideType.title, title=deck_title, subtitle=subtitle)]
    for sec in sections:
        title = sec.heading or deck_title
        caps = {tables[t].caption.strip() for t in sec.table_ids if t in tables and tables[t].caption.strip()}
        bl = _bullets(sec.text, caps)
        for k in range(0, len(bl), max_b):
            slides.append(SlideSpec(type=SlideType.slide, title=title + (CONT if k else ""), bullets=bl[k:k + max_b]))
        for tid in sec.table_ids:
            if tid in tables:
                slides.append(SlideSpec(type=SlideType.table, title=_table_title(tables[tid].caption, title),
                                        table=TableSpec(source_id=tid)))
    plan, _ = sanitize(DeckPlan(deck_title=deck_title, slides=slides), src, catalog)  # evidence, титул/финал, все таблицы
    return plan


class FallbackPlanner:
    """Planner-протокол без LLM: детерминированный план из секций (feedback игнорируется)."""
    def plan(self, src: IngestResult, catalog: TemplateCatalog, feedback: Optional[list[Issue]] = None) -> DeckPlan:
        return plan_without_llm(src, catalog)
