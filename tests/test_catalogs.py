import shutil
from pathlib import Path

import pytest

from deckgen.contracts import FieldKind, SlideType, TemplateCatalog
from deckgen.template import ConventionAdapter, TemplateError, TemplateProfilerImpl
from deckgen.template import metrics

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = str(ROOT / "templates" / "example_fields.pptx")
INTERRAO = str(ROOT / "templates" / "interrao_corporate.pptx")


def test_example_convention_catalog():
    c = TemplateProfilerImpl().profile(EXAMPLE)
    assert c.adapter == "convention"
    assert (c.slide_w, c.slide_h) == (12192000, 6858000)
    ids = [l.layout_id for l in c.layouts]
    assert ids == ["title1", "section1", "slide1", "slide2", "table_text1", "table_only1", "last1"]
    types = {l.layout_id: l.slide_type for l in c.layouts}
    assert types["table_text1"] == SlideType.table and types["last1"] == SlideType.last
    tt = next(l for l in c.layouts if l.layout_id == "table_text1")
    by = {f.name: f for f in tt.fields}
    assert by["TITLE_FIELD"].placeholder_idx == 0
    assert by["TEXT_FIELD"].placeholder_idx == 1 and by["TEXT_FIELD"].kind == FieldKind.text
    assert by["TABLE_FIELD"].placeholder_idx == 2 and by["TABLE_FIELD"].kind == FieldKind.table
    assert by["TEXT_FIELD"].font_pt == pytest.approx(13.23)
    assert by["TEXT_FIELD"].max_lines > 10 and by["TEXT_FIELD"].max_chars > 500
    assert by["TITLE_FIELD"].bbox.w == 10515600
    s2 = next(l for l in c.layouts if l.layout_id == "slide2")
    assert {f.name: f.placeholder_idx for f in s2.fields}["IMAGE_FIELD"] == 13
    assert c.style.font_family == "Liberation Sans" and c.style.title_pt == 24


def test_example_synth_subtitle_below_title():
    c = ConventionAdapter().profile(EXAMPLE)
    title = next(l for l in c.layouts if l.slide_type == SlideType.title)
    sub = next(f for f in title.fields if f.kind == FieldKind.subtitle)
    t = next(f for f in title.fields if f.kind == FieldKind.title)
    assert sub.placeholder_idx is None and not sub.required
    assert sub.bbox.y >= t.bbox.y + t.bbox.h
    assert sub.bbox.y + sub.bbox.h <= 5526372  # не залезает на нижнюю полосу-картинку


def test_interrao_profile_catalog():
    c = TemplateProfilerImpl().profile(INTERRAO)
    assert c.adapter == "profile"
    assert {l.slide_type for l in c.layouts} == set(SlideType)
    for l in c.layouts:
        assert l.source == "sample_slide" and l.sample_slide_index is not None
    assert c.style.body_pt_max == 12
    TemplateCatalog.model_validate_json(c.model_dump_json())


def test_profile_found_by_match_for_renamed_upload(tmp_path):
    dst = tmp_path / "upload_123.pptx"
    shutil.copy(INTERRAO, dst)
    c = TemplateProfilerImpl().profile(str(dst))
    assert c.adapter == "profile" and c.template_id == "upload_123"


def test_errors(tmp_path):
    with pytest.raises(TemplateError, match="не найден"):
        TemplateProfilerImpl().profile(str(tmp_path / "nope.pptx"))
    bad = tmp_path / "bad.pptx"
    bad.write_text("not a zip")
    with pytest.raises(TemplateError, match="PPTX"):
        TemplateProfilerImpl().profile(str(bad))
    with pytest.raises(TemplateError, match="README"):
        ConventionAdapter().profile(INTERRAO)


def test_metrics_wrap_and_capacity():
    lines = metrics.wrap("Выработка электроэнергии составила 19,1 млрд кВт·ч " * 4, 200, 12)
    assert len(lines) > 2
    assert all(metrics.text_width_pt(l, 12) <= 200 for l in lines)
    mc, ml = metrics.capacity(914400 * 4, 914400 * 2, 12)
    assert 8 <= ml <= 12 and mc > 100
