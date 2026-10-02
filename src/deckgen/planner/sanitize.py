"""Детерминированная санитизация плана LLM. Гарантии на выходе:
- таблицы только по source_id существующей SourceTable, ячейки пустые (их подставит Fitter);
- каждая таблица источника показана хотя бы на одном table-слайде;
- в буллетах нет чисел, которых нет в источнике (такие буллеты удаляются);
- evidence содержит только дословные цитаты; на каждое число в тексте слайда есть цитата с этим числом;
- первый слайд — title, последний — last (если такие макеты есть в шаблоне)."""
from __future__ import annotations
import re
from typing import Optional

from ..contracts import DeckPlan, Evidence, IngestResult, SlideSpec, SlideType, TableSpec, TemplateCatalog
from .numbers import find_quote, number_in_source, numbers_in, quote_in_source

_MARKER = re.compile(r"^\s*(?:[•\-–—*·▪►●]+|\d{1,2}[.)])\s+")
LAST_TITLE = "Спасибо за внимание"


def clean_bullet(b: str) -> str:
    return _MARKER.sub("", (b or "").replace("**", "")).strip()


def _match_table(t: TableSpec, src: IngestResult) -> Optional[str]:
    if t.source_id and any(x.table_id == t.source_id for x in src.tables):
        return t.source_id
    cols = [c.strip().lower() for c in t.columns]
    if cols:
        for x in src.tables:
            if [c.strip().lower() for c in x.columns] == cols:
                return x.table_id
    return None


def add_evidence(s: SlideSpec, raw: str) -> None:
    """Дополняет s.evidence дословными фрагментами источника для каждого числа в тексте слайда."""
    for text in [s.title, s.subtitle, *s.bullets]:
        toks = [n for n in numbers_in(text) if number_in_source(n, raw)]
        uncovered = [t for t in toks if not any(number_in_source(t, e.quote) for e in s.evidence)]
        if not uncovered:
            continue
        for q in find_quote(uncovered, raw, text):
            if not any(e.quote == q for e in s.evidence):
                s.evidence.append(Evidence(quote=q))


def sanitize(plan: DeckPlan, src: IngestResult, catalog: TemplateCatalog) -> tuple[DeckPlan, list[str]]:
    notes: list[str] = []
    raw = src.raw_text
    types_present = {l.slide_type for l in catalog.layouts}
    slides: list[SlideSpec] = []

    for i, s in enumerate(plan.slides):
        s = s.model_copy(deep=True)
        if s.type not in types_present and s.type in (SlideType.section, SlideType.table):
            s.type = SlideType.slide
        # --- таблицы: только ссылка на источник ---
        if s.table is not None:
            sid = _match_table(s.table, src)
            if sid:
                if s.table.source_id != sid:
                    notes.append(f"слайд {i + 1}: таблица сопоставлена с источником {sid}")
                s.table = TableSpec(source_id=sid, header_rows=s.table.header_rows or 1)
            else:
                notes.append(f"слайд {i + 1}: удалена таблица без source_id (таблицы генерировать нельзя)")
                s.table = None
        if s.type == SlideType.table and s.table is None:
            s.type = SlideType.slide
        # --- буллеты: маркеры, выдуманные числа ---
        bullets = []
        for b in s.bullets:
            b = clean_bullet(b)
            if not b:
                continue
            bad = [n for n in numbers_in(b) if not number_in_source(n, raw)]
            if bad:
                notes.append(f"слайд {i + 1}: удалён буллет с числами не из источника {bad}: «{b}»")
                continue
            bullets.append(b)
        s.bullets = bullets
        s.title = s.title.strip()
        s.subtitle = s.subtitle.strip()
        if s.type == SlideType.slide and not s.bullets and not s.title:
            notes.append(f"слайд {i + 1}: пустой слайд удалён")
            continue
        # --- evidence: только дословные цитаты + автоцитаты на каждое число ---
        kept = []
        for e in s.evidence:
            if quote_in_source(e.quote, raw):
                kept.append(Evidence(quote=e.quote.strip()))
            else:
                notes.append(f"слайд {i + 1}: удалена недословная цитата «{e.quote[:80]}»")
        s.evidence = kept
        add_evidence(s, raw)
        slides.append(s)

    # --- каждая таблица источника должна попасть в презентацию ---
    used = {s.table.source_id for s in slides if s.table}
    insert_at = len(slides) - 1 if slides and slides[-1].type == SlideType.last else len(slides)
    for t in src.tables:
        if t.table_id in used:
            continue
        title = t.caption or next((sec.heading for sec in src.sections if t.table_id in sec.table_ids), "") \
            or f"Таблица {t.table_id}"
        stype = SlideType.table if SlideType.table in types_present else SlideType.slide
        slides.insert(insert_at, SlideSpec(type=stype, title=title, table=TableSpec(source_id=t.table_id)))
        insert_at += 1
        notes.append(f"добавлен слайд для таблицы источника {t.table_id}")

    # --- титул и финал ---
    deck_title = plan.deck_title.strip()
    if SlideType.title in types_present and (not slides or slides[0].type != SlideType.title):
        slides.insert(0, SlideSpec(type=SlideType.title, title=deck_title))
        add_evidence(slides[0], raw)
        notes.append("добавлен титульный слайд")
    if SlideType.last in types_present and (not slides or slides[-1].type != SlideType.last):
        slides.append(SlideSpec(type=SlideType.last, title=LAST_TITLE))
        notes.append("добавлен финальный слайд")
    return DeckPlan(deck_title=deck_title or (slides[0].title if slides else ""), slides=slides), notes
