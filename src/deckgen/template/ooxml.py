"""Низкоуровневые помощники по OOXML: наследование кегля/полей/интервала плейсхолдеров."""
from __future__ import annotations

from typing import Optional

from lxml import etree
from pptx.oxml.ns import qn

_TITLE_TYPES = {"title", "ctrTitle"}


def ph_type(sp) -> Optional[str]:
    ph = sp.find(f".//{qn('p:nvPr')}/{qn('p:ph')}")
    if ph is None:
        return None
    return ph.get("type", "body")


def ph_idx(sp) -> Optional[int]:
    ph = sp.find(f".//{qn('p:nvPr')}/{qn('p:ph')}")
    if ph is None:
        return None
    return int(ph.get("idx", "0"))


def _lvl1_sz(el) -> Optional[float]:
    if el is None:
        return None
    for d in el.iter(qn("a:lvl1pPr")):
        rpr = d.find(qn("a:defRPr"))
        if rpr is not None and rpr.get("sz"):
            return int(rpr.get("sz")) / 100
    return None


def _lvl1_lnspc(el) -> Optional[float]:
    if el is None:
        return None
    for d in el.iter(qn("a:lvl1pPr")):
        pct = d.find(f"{qn('a:lnSpc')}/{qn('a:spcPct')}")
        if pct is not None:
            return int(pct.get("val")) / 100000
    return None


def _first_run_sz(sp) -> Optional[float]:
    for r in sp.iter(qn("a:rPr"), qn("a:endParaRPr")):
        if r.get("sz"):
            return int(r.get("sz")) / 100
    return None


def _master_ph(master_el, ptype: str):
    """Плейсхолдер мастера того же типа (title/ctrTitle -> title, прочее -> body)."""
    want = "title" if ptype in _TITLE_TYPES else ("body" if ptype in ("body", "subTitle", "obj", "tbl", "pic") else ptype)
    for sp in master_el.iter(qn("p:sp")):
        if ph_type(sp) == want:
            return sp
    return None


def _master_txstyle(master_el, ptype: Optional[str]):
    tx = master_el.find(qn("p:txStyles"))
    if tx is None:
        return None
    if ptype in _TITLE_TYPES:
        return tx.find(qn("p:titleStyle"))
    if ptype is None:
        return tx.find(qn("p:otherStyle"))
    return tx.find(qn("p:bodyStyle"))


def resolve_font_pt(layout_sp, master_el, default: float = 18.0) -> float:
    """Кегль 1-го уровня: макет (lstStyle) -> плейсхолдер мастера -> txStyles мастера -> текст образца."""
    ptype = ph_type(layout_sp)
    lst = layout_sp.find(f".//{qn('a:lstStyle')}")
    for cand in (
        _lvl1_sz(lst),
        _lvl1_sz(_master_ph(master_el, ptype).find(f".//{qn('a:lstStyle')}")) if ptype and _master_ph(master_el, ptype) is not None else None,
        _lvl1_sz(_master_txstyle(master_el, ptype)),
        _first_run_sz(layout_sp),
    ):
        if cand:
            return cand
    return default


def resolve_ln_spc(layout_sp, master_el) -> float:
    ptype = ph_type(layout_sp)
    for cand in (_lvl1_lnspc(layout_sp.find(f".//{qn('a:lstStyle')}")), _lvl1_lnspc(_master_txstyle(master_el, ptype))):
        if cand:
            return cand
    return 1.0


def body_insets(sp) -> tuple[int, int, int, int]:
    bp = sp.find(f".//{qn('a:bodyPr')}")
    d = (91440, 45720, 91440, 45720)
    if bp is None:
        return d
    return (int(bp.get("lIns", d[0])), int(bp.get("tIns", d[1])), int(bp.get("rIns", d[2])), int(bp.get("bIns", d[3])))


def tostring(el) -> str:
    return etree.tostring(el, encoding="unicode")
