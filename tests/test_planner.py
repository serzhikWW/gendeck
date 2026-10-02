"""ML1-2: планировщик на фейковом LLM (без сети)."""
from __future__ import annotations
from deckgen.contracts import *
from deckgen.planner import LLMPlanner
from deckgen.planner.limits import catalog_limits, limit_violations
from deckgen.planner.numbers import numbers_in, number_in_source
from deckgen.planner.prompt_builder import build_user_message
from deckgen.planner.prompts import load_prompts
from deckgen.planner.sanitize import sanitize


RAW = """# Итоги 2024 года
Докладчик: директор по производству, март 2025 г.

## Производство
Выработка электроэнергии составила 19,1 млрд кВт·ч. Установленная мощность — 1 234,5 МВт.
КИУМ вырос до 52,3 %.

## Финансы
| Показатель | 2023 | 2024 |
|---|---|---|
| Выручка | 100,2 | 112,7 |
| EBITDA | 20,1 | ≥ 25 |
"""

SRC = IngestResult(
    raw_text=RAW, source_hash="h",
    sections=[SourceSection(section_id="s1", heading="Итоги 2024 года", text="Докладчик: директор по производству, март 2025 г."),
              SourceSection(section_id="s2", heading="Производство",
                            text="Выработка электроэнергии составила 19,1 млрд кВт·ч. Установленная мощность — 1 234,5 МВт.\nКИУМ вырос до 52,3 %."),
              SourceSection(section_id="s3", heading="Финансы", text="", table_ids=["t1"])],
    tables=[SourceTable(table_id="t1", columns=["Показатель", "2023", "2024"],
                        rows=[["Выручка", "100,2", "112,7"], ["EBITDA", "20,1", "≥ 25"]], caption="Финансы")])


def _f(name, kind, max_chars=0, max_lines=0):
    return FieldSpec(name=name, kind=kind, bbox=BBox(x=0, y=0, w=100, h=100), max_chars=max_chars, max_lines=max_lines)


CATALOG = TemplateCatalog(template_id="ex", adapter="convention", slide_w=12192000, slide_h=6858000, layouts=[
    LayoutSpec(layout_id="title1", slide_type=SlideType.title,
               fields=[_f("TITLE_FIELD", FieldKind.title, 80), _f("SUBTITLE_FIELD", FieldKind.subtitle, 100)]),
    LayoutSpec(layout_id="section1", slide_type=SlideType.section, fields=[_f("TITLE_FIELD", FieldKind.title, 60)]),
    LayoutSpec(layout_id="slide1", slide_type=SlideType.slide,
               fields=[_f("TITLE_FIELD", FieldKind.title, 60), _f("TEXT_FIELD", FieldKind.text, 600, 12)]),
    LayoutSpec(layout_id="table_text1", slide_type=SlideType.table,
               fields=[_f("TITLE_FIELD", FieldKind.title, 60), _f("TEXT_FIELD", FieldKind.text, 200, 6),
                       _f("TABLE_FIELD", FieldKind.table)]),
    LayoutSpec(layout_id="last1", slide_type=SlideType.last, fields=[_f("TITLE_FIELD", FieldKind.title, 60)]),
])


class FakeLLM:
    """Отдаёт заготовленные ответы по очереди; запоминает (messages, schema)."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[list[dict], type]] = []

    def generate(self, messages, schema):
        self.calls.append((messages, schema))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return schema.model_validate(a)


GOOD_PLAN = {"deck_title": "Итоги 2024 года", "slides": [
    {"type": "title", "title": "Итоги 2024 года", "subtitle": "Директор по производству, март 2025 г."},
    {"type": "slide", "title": "Производство", "bullets": ["Выработка — 19,1 млрд кВт·ч", "КИУМ вырос до 52,3 %"],
     "evidence": [{"quote": "Выработка электроэнергии составила 19,1 млрд кВт·ч."}]},
    {"type": "table", "title": "Финансы", "table": {"source_id": "t1", "columns": ["X"], "rows": [["выдумано", "1"]]}},
    {"type": "last", "title": "Спасибо за внимание"}]}


# ---------- числа ----------
def test_numbers_tokenizer_and_lookup():
    assert numbers_in("мощность 1 234,5 МВт и 19,1 и 2024 год") == ["1 234,5", "19,1", "2024"]
    assert number_in_source("19,1", RAW)
    assert not number_in_source("19", RAW)          # часть "19,1" — не считается
    assert not number_in_source("19.1", RAW)        # другой разделитель = другое число
    assert number_in_source("1 234,5", RAW.replace("1 234,5", "1 234,5"))


# ---------- лимиты ----------
def test_catalog_limits():
    lim = catalog_limits(CATALOG)
    assert lim[SlideType.slide].title_chars == 60
    assert lim[SlideType.slide].max_bullets == 6 and lim[SlideType.slide].bullet_chars == 100
    assert lim[SlideType.table].has_table and lim[SlideType.table].max_bullets == 3
    assert lim[SlideType.title].has_subtitle
    assert SlideType.section in lim and not lim[SlideType.section].has_text


def test_limit_violations():
    plan = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.slide, title="a" * 61, bullets=["b"] * 7)])
    v = limit_violations(plan, catalog_limits(CATALOG))
    assert len(v) == 2 and all(i == 0 for i, _ in v)


# ---------- промпт ----------
def test_prompts_parse():
    p = load_prompts()
    assert p["system"].startswith("planner-v")
    assert "source_id" in p["system"] and "evidence" in p["system"]
    assert {"fewshot_user", "fewshot_assistant", "outline", "part", "repair"} <= set(p)
    DeckPlan.model_validate_json(p["fewshot_assistant"])  # встроенный пример валиден по контракту


def test_user_message_has_limits_and_no_cells():
    msg = build_user_message(SRC, catalog_limits(CATALOG))
    assert "t1" in msg and "Показатель | 2023 | 2024" in msg and "строк: 2" in msg
    assert "112,7" not in msg.split("## Источник")[0]  # ячейки таблиц не передаются в список таблиц
    assert "slide: заголовок ≤ 60 симв.; до 6 буллетов по ≤ 100 симв." in msg
    assert "[s2] Производство" in msg and "19,1 млрд" in msg


# ---------- санитизация ----------
def test_sanitize_tables_by_source_id_only():
    plan = DeckPlan.model_validate(GOOD_PLAN)
    out, notes = sanitize(plan, SRC, CATALOG)
    t = [s for s in out.slides if s.type == SlideType.table][0]
    assert t.table.source_id == "t1" and t.table.columns == [] and t.table.rows == []


def test_sanitize_drops_llm_table_and_adds_missing_source_table():
    plan = DeckPlan(deck_title="x", slides=[
        SlideSpec(type=SlideType.title, title="x"),
        SlideSpec(type=SlideType.table, title="Своя таблица", table=TableSpec(columns=["a"], rows=[["42"]])),
        SlideSpec(type=SlideType.last, title="Спасибо")])
    out, notes = sanitize(plan, SRC, CATALOG)
    assert all(s.table is None or s.table.source_id for s in out.slides)
    assert [s.table.source_id for s in out.slides if s.table] == ["t1"]
    assert out.slides[-1].type == SlideType.last  # недостающая таблица вставлена перед финальным слайдом
    assert any("t1" in n for n in notes)


def test_sanitize_matches_copied_table_to_source():
    plan = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.table, title="Ф",
                    table=TableSpec(columns=["Показатель", "2023", "2024"], rows=[["Выручка", "100", "113"]]))])
    out, _ = sanitize(plan, SRC, CATALOG)
    assert out.slides[1].table.source_id == "t1" and out.slides[1].table.rows == []


def test_sanitize_removes_hallucinated_numbers_and_adds_evidence():
    plan = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.slide, title="Производство", bullets=[
        "• Выработка — 19,1 млрд кВт·ч", "Рост на 7,7 %", "1) Мощность 1 234,5 МВт", "Без чисел"],
        evidence=[Evidence(quote="придуманная цитата 5")])])
    out, notes = sanitize(plan, SRC, CATALOG)
    s = [x for x in out.slides if x.title == "Производство"][0]
    assert s.bullets == ["Выработка — 19,1 млрд кВт·ч", "Мощность 1 234,5 МВт", "Без чисел"]
    quotes = [e.quote for e in s.evidence]
    assert "придуманная цитата 5" not in quotes
    assert any("19,1" in q for q in quotes) and any("1 234,5" in q for q in quotes)
    assert all(q in RAW for q in quotes)  # цитаты дословные
    assert any("7,7" in n for n in notes)


def test_sanitize_adds_title_and_last():
    plan = DeckPlan(deck_title="Итоги", slides=[SlideSpec(type=SlideType.slide, title="a", bullets=["b"])])
    out, _ = sanitize(plan, SRC, CATALOG)
    assert out.slides[0].type == SlideType.title and out.slides[0].title == "Итоги"
    assert out.slides[-1].type == SlideType.last


def test_sanitize_bad_quote_with_whitespace_variation_is_kept():
    plan = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.slide, title="a", bullets=["КИУМ 52,3 %"],
                    evidence=[Evidence(quote="Установленная   мощность — 1 234,5 МВт.\nКИУМ вырос до 52,3 %.")])])
    out, _ = sanitize(plan, SRC, CATALOG)
    s = [x for x in out.slides if x.title == "a"][0]
    assert len(s.evidence) == 1


# ---------- LLMPlanner ----------
def test_planner_single_pass():
    llm = FakeLLM(GOOD_PLAN)
    plan = LLMPlanner(llm).plan(SRC, CATALOG)
    assert len(llm.calls) == 1 and llm.calls[0][1] is DeckPlan
    msgs = llm.calls[0][0]
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user" and msgs[2]["role"] == "assistant"
    assert [s.type for s in plan.slides] == [SlideType.title, SlideType.slide, SlideType.table, SlideType.last]
    # у буллета "КИУМ вырос до 52,3 %" не было цитаты — санитайзер добавил дословную
    assert any("52,3" in e.quote for e in plan.slides[1].evidence)


def test_planner_repair_uses_previous_plan_and_feedback():
    llm = FakeLLM(GOOD_PLAN, GOOD_PLAN)
    p = LLMPlanner(llm)
    p.plan(SRC, CATALOG)
    fb = [Issue(severity="error", code=IssueCode.NUMBER_MISSING, slide_index=1, message="число 12,4 не найдено в источнике")]
    p.plan(SRC, CATALOG, fb)
    last_user = llm.calls[1][0][-1]["content"]
    assert "12,4" in last_user and "Слайд 2" in last_user and "Предыдущий план" in last_user


def test_planner_self_check_limits_triggers_one_repair():
    long = dict(GOOD_PLAN)
    long["slides"] = GOOD_PLAN["slides"][:1] + [
        {"type": "slide", "title": "Очень длинный заголовок " * 5, "bullets": ["Без чисел"]}] + GOOD_PLAN["slides"][1:]
    llm = FakeLLM(long, GOOD_PLAN)
    plan = LLMPlanner(llm).plan(SRC, CATALOG)
    assert len(llm.calls) == 2
    assert "заголовок" in llm.calls[1][0][-1]["content"]
    assert all(len(s.title) <= 60 for s in plan.slides)


def test_planner_map_reduce_for_long_input():
    big_sections = [SourceSection(section_id=f"s{i}", heading=f"Раздел {i}", text=("Текст раздела без чисел. " * 40))
                    for i in range(1, 7)]
    big_sections[1].table_ids = ["t1"]
    src = IngestResult(raw_text=RAW + "\n".join(s.text for s in big_sections), source_hash="h",
                       sections=big_sections, tables=SRC.tables)
    outline = {"deck_title": "Большой доклад", "subtitle": "", "parts": [
        {"title": "Часть A", "section_ids": ["s1", "s2", "s3"]},
        {"title": "Часть B", "section_ids": ["s4", "s5"]}]}  # s6 забыта моделью — должна попасть в последнюю часть
    part_a = {"slides": [{"type": "title", "title": "лишний титул"},
                         {"type": "slide", "title": "A1", "bullets": ["тезис"]},
                         {"type": "table", "title": "Финансы", "table": {"source_id": "t1"}}]}
    part_b = {"slides": [{"type": "slide", "title": "B1", "bullets": ["тезис"]}]}
    llm = FakeLLM(outline, part_a, part_b)
    plan = LLMPlanner(llm, chunk_chars=1000).plan(src, CATALOG)
    assert len(llm.calls) == 3
    assert "[s6]" in llm.calls[2][0][-1]["content"]
    assert [(s.type.value, s.title) for s in plan.slides] == [
        ("title", "Большой доклад"), ("slide", "A1"), ("table", "Финансы"), ("slide", "B1"),
        ("last", "Спасибо за внимание")]


def test_planner_map_reduce_with_sections_when_many_parts():
    secs = [SourceSection(section_id=f"s{i}", heading=f"Р{i}", text="Текст. " * 100) for i in range(1, 4)]
    src = IngestResult(raw_text="x", source_hash="h", sections=secs, tables=[])
    outline = {"deck_title": "Д", "parts": [{"title": f"Часть {i}", "section_ids": [f"s{i}"]} for i in range(1, 4)]}
    llm = FakeLLM(outline, *[{"slides": [{"type": "slide", "title": f"c{i}", "bullets": ["т"]}]} for i in range(3)])
    plan = LLMPlanner(llm, chunk_chars=500).plan(src, CATALOG)
    assert [s.type.value for s in plan.slides].count("section") == 3


def test_gold_few_shot_used():
    gold_plan = DeckPlan.model_validate(GOOD_PLAN)
    llm = FakeLLM(GOOD_PLAN)
    LLMPlanner(llm, gold_examples=[(SRC, gold_plan)]).plan(SRC, CATALOG)
    msgs = llm.calls[0][0]
    assert '"source_id":"t1"' in msgs[2]["content"].replace(" ", "")
    assert "Итоги ремонтной кампании" not in "".join(m["content"] for m in msgs)  # встроенный пример заменён
