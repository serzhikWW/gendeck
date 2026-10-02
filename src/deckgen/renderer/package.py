"""Чистка пакета перед сохранением (ML2-6, совместимость с Р7):
- шрифты: темы и все явные гарнитуры -> Liberation Sans (кроме шрифтов буллетов/символов);
- docProps/app.xml пересобирается (в шаблоне там 28 слайдов и чужие заголовки);
- миниатюра шаблона docProps/thumbnail.jpeg удаляется (показывала бы шаблон, а не результат).
"""
from __future__ import annotations

import re
from xml.sax.saxutils import escape


FONT = "Liberation Sans"
RT_THUMBNAIL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
RT_EXT_PROPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_FONT_TAGS = (_A + "latin", _A + "ea", _A + "cs")
_THEME_FONT_RE = re.compile(rb'(<a:(?:major|minor)Font>\s*<a:latin typeface=")[^"]*(")')


def _is_theme_ref(face: str) -> bool:
    return face.startswith("+")  # +mn-lt, +mj-ea ... — ссылки на шрифты темы


def unify_fonts(prs) -> int:
    """Все явные latin/ea/cs вне буллетов -> Liberation Sans; шрифты тем (major/minor latin) -> Liberation Sans."""
    changed = 0
    xml_parts = [s.part for s in prs.slides] + [s.notes_slide.part for s in prs.slides if s.has_notes_slide]
    for m in prs.slide_masters:
        xml_parts.append(m.part)
        xml_parts.extend(l.part for l in m.slide_layouts)
    try:
        xml_parts.append(prs.notes_master.part)
    except Exception:
        pass
    for part in xml_parts:
        for el in part._element.iter(*_FONT_TAGS):
            face = el.get("typeface", "")
            if face and face != FONT and not _is_theme_ref(face):
                el.set("typeface", FONT)
                for a in ("panose", "pitchFamily", "charset"):
                    el.attrib.pop(a, None)
                changed += 1
    for part in prs.part.package.iter_parts():
        if "/ppt/theme/" in str(part.partname) and not hasattr(part, "_element"):
            blob = part.blob
            new = _THEME_FONT_RE.sub(rb"\g<1>" + FONT.encode() + rb"\g<2>", blob)
            if new != blob:
                part._blob = new
                changed += 1
    return changed


def drop_thumbnail(prs) -> None:
    pkg = prs.part.package
    for rid, rel in list(pkg._rels.items()):
        if rel.reltype == RT_THUMBNAIL:
            pkg._rels.pop(rid)


def rewrite_app_props(prs) -> None:
    """Минимальный корректный app.xml: число слайдов/заметок, без чужих TitlesOfParts."""
    pkg = prs.part.package
    part = next((r.target_part for r in pkg._rels.values() if r.reltype == RT_EXT_PROPS), None)
    if part is None:
        return
    n = len(prs.slides)
    notes = sum(1 for s in prs.slides if s.has_notes_slide)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
           f'<Application>{escape("deckgen")}</Application><Slides>{n}</Slides><Notes>{notes}</Notes>'
           '<HiddenSlides>0</HiddenSlides><MMClips>0</MMClips><ScaleCrop>false</ScaleCrop>'
           '<LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>'
           '<HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion></Properties>')
    part._blob = xml.encode("utf-8")
