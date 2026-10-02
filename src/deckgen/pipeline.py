"""Оркестратор. Владелец: ML1. Логика фиксирована: profile -> ingest -> plan -> fit -> render -> validate -> (repair x2)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional
from .contracts import *

MAX_REPAIRS = 2
Progress = Callable[[str, dict], None]   # stage name, payload  (UI показывает это в SSE)

@dataclass
class Modules:
    profiler: TemplateProfiler
    ingestor: Ingestor
    planner: Planner
    fitter: Fitter
    renderer: Renderer
    validator: Validator

def run(text: str, template_path: str, out_path: str, m: Modules,
        progress: Optional[Progress] = None) -> tuple[ValidationReport, DeckPlan]:
    emit = progress or (lambda s, p: None)
    emit("profile", {}); catalog = m.profiler.profile(template_path)
    emit("ingest", {"layouts": len(catalog.layouts)}); src = m.ingestor.ingest(text)
    feedback: Optional[list[Issue]] = None
    report: ValidationReport; plan: DeckPlan
    for attempt in range(MAX_REPAIRS + 1):
        emit("plan", {"attempt": attempt}); plan = m.planner.plan(src, catalog, feedback)
        emit("fit", {"slides": len(plan.slides)}); deck = m.fitter.fit(plan, src, catalog)
        emit("render", {"slides": len(deck.slides)}); m.renderer.render(deck, template_path, catalog, out_path)
        emit("validate", {}); report = m.validator.validate(src, deck, out_path, catalog)
        emit("report", {"ok": report.ok, "issues": len(report.issues), "metrics": report.metrics})
        errors = [i for i in report.issues if i.severity == "error"]
        if not errors: break
        feedback = errors
    return report, plan
