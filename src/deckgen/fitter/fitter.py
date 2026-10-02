"""Fitter: DeckPlan + IngestResult + TemplateCatalog -> FittedDeck. Чистая детерминированная функция.

Что делает (по порядку):
  1. Таблицы по `source_id` заполняются из SourceTable как есть (строки не трогаем).
  2. Нормализация: slide<->table по наличию таблицы; пустой контентный слайд -> section; мусорные маркеры буллетов.
  3. Выбор макета по наполнению (текст / таблица / оба), с фолбэками, если в шаблоне чего-то нет.
  4. Ёмкость: кегль снижается до StyleTokens.*_min; не влезло — слайд делится («… (1/2)»),
     таблицы режутся по строкам с повтором шапки. Текст никогда не обрезается и не переписывается.
  5. Подряд идущие одинаковые слайды схлопываются.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from deckgen.contracts import (BBox, DeckPlan, FieldKind, FittedDeck, FittedSlide, IngestResult, LayoutSpec,
                               SlideSpec, SlideType, SourceTable, TableSpec, TemplateCatalog)
from deckgen.template.geometry import content_zones, first_field
from deckgen.template.textstyle import MAX_BULLETS_PER_SLIDE, TITLE_MIN_RATIO

from .measure import layout_table, table_grid, text_fits, title_lines

_BULLET_MARK = re.compile(r"^\s*(?:[•●▪◦·\-–—*]|\d{1,2}[.)])\s+")
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZА-ЯЁ0-9«\"(])")
_CONTENT = (SlideType.slide, SlideType.table)
_FALLBACK = {
    SlideType.title: [SlideType.title, SlideType.section, SlideType.slide, SlideType.table],
    SlideType.section: [SlideType.section, SlideType.title, SlideType.slide, SlideType.table],
    SlideType.last: [SlideType.last, SlideType.section, SlideType.title, SlideType.slide, SlideType.table],
}


def _font_steps(hi: float, lo: float, step: float = 0.5) -> list[float]:
    out, f = [hi], math.floor(hi / step) * step
    if f == hi:
        f -= step
    while f >= lo - 1e-9:
        out.append(round(f, 2))
        f -= step
    return out


@dataclass
class _Part:
    spec: SlideSpec
    layout: LayoutSpec
    font_pt: dict[str, float] = field(default_factory=dict)


class DeterministicFitter:
    def fit(self, plan: DeckPlan, src: IngestResult, catalog: TemplateCatalog) -> FittedDeck:
        tables = {t.table_id: t for t in src.tables}
        out: list[FittedSlide] = []
        for i, raw in enumerate(plan.slides):
            spec = self._normalize(raw, tables)
            if spec is None:
                continue
            parts = self._fit_one(spec, catalog)
            n = len(parts)
            for k, p in enumerate(parts, 1):
                s = p.spec
                if n > 1:
                    s = s.model_copy(update={"title": f"{s.title} ({k}/{n})"})
                out.append(FittedSlide(spec=s, layout_id=p.layout.layout_id, font_pt=p.font_pt, origin_slide_index=i))
        return FittedDeck(template_id=catalog.template_id, slides=self._dedup(out))

    # ---------- 1-2. нормализация ----------
    @staticmethod
    def _table_from_source(t: TableSpec, tables: dict[str, SourceTable]) -> Optional[TableSpec]:
        if t.source_id and t.source_id in tables:
            st = tables[t.source_id]
            return TableSpec(source_id=st.table_id, columns=list(st.columns), rows=[list(r) for r in st.rows],
                             header_rows=1 if st.columns else 0)
        if t.rows or t.columns:
            return t.model_copy(deep=True)
        return None  # ссылка в никуда и ячеек нет — таблицы нет

    def _normalize(self, raw: SlideSpec, tables: dict[str, SourceTable]) -> Optional[SlideSpec]:
        s = raw.model_copy(deep=True)
        s.title = (s.title or "").strip()
        s.subtitle = (s.subtitle or "").strip()
        s.bullets = [b for b in (_BULLET_MARK.sub("", x).strip() for x in s.bullets) if b]
        s.table = self._table_from_source(s.table, tables) if s.table else None
        if s.type in _CONTENT:
            if s.table is not None:
                s.type = SlideType.table
            elif s.bullets or s.subtitle:
                s.type = SlideType.slide
            elif s.title:
                s.type = SlideType.section
            else:
                return None
        return s

    # ---------- 3. выбор макета ----------
    @staticmethod
    def _pick_content(catalog: TemplateCatalog, has_text: bool, has_table: bool) -> LayoutSpec:
        best, best_score = None, -1e9
        for order, l in enumerate(catalog.layouts):
            if l.slide_type not in _CONTENT:
                continue
            z = content_zones(l, has_text, has_table)
            score = 0.0
            if has_text:
                score += 10 if "text" in z else -100
            if has_table:
                score += 10 if "table" in z else -100
            if not has_table and first_field(l, FieldKind.table):
                score -= 20
            if not has_text and first_field(l, FieldKind.text):
                score -= 2  # макет «текст+таблица» без текста — хуже, чем «только таблица»
            score -= 0.5 * sum(1 for f in l.fields if f.kind == FieldKind.image)
            score += 1 if l.slide_type == (SlideType.table if has_table else SlideType.slide) else 0
            score -= order * 1e-3  # при равенстве — порядок шаблона
            if score > best_score:
                best, best_score = l, score
        if best is None:
            raise ValueError(f"В каталоге {catalog.template_id} нет ни одного макета slide/table")
        return best

    @staticmethod
    def _pick_typed(catalog: TemplateCatalog, t: SlideType) -> LayoutSpec:
        for cand in _FALLBACK[t]:
            ls = catalog.by_type(cand)
            if ls:
                return ls[0]
        raise ValueError(f"В каталоге {catalog.template_id} нет макетов")

    # ---------- 4. ёмкость ----------
    def _fit_one(self, s: SlideSpec, catalog: TemplateCatalog) -> list[_Part]:
        if s.type not in _CONTENT:
            layout = self._pick_typed(catalog, s.type)
            p = _Part(s, layout)
            self._fit_titles(p, catalog)
            if layout.slide_type in _CONTENT and s.subtitle:
                # тип не нашёлся — титул/раздел на контентном макете: подзаголовок идёт текстом
                z = content_zones(layout, True, False).get("text")
                if z:
                    p.font_pt[z.name] = catalog.style.body_pt_max
            return [p]
        has_text = bool(s.bullets or s.subtitle)
        if s.table is not None and has_text:
            layout = self._pick_content(catalog, True, True)
            z = content_zones(layout, True, True)
            if "text" in z and "table" in z:
                text_font = self._best_text_font(self._paras(s), z["text"].bbox, catalog)
                table_font = self._single_table_font(s.table, z["table"].bbox, catalog)
                if text_font is not None and table_font is not None and len(s.bullets) <= MAX_BULLETS_PER_SLIDE:
                    p = _Part(s, layout, {z["text"].name: text_font, z["table"].name: table_font})
                    self._fit_titles(p, catalog)
                    return [p]
            text_only = s.model_copy(update={"type": SlideType.slide, "table": None})
            table_only = s.model_copy(update={"bullets": [], "subtitle": "", "notes": ""})
            return self._fit_text(text_only, catalog) + self._fit_table(table_only, catalog)
        if s.table is not None:
            return self._fit_table(s, catalog)
        return self._fit_text(s, catalog)

    def _fit_titles(self, p: _Part, catalog: TemplateCatalog) -> None:
        for f in p.layout.fields:
            if f.kind == FieldKind.title:
                text = p.spec.title
            elif f.kind == FieldKind.subtitle:
                text = p.spec.subtitle
            else:
                continue
            base = f.font_pt or catalog.style.title_pt
            chosen = base
            if text:
                for size in _font_steps(base, base * TITLE_MIN_RATIO):
                    need, fit = title_lines(text, f.bbox, size)
                    chosen = size
                    if need <= fit:
                        break
            p.font_pt[f.name] = chosen

    # --- текст ---
    @staticmethod
    def _paras(s: SlideSpec) -> list[str]:
        return ([s.subtitle] if s.subtitle else []) + list(s.bullets)

    @staticmethod
    def _best_text_font(paras: list[str], zone: BBox, catalog: TemplateCatalog) -> Optional[float]:
        st = catalog.style
        for f in _font_steps(st.body_pt_max, st.body_pt_min):
            if text_fits(paras, zone, f):
                return f
        return None

    def _split_long_bullets(self, bullets: list[str], zone: BBox, catalog: TemplateCatalog) -> list[str]:
        out = []
        for b in bullets:
            if text_fits([b], zone, catalog.style.body_pt_min):
                out.append(b)
                continue
            out.extend(x for x in _SENT_SPLIT.split(b) if x.strip())
        return out

    def _fit_text(self, s: SlideSpec, catalog: TemplateCatalog) -> list[_Part]:
        layout = self._pick_content(catalog, True, False)
        zone = content_zones(layout, True, False).get("text")
        if zone is None:  # в шаблоне нет текстового поля вообще — отдаём как есть
            p = _Part(s, layout)
            self._fit_titles(p, catalog)
            return [p]
        bullets = self._split_long_bullets(s.bullets, zone.bbox, catalog)
        lead = [s.subtitle] if s.subtitle else []
        n = len(bullets)
        chunks: list[list[str]] = [bullets]
        font = self._best_text_font(lead + bullets, zone.bbox, catalog) if n <= MAX_BULLETS_PER_SLIDE else None
        if font is None:
            for k in range(2, max(n, 2) + 1):
                cand = _balanced(bullets, k)
                fonts = [self._best_text_font((lead if i == 0 else []) + c, zone.bbox, catalog)
                         for i, c in enumerate(cand)]
                if all(f is not None for f in fonts) and all(len(c) <= MAX_BULLETS_PER_SLIDE for c in cand):
                    chunks, font = cand, min(fonts)
                    break
            else:  # даже по одному буллету не влезает — минимальный кегль, переполнение отметит валидатор
                chunks, font = [[b] for b in bullets] or [[]], catalog.style.body_pt_min
        parts = []
        for i, c in enumerate(chunks):
            spec = s.model_copy(update={"bullets": c, "subtitle": s.subtitle if i == 0 else "",
                                        "notes": s.notes if i == 0 else ""})
            p = _Part(spec, layout, {zone.name: font})
            self._fit_titles(p, catalog)
            parts.append(p)
        return parts

    # --- таблицы ---
    @staticmethod
    def _single_table_font(t: TableSpec, zone: BBox, catalog: TemplateCatalog) -> Optional[float]:
        grid, hn = table_grid(t)
        st = catalog.style
        for f in _font_steps(st.table_pt_max, st.table_pt_min):
            tl = layout_table(grid, hn, zone.w, f)
            if tl.fits_width and tl.height <= zone.h:
                return f
        return None

    def _fit_table(self, s: SlideSpec, catalog: TemplateCatalog) -> list[_Part]:
        layout = self._pick_content(catalog, False, True)
        zone = content_zones(layout, False, True).get("table")
        if zone is None:
            p = _Part(s, layout)
            self._fit_titles(p, catalog)
            return [p]
        font = self._single_table_font(s.table, zone.bbox, catalog)
        if font is not None:
            p = _Part(s, layout, {zone.name: font})
            self._fit_titles(p, catalog)
            return [p]
        st = catalog.style
        grid, hn = table_grid(s.table)
        # кегль для нарезки: самый крупный, при котором слова влезают в колонки, но не крупнее 11pt
        cap = max(st.table_pt_min, min(st.table_pt_max, 11.0))
        font = next((f for f in _font_steps(cap, st.table_pt_min)
                     if layout_table(grid, hn, zone.bbox.w, f).fits_width), st.table_pt_min)
        # нарезаем ИСХОДНЫЕ строки (без выравнивания), шапка = columns + (header_rows-1) первых строк
        t0 = s.table
        n_hdr_rows = max(hn - 1, 0) if t0.columns else 0
        hdr_rows, body = t0.rows[:n_hdr_rows], t0.rows[n_hdr_rows:]
        def chunk_fits(c: list) -> bool:
            g, h = table_grid(t0.model_copy(update={"rows": hdr_rows + c}))  # ровно то, что увидит рендерер
            return layout_table(g, h, zone.bbox.w, font).height <= zone.bbox.h

        full = layout_table(grid, hn, zone.bbox.w, font)
        avail = zone.bbox.h - sum(full.row_h[:hn])
        k0 = max(1, math.ceil(sum(full.row_h[hn:]) / max(avail, 1)))  # нижняя оценка числа слайдов
        chunks = [body]
        for k in range(k0, max(len(body), 1) + 1):
            cand = _balanced(body, k)
            if all(chunk_fits(c) for c in cand):
                chunks = cand
                break
        else:
            chunks = [[r] for r in body]
        parts = []
        for i, c in enumerate(chunks):
            t = t0.model_copy(update={"rows": [list(r) for r in hdr_rows] + [list(r) for r in c]})
            spec = s.model_copy(update={"table": t, "notes": s.notes if i == 0 else ""})
            p = _Part(spec, layout, {zone.name: font})
            self._fit_titles(p, catalog)
            parts.append(p)
        return parts

    # ---------- 5. дубли ----------
    @staticmethod
    def _dedup(slides: list[FittedSlide]) -> list[FittedSlide]:
        out: list[FittedSlide] = []
        for fs in slides:
            if out:
                prev = out[-1].spec
                cur = fs.spec
                same = (prev.type, prev.title, prev.subtitle, prev.bullets, prev.table) == \
                       (cur.type, cur.title, cur.subtitle, cur.bullets, cur.table)
                if same:
                    continue
            out.append(fs)
        return out


def _balanced(items: list, k: int) -> list[list]:
    """Делит список на k подряд идущих частей почти равного размера."""
    n = len(items)
    k = max(1, min(k, n)) if n else 1
    base, extra = divmod(n, k)
    out, i = [], 0
    for j in range(k):
        size = base + (1 if j < extra else 0)
        out.append(items[i:i + size])
        i += size
    return out
