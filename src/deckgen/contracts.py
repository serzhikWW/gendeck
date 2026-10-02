"""ЕДИНЫЙ КОНТРАКТ. Менять только через PR с пометкой [CONTRACT] и согласием всех владельцев модулей.
Все модули общаются только этими типами. Никаких dict "на глаз"."""
from __future__ import annotations
from enum import Enum
from typing import Literal, Optional, Protocol
from pydantic import BaseModel, Field


# ---------- Шаблон ----------
class FieldKind(str, Enum):
    title = "title"; subtitle = "subtitle"; text = "text"; table = "table"; image = "image"

class SlideType(str, Enum):
    title = "title"; section = "section"; slide = "slide"; table = "table"; last = "last"

class BBox(BaseModel):
    x: int; y: int; w: int; h: int  # EMU

class FieldSpec(BaseModel):
    name: str                       # TITLE_FIELD, TEXT_FIELD, ... (или логическое имя из YAML-профиля)
    kind: FieldKind
    bbox: BBox
    font_pt: float = 12
    max_chars: int = 0              # оценка ёмкости (0 = неприменимо)
    max_lines: int = 0
    placeholder_idx: Optional[int] = None   # для ConventionAdapter
    required: bool = True

class LayoutSpec(BaseModel):
    layout_id: str                  # имя макета в шаблоне (title1, table_text1, ...) или id профиля
    slide_type: SlideType
    fields: list[FieldSpec]
    source: Literal["layout", "sample_slide"] = "layout"   # "sample_slide" = клонируем слайд-образец
    sample_slide_index: Optional[int] = None

class StyleTokens(BaseModel):
    font_family: str = "Liberation Sans"
    title_pt: float = 20
    body_pt_max: float = 14
    body_pt_min: float = 10
    table_pt_max: float = 11
    table_pt_min: float = 8

class TemplateCatalog(BaseModel):
    template_id: str
    adapter: Literal["convention", "profile"]
    slide_w: int; slide_h: int
    layouts: list[LayoutSpec]
    style: StyleTokens = StyleTokens()

    def by_type(self, t: SlideType) -> list[LayoutSpec]:
        return [l for l in self.layouts if l.slide_type == t]


# ---------- Ввод ----------
class SourceTable(BaseModel):
    table_id: str                   # t1, t2 ... (детерминированно присваивает Ingest)
    columns: list[str]
    rows: list[list[str]]           # ячейки — СТРОКИ как в источнике ("19,1", "—", "≥ 10")
    caption: str = ""

class SourceSection(BaseModel):
    section_id: str
    heading: str = ""
    text: str = ""
    table_ids: list[str] = []

class IngestResult(BaseModel):
    raw_text: str
    source_hash: str
    sections: list[SourceSection]
    tables: list[SourceTable]


# ---------- План презентации (выход LLM) ----------
class Evidence(BaseModel):
    quote: str                      # дословная цитата из источника, подтверждающая числа/факты слайда

class TableSpec(BaseModel):
    source_id: Optional[str] = None # если таблица взята из SourceTable — ячейки подставит Fitter, LLM их не пишет
    columns: list[str] = []
    rows: list[list[str]] = []
    header_rows: int = 1

class SlideSpec(BaseModel):
    type: SlideType
    title: str
    subtitle: str = ""
    bullets: list[str] = []
    table: Optional[TableSpec] = None
    notes: str = ""
    evidence: list[Evidence] = []
    image_prompt: str = ""          # опционально, в полуфинале не используется

class DeckPlan(BaseModel):
    deck_title: str
    slides: list[SlideSpec]


# ---------- После подгонки под шаблон ----------
class FittedSlide(BaseModel):
    spec: SlideSpec                 # уже после разбиения/сокращений; таблица заполнена из SourceTable
    layout_id: str
    font_pt: dict[str, float] = {}  # field name -> кегль
    origin_slide_index: int         # из какого слайда DeckPlan получился (для отчёта)

class FittedDeck(BaseModel):
    template_id: str
    slides: list[FittedSlide]


# ---------- Валидация ----------
class IssueCode(str, Enum):
    NUMBER_MISSING = "NUMBER_MISSING"        # число из слайда не найдено в источнике (или потеряно)
    TABLE_MISMATCH = "TABLE_MISMATCH"        # ячейка таблицы != источник
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    TEXT_OVERFLOW = "TEXT_OVERFLOW"
    EMPTY_PLACEHOLDER = "EMPTY_PLACEHOLDER"
    FONT_NOT_ALLOWED = "FONT_NOT_ALLOWED"
    AUTOFIT_USED = "AUTOFIT_USED"
    PPTX_INVALID = "PPTX_INVALID"
    TOO_MANY_BULLETS = "TOO_MANY_BULLETS"

class Issue(BaseModel):
    severity: Literal["error", "warning"]
    code: IssueCode
    slide_index: Optional[int] = None        # индекс в итоговом PPTX (0-based)
    message: str

class ValidationReport(BaseModel):
    ok: bool
    issues: list[Issue] = []
    metrics: dict[str, float] = {}           # fidelity_numbers, fidelity_tables, overflow_slides, ...


# ---------- Интерфейсы модулей (владельцы указаны в CLAUDE.md) ----------
class TemplateProfiler(Protocol):
    def profile(self, template_path: str) -> TemplateCatalog: ...

class Ingestor(Protocol):
    def ingest(self, text: str) -> IngestResult: ...

class Planner(Protocol):
    def plan(self, src: IngestResult, catalog: TemplateCatalog,
             feedback: Optional[list[Issue]] = None) -> DeckPlan: ...

class Fitter(Protocol):
    def fit(self, plan: DeckPlan, src: IngestResult, catalog: TemplateCatalog) -> FittedDeck: ...

class Renderer(Protocol):
    def render(self, deck: FittedDeck, template_path: str, catalog: TemplateCatalog, out_path: str) -> None: ...

class Validator(Protocol):
    def validate(self, src: IngestResult, deck: FittedDeck, pptx_path: str,
                 catalog: TemplateCatalog) -> ValidationReport: ...
