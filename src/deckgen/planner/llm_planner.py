"""LLMPlanner — реализация Planner (contracts.py). Все вызовы модели — через llm/base.py (LLMClient).

Короткий вход: один вызов -> DeckPlan. Длинный (> chunk_chars): outline -> слайды по частям (map-reduce).
После ответа — детерминированная санитизация (sanitize.py) и самопроверка лимитов каталога
(если > 5% слайдов вне лимитов — один repair-вызов). Planner хранит последний план (last_plan), чтобы
repair по feedback валидатора правил именно его."""
from __future__ import annotations
import os
from typing import Optional, Protocol, Type, TypeVar

from pydantic import BaseModel

from ..contracts import DeckPlan, IngestResult, Issue, SlideSpec, SlideType, TemplateCatalog
from ..llm.base import LLMClient, LLMError
from .feedback import issues_to_feedback, violations_to_feedback
from .limits import SlideLimits, catalog_limits, limit_violations
from .prompt_builder import build_outline_message, build_part_message, build_user_message
from .prompts import load_prompts
from .sanitize import sanitize

T = TypeVar("T", bound=BaseModel)
SELF_CHECK_SHARE = 0.05


class _Generator(Protocol):
    def generate(self, messages: list[dict], schema: Type[T]) -> T: ...


# ---- внутренние схемы ответов map-reduce (не межмодульные, поэтому не в contracts.py) ----
class OutlinePart(BaseModel):
    title: str
    section_ids: list[str]


class Outline(BaseModel):
    deck_title: str
    subtitle: str = ""
    parts: list[OutlinePart]


class SlideBatch(BaseModel):
    slides: list[SlideSpec]


class LLMPlanner:
    def __init__(self, client: Optional[_Generator] = None, *, chunk_chars: Optional[int] = None,
                 gold_examples: Optional[list[tuple[IngestResult, DeckPlan]]] = None,
                 self_check: bool = True, max_fewshot_chars: int = 8000):
        self.client = client if client is not None else LLMClient()
        self.chunk_chars = chunk_chars or int(os.getenv("PLANNER_CHUNK_CHARS", "12000"))
        self.gold_examples = gold_examples or []
        self.self_check = self_check
        self.max_fewshot_chars = max_fewshot_chars
        self.last_plan: Optional[DeckPlan] = None
        self.notes: list[str] = []          # что поправила санитизация (для отчёта/отладки)
        self.self_repairs = 0

    # ---------- Planner protocol ----------
    def plan(self, src: IngestResult, catalog: TemplateCatalog,
             feedback: Optional[list[Issue]] = None) -> DeckPlan:
        limits = catalog_limits(catalog)
        if feedback and self.last_plan is not None:
            raw = self._repair(src, limits, self.last_plan, issues_to_feedback(feedback, self.last_plan))
        elif self._source_chars(src) > self.chunk_chars and len(src.sections) > 1:
            raw = self._map_reduce(src, catalog, limits)
        else:
            extra = "## Замечания проверки\n" + issues_to_feedback(feedback) if feedback else ""
            raw = self._call(DeckPlan, build_user_message(src, limits, extra=extra), limits)
        plan, notes = sanitize(raw, src, catalog)

        if self.self_check and plan.slides:
            viol = limit_violations(plan, limits)
            if len(viol) > SELF_CHECK_SHARE * len(plan.slides):
                try:
                    fixed, notes2 = sanitize(self._repair(src, limits, plan, violations_to_feedback(viol, plan)),
                                             src, catalog)
                    self.self_repairs += 1
                    if len(limit_violations(fixed, limits)) < len(viol):
                        plan, notes = fixed, notes + notes2
                except LLMError:
                    pass  # остаётся исходный план; Fitter сократит/разобьёт
        self.last_plan, self.notes = plan, notes
        return plan

    # ---------- вызовы ----------
    def _fewshot(self, limits: dict[SlideType, SlideLimits]) -> list[dict]:
        out: list[dict] = []
        for gsrc, gplan in sorted(self.gold_examples, key=lambda e: len(e[0].raw_text))[:2]:
            user = build_user_message(gsrc, limits)
            if len(user) > self.max_fewshot_chars:
                continue
            out += [{"role": "user", "content": user},
                    {"role": "assistant", "content": gplan.model_dump_json(exclude_defaults=True)}]
        if not out:
            p = load_prompts()
            out = [{"role": "user", "content": p["fewshot_user"]},
                   {"role": "assistant", "content": p["fewshot_assistant"]}]
        return out

    def _call(self, schema: Type[T], user: str, limits: Optional[dict] = None, fewshot: bool = True) -> T:
        msgs = [{"role": "system", "content": load_prompts()["system"]}]
        if fewshot:
            msgs += self._fewshot(limits or {})
        msgs.append({"role": "user", "content": user})
        return self.client.generate(msgs, schema)

    def _repair(self, src: IngestResult, limits: dict, prev: DeckPlan, feedback_text: str) -> DeckPlan:
        extra = ("## Предыдущий план\n" + prev.model_dump_json(exclude_defaults=True)
                 + "\n\n## Замечания проверки\n" + feedback_text)
        return self._call(DeckPlan, build_user_message(src, limits, task=load_prompts()["repair"], extra=extra),
                          limits)

    # ---------- map-reduce ----------
    @staticmethod
    def _source_chars(src: IngestResult) -> int:
        return sum(len(s.heading) + len(s.text) for s in src.sections)

    def _normalize_outline(self, outline: Outline, src: IngestResult) -> list[OutlinePart]:
        """Каждая секция ровно в одной части, порядок источника; забытые секции — к части предыдущей секции."""
        order = [s.section_id for s in src.sections]
        pos = {sid: i for i, sid in enumerate(order)}
        seen: set[str] = set()
        parts: list[OutlinePart] = []
        for p in outline.parts:
            ids = [sid for sid in p.section_ids if sid in pos and sid not in seen]
            seen.update(ids)
            if ids:
                parts.append(OutlinePart(title=p.title.strip() or src.sections[pos[ids[0]]].heading, section_ids=ids))
        if not parts:  # модель не дала годной структуры — группируем секции по объёму
            return self._chunk_sections(src)
        owner = {sid: k for k, p in enumerate(parts) for sid in p.section_ids}
        for i, sid in enumerate(order):
            if sid in owner:
                continue
            prev = next((owner[order[j]] for j in range(i - 1, -1, -1) if order[j] in owner), 0)
            parts[prev].section_ids.append(sid)
            owner[sid] = prev
        for p in parts:
            p.section_ids.sort(key=pos.__getitem__)
        parts.sort(key=lambda p: pos[p.section_ids[0]])
        return parts

    def _chunk_sections(self, src: IngestResult) -> list[OutlinePart]:
        parts: list[OutlinePart] = []
        size = 0
        for s in src.sections:
            n = len(s.heading) + len(s.text)
            if not parts or size + n > self.chunk_chars:
                parts.append(OutlinePart(title=s.heading or f"Часть {len(parts) + 1}", section_ids=[]))
                size = 0
            parts[-1].section_ids.append(s.section_id)
            size += n
        return parts

    def _map_reduce(self, src: IngestResult, catalog: TemplateCatalog, limits: dict) -> DeckPlan:
        prompts = load_prompts()
        try:
            outline = self._call(Outline, build_outline_message(src, prompts["outline"]), fewshot=False)
            parts = self._normalize_outline(outline, src)
            deck_title, subtitle = outline.deck_title, outline.subtitle
        except LLMError:
            parts = self._chunk_sections(src)
            deck_title = next((s.heading for s in src.sections if s.heading), "Презентация")
            subtitle = ""
        with_sections = len(parts) >= 3 and bool(catalog.by_type(SlideType.section))
        slides = [SlideSpec(type=SlideType.title, title=deck_title, subtitle=subtitle)]
        for p in parts:
            if with_sections:
                slides.append(SlideSpec(type=SlideType.section, title=p.title))
            batch = self._call(SlideBatch, build_part_message(src, limits, p.title, p.section_ids, prompts["part"]),
                               limits)
            slides += [s for s in batch.slides if s.type in (SlideType.slide, SlideType.table)]
        return DeckPlan(deck_title=deck_title, slides=slides)
