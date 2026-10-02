"""Оркестратор. Владелец: ML1. Логика: profile -> ingest -> plan -> fit -> render -> validate -> (repair x2).

Гарантия: файл создаётся всегда.
- Planner упал на первой попытке (LLM недоступна, невалидный JSON после repair) -> fallback-план без LLM.
- Planner упал на repair-попытке -> остаёмся на лучшем уже полученном плане.
- Fit/render упали на плане LLM -> fallback-план.
- Ошибки валидатора, которые может исправить планировщик, уходят ему как feedback (индексы слайдов PPTX
  переводятся в индексы плана через FittedSlide.origin_slide_index). Если после MAX_REPAIRS ошибки остались —
  рендерится лучший план (с наименьшим числом ошибок), в отчёт добавляется warning.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from .contracts import *
from .planner.fallback import FallbackPlanner

MAX_REPAIRS = 2
Progress = Callable[[str, dict], None]   # stage name, payload  (UI показывает это в SSE)

# Ошибки, которые имеет смысл чинить перепланированием (остальные — забота Fitter/Renderer)
REPAIRABLE = {IssueCode.NUMBER_MISSING, IssueCode.EVIDENCE_NOT_FOUND, IssueCode.TEXT_OVERFLOW,
              IssueCode.TOO_MANY_BULLETS, IssueCode.EMPTY_PLACEHOLDER}


@dataclass
class Modules:
    profiler: TemplateProfiler
    ingestor: Ingestor
    planner: Planner
    fitter: Fitter
    renderer: Renderer
    validator: Validator


def remap_issues(issues: list[Issue], deck: FittedDeck) -> list[Issue]:
    """slide_index PPTX -> индекс слайда плана; в сообщение добавляется заголовок слайда PPTX."""
    out = []
    for it in issues:
        idx = it.slide_index
        if idx is not None and 0 <= idx < len(deck.slides):
            fs = deck.slides[idx]
            it = it.model_copy(update={"slide_index": fs.origin_slide_index,
                                       "message": f"{it.message} (слайд PPTX {idx + 1} «{fs.spec.title[:60]}»)"})
        out.append(it)
    return out


def _errors(report: ValidationReport) -> list[Issue]:
    return [i for i in report.issues if i.severity == "error"]


def run(text: str, template_path: str, out_path: str, m: Modules,
        progress: Optional[Progress] = None, fallback: Optional[Planner] = None) -> tuple[ValidationReport, DeckPlan]:
    emit = progress or (lambda s, p: None)
    fallback = fallback or FallbackPlanner()
    emit("profile", {}); catalog = m.profiler.profile(template_path)
    emit("ingest", {"layouts": len(catalog.layouts)}); src = m.ingestor.ingest(text)
    emit("ingest_done", {"sections": len(src.sections), "tables": len(src.tables)})

    def build(plan: DeckPlan) -> FittedDeck:
        emit("fit", {"slides": len(plan.slides)}); deck = m.fitter.fit(plan, src, catalog)
        emit("render", {"slides": len(deck.slides)}); m.renderer.render(deck, template_path, catalog, out_path)
        return deck

    def validate(deck: FittedDeck) -> ValidationReport:
        emit("validate", {})
        try:
            return m.validator.validate(src, deck, out_path, catalog)
        except Exception as e:  # валидатор не должен ронять выдачу файла
            return ValidationReport(ok=False, issues=[Issue(severity="warning", code=IssueCode.PPTX_INVALID,
                                                            message=f"Валидатор упал: {type(e).__name__}: {e}")])

    feedback: Optional[list[Issue]] = None
    used_fallback = False
    best: Optional[tuple[int, DeckPlan, FittedDeck, ValidationReport]] = None
    last_plan: Optional[DeckPlan] = None
    attempts = 0
    for attempt in range(MAX_REPAIRS + 1):
        emit("plan", {"attempt": attempt})
        try:
            plan = (fallback if used_fallback else m.planner).plan(src, catalog, feedback)
        except Exception as e:
            if best is not None:   # repair не удался — остаёмся на лучшем
                emit("repair_failed", {"error": f"{type(e).__name__}: {e}"})
                break
            emit("fallback", {"reason": f"{type(e).__name__}: {e}"})
            used_fallback = True
            plan = fallback.plan(src, catalog, None)
        attempts = attempt + 1
        try:
            deck = build(plan)
        except Exception as e:
            if used_fallback:
                raise
            emit("fallback", {"reason": f"fit/render: {type(e).__name__}: {e}"})
            used_fallback = True
            plan = fallback.plan(src, catalog, None)
            deck = build(plan)
        last_plan = plan
        report = validate(deck)
        emit("report", {"ok": report.ok, "issues": len(report.issues), "metrics": report.metrics})
        errors = _errors(report)
        if best is None or len(errors) < best[0]:
            best = (len(errors), plan, deck, report)
        fixable = [i for i in errors if i.code in REPAIRABLE]
        if not errors or not fixable or used_fallback:
            break
        feedback = remap_issues(fixable, deck)

    assert best is not None
    n_err, plan, deck, report = best
    if plan is not last_plan:   # последняя попытка хуже лучшей — перерендерить лучшую
        emit("render", {"slides": len(deck.slides), "best_attempt": True})
        m.renderer.render(deck, template_path, catalog, out_path)
    report = report.model_copy(deep=True)
    if n_err and attempts > 1:
        report.issues.append(Issue(severity="warning", code=_errors(report)[0].code,
                                   message=f"После {attempts - 1} repair-итераций осталось ошибок: {n_err}; "
                                           f"файл создан по лучшему плану, нужна ручная проверка"))
    report.metrics.update({"repair_iterations": float(attempts - 1), "fallback_used": float(used_fallback),
                           "slides": float(len(deck.slides))})
    emit("done", {"ok": report.ok, "out": out_path})
    return report, plan
