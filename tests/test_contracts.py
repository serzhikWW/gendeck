from deckgen.contracts import *

def test_deckplan_roundtrip():
    p = DeckPlan(deck_title="t", slides=[SlideSpec(type=SlideType.title, title="a"),
        SlideSpec(type=SlideType.table, title="b", table=TableSpec(source_id="t1"))])
    assert DeckPlan.model_validate_json(p.model_dump_json()) == p
