"""ProfileAdapter: произвольный корпоративный шаблон + YAML-профиль (templates/profiles/*.yaml).

Где ищется профиль (первое совпадение):
  1) <шаблон>.yaml рядом с шаблоном;
  2) каталоги $DECKGEN_PROFILES_DIR, <папка шаблона>/profiles, <репо>/templates/profiles:
     профиль с `template: <имя файла>` либо совпавший по `match` (размер слайда + имена макетов) —
     так находится и загруженный через веб-UI файл с произвольным именем.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from deckgen.contracts import (BBox, FieldKind, FieldSpec, LayoutSpec, SlideType, StyleTokens,
                               TemplateCatalog)

from . import metrics
from .convention import iter_layouts, open_presentation
from .errors import TemplateError

REPO_PROFILES = Path(__file__).resolve().parents[3] / "templates" / "profiles"


@dataclass
class Profile:
    path: Path
    data: dict[str, Any]
    colors: dict[str, str] = field(default_factory=dict)
    text: dict[str, Any] = field(default_factory=dict)

    def layout(self, layout_id: str) -> dict[str, Any]:
        for l in self.data["layouts"]:
            if l["id"] == layout_id:
                return l
        raise TemplateError(f"В профиле {self.path.name} нет макета '{layout_id}'")

    def field(self, layout_id: str, name: str) -> dict[str, Any]:
        for f in self.layout(layout_id).get("fields", []):
            if f["name"] == name:
                return f
        raise TemplateError(f"В профиле {self.path.name} у '{layout_id}' нет поля '{name}'")


def _load(path: Path) -> Profile:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise TemplateError(f"Не удалось прочитать профиль {path}: {e}") from e
    if not isinstance(data, dict) or "layouts" not in data:
        raise TemplateError(f"Профиль {path} не содержит раздела layouts")
    return Profile(path=path, data=data, colors={k: str(v) for k, v in (data.get("colors") or {}).items()},
                   text=data.get("text") or {})


def _matches(data: dict, template_path: Path, prs) -> bool:
    if data.get("template") and Path(str(data["template"])).name == template_path.name:
        return True
    m = data.get("match") or {}
    if not m:
        return False
    if m.get("slide_w") and int(m["slide_w"]) != int(prs.slide_width):
        return False
    if m.get("slide_h") and int(m["slide_h"]) != int(prs.slide_height):
        return False
    names = {lid for lid, _, _ in iter_layouts(prs)}
    return all(n in names for n in m.get("layout_names", []))


def find_profile(template_path: str, prs=None) -> Optional[Profile]:
    tp = Path(template_path)
    side = tp.with_suffix(".yaml")
    if side.is_file():
        return _load(side)
    prs = prs or open_presentation(template_path)
    dirs = [Path(os.environ["DECKGEN_PROFILES_DIR"])] if os.environ.get("DECKGEN_PROFILES_DIR") else []
    dirs += [tp.parent / "profiles", REPO_PROFILES]
    for d in dirs:
        if not d.is_dir():
            continue
        for y in sorted(d.glob("*.yaml")):
            prof = _load(y)
            if _matches(prof.data, tp, prs):
                return prof
    return None


class ProfileAdapter:
    def profile(self, template_path: str) -> TemplateCatalog:
        prs = open_presentation(template_path)
        prof = find_profile(template_path, prs)
        if prof is None:
            raise TemplateError(
                f"Шаблон {Path(template_path).name} не по README организаторов и для него нет YAML-профиля "
                f"(ищется {Path(template_path).with_suffix('.yaml').name} рядом с шаблоном или в templates/profiles/)")
        return catalog_from_profile(prof, prs, Path(template_path).stem)


def catalog_from_profile(prof: Profile, prs, template_id: str) -> TemplateCatalog:
    n_slides = len(prs.slides)
    style = StyleTokens(**(prof.data.get("style") or {}))
    ln_spc = float(prof.text.get("ln_spc", 1.0))
    layouts: list[LayoutSpec] = []
    for l in prof.data["layouts"]:
        try:
            st = SlideType(l["slide_type"])
        except ValueError as e:
            raise TemplateError(f"{prof.path.name}: неизвестный slide_type '{l.get('slide_type')}'") from e
        idx = int(l["sample_slide"])
        if not 1 <= idx <= n_slides:
            raise TemplateError(f"{prof.path.name}: sample_slide {idx} вне диапазона 1..{n_slides}")
        fields = []
        for f in l.get("fields", []):
            kind = FieldKind(f["kind"])
            x, y, w, h = (int(v) for v in f["bbox"])
            font = float(f.get("font_pt", style.body_pt_max))
            mc = ml = 0
            if kind in (FieldKind.title, FieldKind.subtitle, FieldKind.text):
                mc, ml = metrics.capacity(w, h, font, ln_spc if kind == FieldKind.text else 1.0)
            fields.append(FieldSpec(name=f["name"], kind=kind, bbox=BBox(x=x, y=y, w=w, h=h), font_pt=font,
                                    max_chars=mc, max_lines=ml, placeholder_idx=f.get("placeholder_idx"),
                                    required=bool(f.get("required", kind != FieldKind.subtitle))))
        layouts.append(LayoutSpec(layout_id=l["id"], slide_type=st, fields=fields,
                                  source="sample_slide", sample_slide_index=idx - 1))
    return TemplateCatalog(template_id=template_id, adapter="profile", slide_w=int(prs.slide_width),
                           slide_h=int(prs.slide_height), layouts=layouts, style=style)
