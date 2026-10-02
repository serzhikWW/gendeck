"""Загрузка промптов из docs/PROMPTS.md (источник истины, версионируется). Путь: DECKGEN_PROMPTS_PATH."""
from __future__ import annotations
import os
import re
from functools import lru_cache
from pathlib import Path

_BLOCK = re.compile(r"<!--\s*prompt:(\w+)\s*-->\s*```[a-z]*\n(.*?)\n```", re.S)


def prompts_path() -> Path:
    env = os.getenv("DECKGEN_PROMPTS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "docs" / "PROMPTS.md"


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, str]:
    text = Path(path).read_text(encoding="utf-8")
    blocks = {m.group(1): m.group(2).strip() for m in _BLOCK.finditer(text)}
    missing = {"system", "fewshot_user", "fewshot_assistant", "outline", "part", "repair"} - set(blocks)
    if missing:
        raise RuntimeError(f"В {path} нет блоков промптов: {sorted(missing)}")
    return blocks


def load_prompts() -> dict[str, str]:
    return _load(str(prompts_path()))


def prompt_version() -> str:
    return load_prompts()["system"].splitlines()[0].strip()
