"""ML1-5. Прогон золотого набора (data/samples) через несколько моделей -> docs/MODEL_COMPARISON.md.

Модели задаются без правок кода:
  python scripts/eval_models.py --model gpt-oss-120b --model qwen3.6-35b-a3b      # общий LLM_BASE_URL/KEY из .env
  python scripts/eval_models.py --models-file scripts/models.json                  # разные провайдеры
models.json: [{"name": "yandexgpt", "model": "gpt://<folder>/yandexgpt/latest",
               "base_url": "https://llm.api.cloud.yandex.net/v1", "api_key_env": "YANDEX_API_KEY",
               "auth_scheme": "Bearer", "structured_mode": "json_schema"}, ...]
Метрики: из ValidationReport.metrics (fidelity_numbers, fidelity_tables, ...), доля repair-итераций пайплайна,
repair-повторы JSON в LLM-клиенте, доля слайдов в лимитах каталога, fallback, время, токены.
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deckgen.llm.base import LLMClient, LLMConfig, load_env_file  # noqa: E402
from deckgen.pipeline import default_modules, run  # noqa: E402
from deckgen.planner.limits import catalog_limits, limit_violations  # noqa: E402

CFG_KEYS = {"model", "base_url", "structured_mode", "reasoning_effort", "extra_body", "auth_scheme",
            "extra_headers", "timeout_s", "temperature", "max_tokens"}


@dataclass
class SampleResult:
    model: str
    sample: str
    ok: bool = False
    error: str = ""
    seconds: float = 0.0
    slides: int = 0
    repair_iterations: float = 0.0
    fallback_used: float = 0.0
    llm_calls: int = 0
    json_repairs: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    within_limits: float = 0.0
    metrics: dict = field(default_factory=dict)


def make_client(spec: dict, log_dir: Path) -> LLMClient:
    kw = {k: v for k, v in spec.items() if k in CFG_KEYS}
    if spec.get("api_key_env"):
        kw["api_key"] = os.getenv(spec["api_key_env"], "")
    kw["log_path"] = str(log_dir / f"{spec['name']}.llm_log.jsonl")
    return LLMClient(LLMConfig(**kw))


def evaluate(specs: list[dict], samples: list[Path], template: str, out_dir: Path,
             client_factory: Callable[[dict, Path], object] = make_client,
             progress: Callable[[str], None] = print) -> list[SampleResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[SampleResult] = []
    for spec in specs:
        client = client_factory(spec, out_dir)
        modules, _ = default_modules(use_llm=True, llm_client=client)
        limits = catalog_limits(modules.profiler.profile(template))
        for sample in samples:
            r = SampleResult(model=spec["name"], sample=sample.name)
            st = getattr(client, "stats", None)
            before = asdict(st) if st is not None else {}
            t0 = time.perf_counter()
            try:
                out = out_dir / spec["name"] / (sample.stem + ".pptx")
                out.parent.mkdir(parents=True, exist_ok=True)
                report, plan = run(sample.read_text(encoding="utf-8"), template, str(out), modules)
                bad = {i for i, _ in limit_violations(plan, limits)}
                r.ok, r.metrics, r.slides = report.ok, dict(report.metrics), len(plan.slides)
                r.repair_iterations = report.metrics.get("repair_iterations", 0.0)
                r.fallback_used = report.metrics.get("fallback_used", 0.0)
                r.within_limits = 1 - len(bad) / len(plan.slides) if plan.slides else 0.0
                (out.with_suffix(".report.json")).write_text(report.model_dump_json(indent=2), encoding="utf-8")
                (out.with_suffix(".plan.json")).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            except Exception as e:
                r.error = f"{type(e).__name__}: {e}"
            r.seconds = time.perf_counter() - t0
            if st is not None:
                after = asdict(st)
                r.llm_calls = after["calls"] - before["calls"]
                r.json_repairs = after["repairs"] - before["repairs"]
                r.prompt_tokens = after["prompt_tokens"] - before["prompt_tokens"]
                r.completion_tokens = after["completion_tokens"] - before["completion_tokens"]
            progress(f"{r.model:<20} {r.sample:<40} ok={r.ok} fallback={int(r.fallback_used)} "
                     f"repairs={r.repair_iterations:.0f} {r.seconds:.1f}s {r.error}")
            results.append(r)
    return results


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def render_markdown(results: list[SampleResult], note: str = "") -> str:
    models = list(dict.fromkeys(r.model for r in results))
    lines = ["# Сравнение моделей (генерируется scripts/eval_models.py — не править руками)", "",
             f"Дата прогона: {time.strftime('%Y-%m-%d %H:%M')}. Образцов: "
             f"{len({r.sample for r in results})}. Смена модели = смена переменных окружения, код не менялся.", ""]
    if note:
        lines += [note, ""]
    lines += ["| Модель | fidelity_numbers | fidelity_tables | Без ошибок валидации | Слайдов в лимитах | "
              "Repair-итерации (ср.) | JSON-repair (ср.) | Fallback | Время, с (ср.) | Токены вход/выход (ср.) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in results if r.model == m]
        done = [r for r in rs if not r.error]
        lines.append("| {} | {:.3f} | {:.3f} | {}/{} | {:.1%} | {:.2f} | {:.2f} | {}/{} | {:.1f} | {:.0f} / {:.0f} |".format(
            m, _mean([r.metrics.get("fidelity_numbers", 0) for r in done]),
            _mean([r.metrics.get("fidelity_tables", 0) for r in done]),
            sum(r.ok for r in rs), len(rs), _mean([r.within_limits for r in done]),
            _mean([r.repair_iterations for r in done]), _mean([r.json_repairs for r in rs]),
            int(sum(r.fallback_used for r in done)), len(rs), _mean([r.seconds for r in rs]),
            _mean([r.prompt_tokens for r in rs]), _mean([r.completion_tokens for r in rs])))
    lines += ["", "Fallback > 0 означает, что модель/эндпоинт не дали валидный план и сработала деградация "
              "(план без LLM) — такие прогоны не характеризуют качество модели.", "",
              "## По образцам", "", "| Модель | Образец | OK | Слайдов | В лимитах | Repair | Fallback | Время, с | Ошибка |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.model} | {r.sample} | {'✅' if r.ok else '❌'} | {r.slides} | {r.within_limits:.0%} | "
                     f"{r.repair_iterations:.0f} | {int(r.fallback_used)} | {r.seconds:.1f} | {r.error[:80]} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", default=[], help="имя модели (общий LLM_BASE_URL/LLM_API_KEY)")
    ap.add_argument("--models-file", type=Path, help="JSON-список конфигураций моделей")
    ap.add_argument("--samples-dir", type=Path, default=ROOT / "data" / "samples")
    ap.add_argument("--template", default=str(ROOT / "templates" / "example_fields.pptx"))
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "eval")
    ap.add_argument("--report", type=Path, default=ROOT / "docs" / "MODEL_COMPARISON.md")
    ap.add_argument("--limit", type=int, default=0, help="взять первые N образцов")
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    a = ap.parse_args(argv)
    load_env_file(a.env_file)

    specs = [{"name": m.replace("/", "_").replace(":", "_"), "model": m} for m in a.model]
    if a.models_file:
        specs += json.loads(a.models_file.read_text(encoding="utf-8"))
    if not specs:
        specs = [{"name": os.getenv("LLM_MODEL", "default").replace("/", "_"), "model": os.getenv("LLM_MODEL", "")}]
    samples = sorted(p for p in a.samples_dir.glob("*") if p.suffix in (".md", ".txt"))
    if a.limit:
        samples = samples[:a.limit]
    if not samples:
        print(f"Нет образцов в {a.samples_dir} (ждём BA-1)", file=sys.stderr)
        return 2
    results = evaluate(specs, samples, a.template, a.out_dir)
    a.report.write_text(render_markdown(results), encoding="utf-8")
    (a.out_dir / "results.json").write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(f"Готово: {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
