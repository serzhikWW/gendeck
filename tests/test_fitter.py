from pathlib import Path

import pytest

from deckgen.contracts import DeckPlan, SlideSpec, SlideType, SourceTable, TableSpec, IngestResult
from deckgen.fitter import DeterministicFitter
from deckgen.fitter.measure import is_numeric, layout_table, table_grid
from deckgen.template import profile

import ml2_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = profile(str(ROOT / "templates" / "example_fields.pptx"))
INTERRAO = profile(str(ROOT / "templates" / "interrao_corporate.pptx"))


def _fit(plan, cat=EXAMPLE, src=None):
    return DeterministicFitter().fit(plan, src or fx.ingest(), cat)


@pytest.mark.parametrize("cat", [EXAMPLE, INTERRAO], ids=["example", "interrao"])
def test_fixture_deck_layouts(cat):
    deck = _fit(fx.plan(), cat)
    lids = {l.layout_id: l for l in cat.layouts}
    assert deck.template_id == cat.template_id
    assert all(s.layout_id in lids for s in deck.slides)
    assert deck.slides[0].spec.type == SlideType.title
    assert deck.slides[-1].spec.type == SlideType.last
    # детерминизм
    assert _fit(fx.plan(), cat) == deck


def test_table_cells_verbatim_from_source():
    deck = _fit(fx.plan())
    t_slides = [s for s in deck.slides if s.spec.table and s.spec.table.source_id == "t1"]
    assert t_slides
    for s in t_slides:
        assert s.spec.table.columns == fx.T1.columns
        assert s.spec.table.rows == fx.T1.rows  # 11 строк влезают целиком
    # LLM-ячейки по source_id игнорируются: берём источник
    p = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.table, title="T",
                 table=TableSpec(source_id="t1", columns=["выдумка"], rows=[["1"]]))])
    s = _fit(p).slides[0]
    assert s.spec.table.rows == fx.T1.rows and s.spec.table.columns == fx.T1.columns


def test_layout_choice_by_content():
    deck = _fit(fx.plan())
    by_title = {s.spec.title: s for s in deck.slides}
    assert by_title["Главное за год"].layout_id == "slide1"
    assert by_title["Ключевые показатели 2022–2024"].layout_id == "table_text1"
    assert by_title["Ключевые показатели (таблица целиком)"].layout_id == "table_only1"


def test_big_table_split_with_header_repeat():
    deck = _fit(fx.plan())
    parts = [s for s in deck.slides if s.spec.table and s.spec.table.source_id == "t2"]
    assert len(parts) >= 2
    assert all(p.spec.title.startswith("Ремонтная программа 2025 (") for p in parts)
    assert all(p.spec.table.columns == fx.T2.columns for p in parts)
    assert [r for p in parts for r in p.spec.table.rows] == fx.T2.rows  # ничего не потеряно, порядок тот же
    sizes = [len(p.spec.table.rows) for p in parts]
    assert max(sizes) - min(sizes) <= 1  # нарезка сбалансирована


def test_table_12x6_fits_one_slide():
    rows = [[f"Параметр {i}", "млрд руб.", "19,1", "≥ 10", "—", "3,2%"] for i in range(11)]
    src = IngestResult(raw_text="", source_hash="h", sections=[], tables=[
        SourceTable(table_id="t9", columns=["Показатель", "Ед.", "2022", "2023", "2024", "Изм."], rows=rows)])
    p = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.table, title="T", table=TableSpec(source_id="t9"))])
    deck = _fit(p, src=src)
    assert len(deck.slides) == 1 and deck.slides[0].spec.table.rows == rows


def test_long_bullets_split_never_truncate():
    deck = _fit(fx.plan())
    parts = [s for s in deck.slides if s.spec.title.startswith("Программа повышения эффективности")]
    assert len(parts) == 2
    joined = [b for p in parts for b in p.spec.bullets]
    assert joined == next(s for s in fx.plan().slides if s.title == "Программа повышения эффективности").bullets


def test_font_reduced_within_limits():
    bullets = ["Длинный пункт про модернизацию энергоблоков и снижение потерь в сетях " * 2] * 7
    p = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.slide, title="T", bullets=bullets)])
    deck = _fit(p)
    st = EXAMPLE.style
    for s in deck.slides:
        assert st.body_pt_min <= s.font_pt["TEXT_FIELD"] <= st.body_pt_max


def test_empty_slide_becomes_section_and_dups_removed():
    s = SlideSpec(type=SlideType.slide, title="Итоги")
    p = DeckPlan(deck_title="x", slides=[s, s, SlideSpec(type=SlideType.slide, title="")])
    deck = _fit(p)
    assert [x.spec.type for x in deck.slides] == [SlideType.section]


def test_missing_source_table_degrades_gracefully():
    p = DeckPlan(deck_title="x", slides=[SlideSpec(type=SlideType.table, title="T", bullets=["a"],
                                                   table=TableSpec(source_id="nope"))])
    s = _fit(p).slides[0]
    assert s.spec.table is None and s.spec.type == SlideType.slide


def test_measure_helpers():
    assert is_numeric("19,1") and is_numeric("≥ 10") and is_numeric("—") and is_numeric("−0,8")
    assert is_numeric("~23*") and is_numeric("5 120") and not is_numeric("млрд руб.")
    grid, hn = table_grid(TableSpec(columns=["a", "b"], rows=[["1"], ["2", "3"]]))
    assert grid == [["a", "b"], ["1", ""], ["2", "3"]] and hn == 1
    tl = layout_table(grid, hn, 914400 * 4, 12)
    assert 914400 * 2 <= sum(tl.col_w) <= 914400 * 4 and len(tl.row_h) == 3
    wide = layout_table([["Очень длинный текст ячейки " * 6] * 3], 0, 914400 * 4, 12)
    assert sum(wide.col_w) == 914400 * 4  # большой таблице — вся зона
