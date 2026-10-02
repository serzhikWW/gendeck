"""ML1-5: scripts/eval_models.py на фейковых моделях (без сети)."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

from deckgen.llm.base import LLMStats, LLMUnavailable

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_models", ROOT / "scripts" / "eval_models.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["eval_models"] = ev  # нужно dataclass-ам скрипта
spec.loader.exec_module(ev)

SAMPLE = "# Отчёт\n\n## Итоги\nВыработка составила 19,1 млрд кВт·ч.\n\n## Таблица\n| A | B |\n|---|---|\n| x | 1,5 |\n"


class GoodLLM:
    def __init__(self):
        self.stats = LLMStats()

    def generate(self, messages, schema):
        self.stats.calls += 1
        self.stats.prompt_tokens += 100
        self.stats.completion_tokens += 50
        return schema.model_validate({"deck_title": "Отчёт", "slides": [
            {"type": "title", "title": "Отчёт"},
            {"type": "slide", "title": "Итоги", "bullets": ["Выработка — 19,1 млрд кВт·ч"]},
            {"type": "table", "title": "Таблица", "table": {"source_id": "t1"}},
            {"type": "last", "title": "Спасибо"}]})


class DownLLM:
    def __init__(self):
        self.stats = LLMStats()

    def generate(self, messages, schema):
        raise LLMUnavailable("down")


def test_evaluate_and_markdown(tmp_path):
    s = tmp_path / "01_x.md"
    s.write_text(SAMPLE, encoding="utf-8")
    factories = {"good": GoodLLM, "down": DownLLM}
    res = ev.evaluate([{"name": "good"}, {"name": "down"}], [s],
                      str(ROOT / "templates" / "example_fields.pptx"), tmp_path / "out",
                      client_factory=lambda spec, d: factories[spec["name"]](), progress=lambda m: None)
    good, down = res
    assert good.ok and good.fallback_used == 0 and good.llm_calls == 1 and good.prompt_tokens == 100
    assert good.metrics["fidelity_tables"] == 1.0 and good.within_limits == 1.0
    assert down.fallback_used == 1 and (tmp_path / "out" / "down" / "01_x.pptx").exists()
    md = ev.render_markdown(res)
    assert "| good | 1.000 | 1.000 | 1/1 |" in md and "| down |" in md


def test_main_without_samples_returns_2(tmp_path):
    assert ev.main(["--samples-dir", str(tmp_path), "--env-file", str(tmp_path / "none")]) == 2
