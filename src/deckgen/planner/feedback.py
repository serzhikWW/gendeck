"""Перевод замечаний валидатора/самопроверки в текст для repair-промпта."""
from __future__ import annotations
from typing import Optional

from ..contracts import DeckPlan, Issue, IssueCode

HINTS: dict[IssueCode, str] = {
    IssueCode.NUMBER_MISSING: "число не найдено в источнике — удали его или перепиши дословно из источника",
    IssueCode.TABLE_MISMATCH: "таблица расходится с источником — оставь только table.source_id, без своих ячеек",
    IssueCode.EVIDENCE_NOT_FOUND: "цитата evidence не дословная — скопируй точное предложение из источника",
    IssueCode.TEXT_OVERFLOW: "текст не помещается — сократи формулировки или раздели слайд на два",
    IssueCode.TOO_MANY_BULLETS: "слишком много буллетов — оставь главное или раздели слайд",
    IssueCode.EMPTY_PLACEHOLDER: "пустое поле макета — заполни его или выбери другой тип слайда",
}


def _slide_label(idx: Optional[int], plan: Optional[DeckPlan]) -> str:
    if idx is None:
        return "Весь план"
    label = f"Слайд {idx + 1}"
    if plan is not None and 0 <= idx < len(plan.slides):
        label += f" «{plan.slides[idx].title[:70]}»"
    return label


def issues_to_feedback(issues: list[Issue], plan: Optional[DeckPlan] = None) -> str:
    """slide_index в issues — индекс слайда ПЛАНА (pipeline переводит индексы PPTX через origin_slide_index)."""
    lines = []
    for it in issues:
        hint = HINTS.get(it.code, "")
        msg = it.message.strip()
        lines.append(f"- {_slide_label(it.slide_index, plan)}: {msg}" + (f" → {hint}" if hint else ""))
    return "\n".join(lines)


def violations_to_feedback(violations: list[tuple[int, str]], plan: DeckPlan) -> str:
    return "\n".join(f"- {_slide_label(i, plan)}: {msg} → сократи или раздели слайд" for i, msg in violations)
