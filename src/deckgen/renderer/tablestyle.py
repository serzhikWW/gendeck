"""Разворачивание стиля таблицы шаблона (ppt/tableStyles.xml + цвета темы) в явные цвета ячеек.

Зачем: Р7/OnlyOffice и LibreOffice по-разному (или никак) применяют пользовательские tableStyles.
Если цвета записаны прямо в ячейки, таблица выглядит одинаково везде, а tableStyleId остаётся
для редактирования в PowerPoint/Р7.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Optional

from lxml import etree
from pptx.oxml.ns import qn

RT_TABLE_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles"
RT_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
_SCHEME_ALIAS = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
_PRESET = {"black": "000000", "white": "FFFFFF"}


@dataclass
class Line:
    color: str
    w: int = 12700


@dataclass
class ResolvedTableStyle:
    grid: Optional[Line] = None          # все линии (внешние и внутренние)
    header_rule: Optional[Line] = None   # линия под шапкой
    header_fill: Optional[str] = None
    header_text: Optional[str] = None
    header_bold: bool = True
    body_fill: Optional[str] = None
    band_fill: Optional[str] = None
    body_text: Optional[str] = None


def theme_colors(prs) -> dict[str, str]:
    master = prs.slide_masters[0]
    try:
        theme = master.part.part_related_by(RT_THEME)
    except KeyError:
        return {}
    root = etree.fromstring(theme.blob)
    out = {}
    cs = root.find(f".//{qn('a:clrScheme')}")
    if cs is None:
        return out
    for el in cs:
        name = etree.QName(el).localname
        c = el[0] if len(el) else None
        if c is None:
            continue
        if c.tag == qn("a:srgbClr"):
            out[name] = c.get("val").upper()
        elif c.tag == qn("a:sysClr"):
            out[name] = (c.get("lastClr") or "000000").upper()
    return out


def _hex(rgb: tuple[float, float, float]) -> str:
    return "".join(f"{max(0, min(255, round(v))):02X}" for v in rgb)


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def resolve_color(el, theme: dict[str, str]) -> Optional[str]:
    """a:srgbClr / a:schemeClr / a:sysClr / a:prstClr (+ tint, shade, lumMod, lumOff, alpha) -> RRGGBB.
    alpha смешивается с белым фоном слайда."""
    if el is None:
        return None
    tag = etree.QName(el).localname
    if tag == "srgbClr":
        base = el.get("val")
    elif tag == "schemeClr":
        v = el.get("val")
        base = theme.get(_SCHEME_ALIAS.get(v, v))
    elif tag == "sysClr":
        base = el.get("lastClr")
    elif tag == "prstClr":
        base = _PRESET.get(el.get("val"))
    elif tag == "scrgbClr":
        base = _hex(tuple(int(el.get(k, "0")) / 100000 * 255 for k in ("r", "g", "b")))
    else:
        return None
    if not base:
        return None
    r, g, b = _rgb(base.upper())
    alpha = 1.0
    for mod in el:
        m, val = etree.QName(mod).localname, int(mod.get("val", "100000")) / 100000
        if m == "tint":
            r, g, b = (255 - (255 - c) * val for c in (r, g, b))
        elif m == "shade":
            r, g, b = (c * val for c in (r, g, b))
        elif m in ("lumMod", "lumOff"):
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            l = l * val if m == "lumMod" else l + val
            r, g, b = (c * 255 for c in colorsys.hls_to_rgb(h, max(0, min(1, l)), s))
        elif m == "alpha":
            alpha = val
    if alpha < 1:
        r, g, b = (c * alpha + 255 * (1 - alpha) for c in (r, g, b))
    return _hex((r, g, b))


def _fill(tc_style, theme) -> Optional[str]:
    if tc_style is None:
        return None
    sf = tc_style.find(f"{qn('a:fill')}/{qn('a:solidFill')}")
    return resolve_color(sf[0], theme) if sf is not None and len(sf) else None


def _line(bdr, side: str, theme) -> Optional[Line]:
    if bdr is None:
        return None
    ln = bdr.find(f"{qn('a:' + side)}/{qn('a:ln')}")
    if ln is None:
        return None
    sf = ln.find(qn("a:solidFill"))
    if sf is None or not len(sf):
        return None
    color = resolve_color(sf[0], theme)
    return Line(color, int(ln.get("w", "12700"))) if color else None


def _txt(part, theme) -> tuple[Optional[str], Optional[bool]]:
    tx = part.find(qn("a:tcTxStyle")) if part is not None else None
    if tx is None:
        return None, None
    color = None
    for c in tx:
        if etree.QName(c).localname in ("srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr"):
            color = resolve_color(c, theme)
    b = tx.get("b")
    return color, (b == "on") if b else None


def resolve_table_style(prs, style_id: Optional[str]) -> Optional[ResolvedTableStyle]:
    """None — стиль не описан в tableStyles.xml (встроенный стиль Office): цвета не разворачиваем."""
    if not style_id:
        return None
    try:
        part = prs.part.part_related_by(RT_TABLE_STYLES)
    except KeyError:
        return None
    root = etree.fromstring(part.blob)
    st = next((s for s in root.findall(qn("a:tblStyle")) if s.get("styleId") == style_id), None)
    if st is None:
        return None
    theme = theme_colors(prs)
    whole, first, band = st.find(qn("a:wholeTbl")), st.find(qn("a:firstRow")), st.find(qn("a:band1H"))
    w_style = whole.find(qn("a:tcStyle")) if whole is not None else None
    f_style = first.find(qn("a:tcStyle")) if first is not None else None
    w_bdr = w_style.find(qn("a:tcBdr")) if w_style is not None else None
    f_bdr = f_style.find(qn("a:tcBdr")) if f_style is not None else None
    grid = _line(w_bdr, "insideH", theme) or _line(w_bdr, "bottom", theme) or _line(w_bdr, "left", theme)
    body_text, _ = _txt(whole, theme)
    head_text, head_bold = _txt(first, theme)
    return ResolvedTableStyle(
        grid=grid,
        header_rule=_line(f_bdr, "bottom", theme),
        header_fill=_fill(f_style, theme),
        header_text=head_text or body_text,
        header_bold=True if head_bold is None else head_bold,
        body_fill=_fill(w_style, theme),
        band_fill=_fill(band.find(qn("a:tcStyle")) if band is not None else None, theme),
        body_text=body_text,
    )
