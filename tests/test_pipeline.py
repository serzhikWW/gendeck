"""ML1-3: repair-цикл, деградация, fallback-план без LLM (все модули — фейки по Protocol)."""
from __future__ import annotations
from pathlib import Path

from deckgen.contracts import *
from deckgen.llm.base import LLMUnavailable
from deckgen.pipeline import MAX_REPAIRS, Modules, remap_issues, run
from deckgen.planner.fallback import FallbackPlanner, plan_without_llm
from deckgen.planner.numbers import number_in_source, quote_in_source

from test_planner import CATALOG, RAW, SRC


class Prof:
    def profile(self, template_path): return CATALOG


class Ing:
    def ingest(self, text): return SRC


class Fit:
    """Один FittedSlide на слайд плана; таблицы заполняются из источника."""
    def fit(self, plan, src, catalog):
        out = []
        for i, s in enumerate(plan.slides):
            s = s.model_copy(deep=True)
            if s.table and s.table.source_id:
                t = next(t for t in src.tables if t.table_id == s.table.source_id)
                s.table.columns, s.table.rows = t.columns, t.rows
            out.append(FittedSlide(spec=s, layout_id=s.type.value, origin_slide_index=i))
        return FittedDeck(template_id=catalog.template_id, slides=out)


class Rend:
    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on

    def render(self, deck, template_path, catalog, out_path):
        self.calls += 1
        if self.fail_on and self.fail_on(deck):
            raise RuntimeError("render boom")
        Path(out_path).write_text("\n".join(s.spec.title for s in deck.slides), encoding="utf-8")


class Val:
    """Ошибка NUMBER_MISSING на каждый слайд, где в тексте есть число не из источника."""
    def __init__(self): self.calls = 0

    def validate(self, src, deck, pptx_path, catalog):
        self.calls += 1
        issues = []
        for i, fs in enumerate(deck.slides):
            if "999" in " ".join([fs.spec.title, *fs.spec.bullets]):
                issues.append(Issue(severity="error", code=IssueCode.NUMBER_MISSING, slide_index=i,
                                    message="число 999 не найдено в источнике"))
        return ValidationReport(ok=not issues, issues=issues, metrics={"fidelity_numbers": 0.0 if issues else 1.0})


class ScriptedPlanner:
    def __init__(self, *plans):
        self.plans = list(plans)
        self.feedbacks = []

    def plan(self, src, catalog, feedback=None):
        self.feedbacks.append(feedback)
        p = self.plans.pop(0)
        if isinstance(p, Exception):
            raise p
        return p


def _plan(title):
    return DeckPlan(deck_title="d", slides=[SlideSpec(type=SlideType.title, title="d"),
                                            SlideSpec(type=SlideType.slide, title=title, bullets=["x"])])


def _mods(planner, renderer=None, validator=None):
    return Modules(profiler=Prof(), ingestor=Ing(), planner=planner, fitter=Fit(),
                   renderer=renderer or Rend(), validator=validator or Val())


def test_ok_on_first_attempt(tmp_path):
    out = tmp_path / "o.pptx"
    rep, plan = run("txt", "t.pptx", str(out), _mods(ScriptedPlanner(_plan("ok"))))
    assert rep.ok and out.exists() and rep.metrics["repair_iterations"] == 0 and rep.metrics["fallback_used"] == 0


def test_repair_feedback_remapped_to_plan_index(tmp_path):
    p = ScriptedPlanner(_plan("bad 999"), _plan("fixed"))
    events = []
    rep, plan = run("txt", "t.pptx", str(tmp_path / "o.pptx"), _mods(p), lambda s, d: events.append(s))
    assert rep.ok and plan.slides[1].title == "fixed"
    fb = p.feedbacks[1]
    assert fb[0].slide_index == 1 and fb[0].code == IssueCode.NUMBER_MISSING
    assert rep.metrics["repair_iterations"] == 1
    assert events.count("plan") == 2


def test_unresolved_after_max_repairs_keeps_best_and_warns(tmp_path):
    worse = DeckPlan(deck_title="d", slides=[SlideSpec(type=SlideType.slide, title="999", bullets=["999"]),
                                             SlideSpec(type=SlideType.slide, title="a 999")])
    p = ScriptedPlanner(_plan("bad 999"), worse, worse)
    r = Rend()
    out = tmp_path / "o.pptx"
    rep, plan = run("txt", "t.pptx", str(out), _mods(p, r))
    assert len(p.feedbacks) == MAX_REPAIRS + 1
    assert plan.slides[1].title == "bad 999"                 # лучший (с меньшим числом ошибок) план
    assert out.read_text(encoding="utf-8").splitlines()[1] == "bad 999"  # и файл перерендерен по нему
    assert any(i.severity == "warning" and "repair" in i.message for i in rep.issues)
    assert out.exists() and not rep.ok


def test_llm_unavailable_falls_back(tmp_path):
    events = []
    rep, plan = run(RAW, "t.pptx", str(tmp_path / "o.pptx"),
                    _mods(ScriptedPlanner(LLMUnavailable("no net"))), lambda s, d: events.append(s))
    assert "fallback" in events and rep.metrics["fallback_used"] == 1
    assert (tmp_path / "o.pptx").exists()
    assert any(s.table and s.table.source_id == "t1" for s in plan.slides)


def test_planner_crash_in_repair_keeps_previous(tmp_path):
    p = ScriptedPlanner(_plan("bad 999"), RuntimeError("boom"))
    rep, plan = run("txt", "t.pptx", str(tmp_path / "o.pptx"), _mods(p))
    assert plan.slides[1].title == "bad 999" and (tmp_path / "o.pptx").exists()


def test_render_failure_with_llm_plan_uses_fallback(tmp_path):
    r = Rend(fail_on=lambda deck: any(s.spec.title == "boom" for s in deck.slides))
    rep, plan = run(RAW, "t.pptx", str(tmp_path / "o.pptx"), _mods(ScriptedPlanner(_plan("boom")), r))
    assert rep.metrics["fallback_used"] == 1 and (tmp_path / "o.pptx").exists()


def test_validator_crash_still_returns_report(tmp_path):
    class BadVal:
        def validate(self, *a): raise ValueError("oops")
    rep, _ = run("txt", "t.pptx", str(tmp_path / "o.pptx"), _mods(ScriptedPlanner(_plan("ok")), validator=BadVal()))
    assert (tmp_path / "o.pptx").exists() and any("oops" in i.message for i in rep.issues)


def test_remap_issues():
    deck = FittedDeck(template_id="t", slides=[
        FittedSlide(spec=SlideSpec(type=SlideType.slide, title="a"), layout_id="l", origin_slide_index=0),
        FittedSlide(spec=SlideSpec(type=SlideType.slide, title="b (2/2)"), layout_id="l", origin_slide_index=0),
        FittedSlide(spec=SlideSpec(type=SlideType.slide, title="c"), layout_id="l", origin_slide_index=1)])
    issues = [Issue(severity="error", code=IssueCode.TEXT_OVERFLOW, slide_index=1, message="m"),
              Issue(severity="error", code=IssueCode.NUMBER_MISSING, slide_index=None, message="n")]
    out = remap_issues(issues, deck)
    assert out[0].slide_index == 0 and out[1].slide_index is None


# ---------- fallback без LLM ----------
def test_plan_without_llm_structure_and_fidelity():
    plan = plan_without_llm(SRC, CATALOG)
    types = [s.type for s in plan.slides]
    assert types[0] == SlideType.title and types[-1] == SlideType.last
    assert plan.slides[0].title == "Итоги 2024 года"
    assert plan.slides[0].subtitle == "Докладчик: директор по производству, март 2025 г."
    tables = [s for s in plan.slides if s.table]
    assert len(tables) == 1 and tables[0].table.source_id == "t1" and tables[0].table.rows == []
    prod = next(s for s in plan.slides if s.title == "Производство")
    assert prod.bullets == ["Выработка электроэнергии составила 19,1 млрд кВт·ч.",
                            "Установленная мощность — 1 234,5 МВт.", "КИУМ вырос до 52,3 %."]
    for s in plan.slides:  # все числа из источника, на каждое — дословная цитата
        for b in s.bullets:
            assert quote_in_source(b, RAW)
        for e in s.evidence:
            assert quote_in_source(e.quote, RAW)
    assert all(any(number_in_source("19,1", e.quote) for e in prod.evidence) for _ in [0])


def test_plan_without_llm_respects_bullet_limit():
    text = " ".join(f"Пункт номер {i} выполнен." for i in range(1, 15))
    src = IngestResult(raw_text=text, source_hash="h", sections=[SourceSection(section_id="s1", heading="Поручения", text=text)],
                       tables=[])
    plan = plan_without_llm(src, CATALOG)
    content = [s for s in plan.slides if s.type == SlideType.slide]
    assert len(content) == 3 and all(len(s.bullets) <= 6 for s in content)
    assert content[1].title == "Поручения (продолжение)"


def test_fallback_planner_protocol():
    assert FallbackPlanner().plan(SRC, CATALOG).slides


def test_sentences_do_not_split_on_abbreviations():
    from deckgen.planner.numbers import sentences
    assert sentences("Затраты 1 234,5 млн руб. при плане 1 300,0 млн руб. Аварийность снизилась.") == [
        "Затраты 1 234,5 млн руб. при плане 1 300,0 млн руб.", "Аварийность снизилась."]


def test_find_quote_prefers_context_overlap():
    from deckgen.planner.numbers import find_quote
    raw = "Задачи на 2025 год\nДокладчик: заместитель директора, февраль 2025 г."
    assert find_quote(["2025"], raw, "Заместитель директора, февраль 2025 г.") == [
        "Докладчик: заместитель директора, февраль 2025 г."]


def test_fallback_table_caption_not_a_bullet():
    from deckgen.cli import DEMO_TEXT
    from deckgen.stubs import StubIngestor
    src = StubIngestor().ingest(DEMO_TEXT)
    plan = plan_without_llm(src, CATALOG)
    assert not any("Таблица 1." in b for s in plan.slides for b in s.bullets)
    t = next(s for s in plan.slides if s.table)
    assert t.title == "Основные показатели, план-факт" and not t.evidence
