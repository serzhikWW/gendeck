"""Сборка user-сообщений планировщика: каталог с лимитами, таблицы БЕЗ ячеек, секции источника."""
from __future__ import annotations
from typing import Iterable, Optional

from ..contracts import IngestResult, SlideType, SourceSection, SourceTable
from .limits import SlideLimits


def _catalog_block(limits: dict[SlideType, SlideLimits]) -> str:
    lines = ["## Каталог типов слайдов шаблона (лимиты)"]
    lines += [lim.describe() for lim in limits.values()]
    missing = [t.value for t in SlideType if t not in limits]
    if missing:
        lines.append(f"(в шаблоне нет типов: {', '.join(missing)} — не используй их)")
    return "\n".join(lines)


def _table_owner(src: IngestResult) -> dict[str, str]:
    return {tid: s.section_id for s in src.sections for tid in s.table_ids}


def _tables_block(tables: Iterable[SourceTable], owner: dict[str, str]) -> str:
    tables = list(tables)
    lines = ["## Таблицы источника (ячейки не показаны — ссылайся по source_id)"]
    if not tables:
        lines.append("- нет")
    for t in tables:
        sec = f" (секция {owner[t.table_id]})" if t.table_id in owner else ""
        cap = f"«{t.caption}»; " if t.caption else ""
        lines.append(f"- {t.table_id}{sec}: {cap}колонки: {' | '.join(t.columns)}; строк: {len(t.rows)}")
    return "\n".join(lines)


def _sections_block(sections: Iterable[SourceSection], preview_chars: Optional[int] = None) -> str:
    lines = ["## Источник"]
    for s in sections:
        head = f"### [{s.section_id}] {s.heading}".rstrip()
        text = s.text.strip()
        if preview_chars is not None and len(text) > preview_chars:
            text = text[:preview_chars].rstrip() + " …"
        if s.table_ids:
            text = (text + "\n" if text else "") + f"(в секции таблицы: {', '.join(s.table_ids)})"
        lines.append(head + ("\n" + text if text else ""))
    return "\n\n".join(lines)


def build_user_message(src: IngestResult, limits: dict[SlideType, SlideLimits],
                       task: str = "Составь DeckPlan по правилам.", extra: str = "") -> str:
    parts = [_catalog_block(limits), _tables_block(src.tables, _table_owner(src)), _sections_block(src.sections)]
    if extra:
        parts.append(extra)
    parts.append("## Задание\n" + task)
    return "\n\n".join(parts)


def build_outline_message(src: IngestResult, task: str, preview_chars: int = 300) -> str:
    return "\n\n".join([_tables_block(src.tables, _table_owner(src)),
                        _sections_block(src.sections, preview_chars), "## Задание\n" + task])


def build_part_message(src: IngestResult, limits: dict[SlideType, SlideLimits], part_title: str,
                       section_ids: list[str], task: str) -> str:
    wanted = set(section_ids)
    secs = [s for s in src.sections if s.section_id in wanted]
    tids = {t for s in secs for t in s.table_ids}
    owner = _table_owner(src)
    return "\n\n".join([_catalog_block(limits), _tables_block([t for t in src.tables if t.table_id in tids], owner),
                        f"## Часть доклада: {part_title}", _sections_block(secs), "## Задание\n" + task])
