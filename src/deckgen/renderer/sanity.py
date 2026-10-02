"""Санитарная проверка выходного PPTX на совместимость с Р7-Офис (ML2-6).

`check_pptx(path) -> list[Problem]` — пустой список = файл чистый. Проверки дешёвые (только zip/XML),
их можно звать из валидатора (FS) после каждого рендера. Чек-лист: docs/R7_COMPAT_CHECKLIST.md.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

FONT = "Liberation Sans"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
}
_SLIDE_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
_DESIGN_RE = re.compile(r"^ppt/(slides|slideLayouts|slideMasters|notesSlides|notesMasters)/[^/]+\.xml$")
_DIAGRAM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"


@dataclass
class Problem:
    code: str      # AUTOFIT / BROKEN_REL / CONTENT_TYPE / UNUSED_MEDIA / FONT / SMARTART / MACRO /
                   # EMBEDDED_FONT / ANIMATION / EMPTY_PLACEHOLDER / NO_FONT_SIZE / SLIDE_IDS / APP_PROPS / INVALID
    part: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.part}: {self.message}"


def _rels_path(part: str) -> str:
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


def _resolve(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _rels(z: zipfile.ZipFile, names: set[str], part: str) -> list[tuple[str, str, str]]:
    """[(rId, type, resolved_target)] для внутренних связей части (part='' — корневые связи пакета)."""
    rp = "_rels/.rels" if part == "" else _rels_path(part)
    if rp not in names:
        return []
    out = []
    for rel in ET.fromstring(z.read(rp)).findall("rel:Relationship", NS):
        if rel.get("TargetMode") == "External":
            continue
        out.append((rel.get("Id"), rel.get("Type"), _resolve(part, rel.get("Target"))))
    return out


def check_pptx(path: str) -> list[Problem]:
    probs: list[Problem] = []
    add = lambda c, p, m: probs.append(Problem(c, p, m))
    try:
        z = zipfile.ZipFile(path)
        bad = z.testzip()
    except Exception as e:
        return [Problem("INVALID", path, f"не zip: {e}")]
    if bad:
        add("INVALID", bad, "битый элемент архива")
    names = set(z.namelist())

    # --- content types и связи ---
    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {d.get("Extension").lower() for d in ct.findall("ct:Default", NS)}
    overrides = {o.get("PartName").lstrip("/") for o in ct.findall("ct:Override", NS)}
    for o in overrides - names:
        add("CONTENT_TYPE", o, "Override на несуществующую часть")
    referenced: set[str] = set()
    for part in [""] + sorted(n for n in names if not n.endswith(".rels") and not n.endswith("/")):
        if part and part != "[Content_Types].xml" and part not in overrides \
                and part.rsplit(".", 1)[-1].lower() not in defaults:
            add("CONTENT_TYPE", part, "нет content type")
        for rid, rtype, tgt in _rels(z, names, part):
            if tgt not in names:
                add("BROKEN_REL", part or "_rels/.rels", f"{rid} -> {tgt} не существует")
            referenced.add(tgt)
    for m in sorted(n for n in names if n.startswith("ppt/media/") or n.startswith("ppt/embeddings/")):
        if m not in referenced:
            add("UNUSED_MEDIA", m, "файл не используется ни одной частью")
    for n in names:
        if "vbaProject" in n:
            add("MACRO", n, "макросы запрещены")
        if n.startswith("ppt/diagrams/"):
            add("SMARTART", n, "SmartArt запрещён")
        if n.startswith("ppt/fonts/"):
            add("EMBEDDED_FONT", n, "внедрённые шрифты запрещены")

    # --- presentation.xml ---
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    if pres.find("p:embeddedFontLst", NS) is not None:
        add("EMBEDDED_FONT", "ppt/presentation.xml", "embeddedFontLst")
    pres_rels = {rid: tgt for rid, _, tgt in _rels(z, names, "ppt/presentation.xml")}
    sld_ids = []
    for s in pres.findall("p:sldIdLst/p:sldId", NS):
        sld_ids.append(s.get("id"))
        if pres_rels.get(s.get(f"{{{NS['r']}}}id")) not in names:
            add("SLIDE_IDS", "ppt/presentation.xml", f"sldId {s.get('id')} без слайда")
    if len(set(sld_ids)) != len(sld_ids):
        add("SLIDE_IDS", "ppt/presentation.xml", "повторяющиеся sldId")
    for sid in pres.iter(f"{{{NS['p14']}}}sldId"):
        if sid.get("id") not in sld_ids:
            add("SLIDE_IDS", "ppt/presentation.xml", f"раздел ссылается на удалённый слайд {sid.get('id')}")
    n_slides = len(sld_ids)
    slide_parts = sorted(n for n in names if _SLIDE_RE.match(n))
    if len(slide_parts) != n_slides:
        add("SLIDE_IDS", "ppt/slides", f"частей слайдов {len(slide_parts)}, в sldIdLst {n_slides}")
    if "docProps/app.xml" in names:
        m = re.search(rb"<Slides>(\d+)</Slides>", z.read("docProps/app.xml"))
        if m and int(m.group(1)) != n_slides:
            add("APP_PROPS", "docProps/app.xml", f"Slides={m.group(1).decode()} при {n_slides} слайдах")

    # --- темы ---
    for t in sorted(n for n in names if re.match(r"^ppt/theme/theme\d+\.xml$", n)):
        root = ET.fromstring(z.read(t))
        for kind in ("majorFont", "minorFont"):
            lat = root.find(f".//a:{kind}/a:latin", NS)
            if lat is not None and lat.get("typeface") != FONT:
                add("FONT", t, f"{kind} = {lat.get('typeface')}")

    # --- оформление ---
    for part in sorted(n for n in names if _DESIGN_RE.match(n)):
        xml = z.read(part)
        if b"normAutofit" in xml or b"spAutoFit" in xml:
            add("AUTOFIT", part, "normAutofit/spAutoFit")
        root = ET.fromstring(xml)
        for tag in ("latin", "ea", "cs"):
            for el in root.iter(f"{{{NS['a']}}}{tag}"):
                face = el.get("typeface", "")
                if face and face != FONT and not face.startswith("+"):
                    add("FONT", part, f"{tag} typeface={face}")
                    break
        for gd in root.iter(f"{{{NS['a']}}}graphicData"):
            if gd.get("uri") == _DIAGRAM_URI:
                add("SMARTART", part, "SmartArt в graphicFrame")
        if _SLIDE_RE.match(part):
            _check_slide(root, part, add)
    return probs


def _check_slide(root, part: str, add) -> None:
    for tag in ("anim", "animEffect", "animMotion", "bldLst"):
        if root.find(f".//p:{tag}", NS) is not None:
            add("ANIMATION", part, f"p:{tag}")
            break
    for sp in root.iter(f"{{{NS['p']}}}sp"):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        txt = "".join(t.text or "" for t in sp.iter(f"{{{NS['a']}}}t")).strip()
        if ph is not None and not txt and sp.find(".//a:fld", NS) is None:
            add("EMPTY_PLACEHOLDER", part, f"пустой плейсхолдер type={ph.get('type', 'body')} idx={ph.get('idx', '0')}")
    for r in root.iter(f"{{{NS['a']}}}r"):
        t = r.find("a:t", NS)
        if t is None or not (t.text or "").strip():
            continue
        rpr = r.find("a:rPr", NS)
        if rpr is None or not rpr.get("sz"):
            add("NO_FONT_SIZE", part, f"ран без явного кегля: {t.text[:40]!r}")
            break
