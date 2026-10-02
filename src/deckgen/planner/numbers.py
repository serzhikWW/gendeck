"""Числовые токены и дословный поиск в источнике. Числа сравниваются как СТРОКИ: "19,1" != "19.1"."""
from __future__ import annotations
import re

_WS = re.compile(r"[\s\u00a0\u202f\u2009\u2007]+")
# "1 234,5" (группы по 3 через пробел/nbsp) | "19,1" | "2024" | "12.4"
NUM_RE = re.compile(r"\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_SENT_SPLIT = re.compile(r"(?<=[.!?…;])\s+|\n+")
_QUOTE_STRIP = re.compile(r"^[\s#>*•\-–—]+")


def norm_ws(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def numbers_in(text: str) -> list[str]:
    return [norm_ws(m.group(0)) for m in NUM_RE.finditer(text or "")]


def number_in_source(token: str, source: str) -> bool:
    """Токен встречается в source как самостоятельное число (не как часть "19,1" или "2019")."""
    tok = re.escape(norm_ws(token)).replace(r"\ ", " ")
    return re.search(rf"(?<!\d)(?<!\d[.,]){tok}(?!\d)(?![.,]\d)", norm_ws(source)) is not None


def quote_in_source(quote: str, source: str) -> bool:
    q = norm_ws(quote)
    return bool(q) and q in norm_ws(source)


def sentences(raw: str) -> list[str]:
    """Дословные фрагменты источника (предложения/строки) для автоцитат."""
    out = []
    for part in _SENT_SPLIT.split(raw or ""):
        part = _QUOTE_STRIP.sub("", part).strip()
        if part:
            out.append(part)
    return out


def find_quote(tokens: list[str], raw: str, _cache: dict | None = None) -> list[str]:
    """Минимальный набор дословных фрагментов источника, покрывающий все токены.
    Предпочтение: один фрагмент со всеми числами, проза раньше строк таблиц, короче — лучше."""
    if not tokens:
        return []
    sents = sentences(raw)
    def key(s: str):
        return ("|" in s, len(s))
    full = sorted((s for s in sents if all(number_in_source(t, s) for t in tokens)), key=key)
    if full:
        return [full[0]]
    out: list[str] = []
    for t in tokens:
        if any(number_in_source(t, q) for q in out):
            continue
        cands = sorted((s for s in sents if number_in_source(t, s)), key=key)
        if cands:
            out.append(cands[0])
    return out
