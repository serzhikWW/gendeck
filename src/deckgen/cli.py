"""CLI. Владелец: ML1.
python -m deckgen.cli --input X --template T -o OUT [--demo] [--no-llm]
Печатает стадии пайплайна (те же события, что Progress для UI) и итоговый отчёт;
рядом с OUT сохраняет OUT.report.json и OUT.plan.json."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from .llm.base import load_env_file
from .pipeline import REPO_ROOT, default_modules, run

DEFAULT_TEMPLATE = REPO_ROOT / "templates" / "example_fields.pptx"

# Встроенный пример на случай, если data/samples/01_* ещё нет (данные синтетические)
DEMO_TEXT = """# Итоги производственной деятельности филиала за 2024 год
Докладчик: заместитель директора по производству, февраль 2025 г.

## Производство электроэнергии
Выработка электроэнергии в 2024 году составила 19,1 млрд кВт·ч, что на 3,2 % выше уровня 2023 года.
Коэффициент использования установленной мощности (КИУМ) составил 52,3 %.
Удельный расход условного топлива снизился до 318,4 г/кВт·ч.

## Ремонтная кампания
- Выполнены капитальные ремонты 4 энергоблоков и 2 котлоагрегатов.
- Затраты на ремонтную программу составили 1 234,5 млн руб. при плане 1 300,0 млн руб.
- Аварийность снизилась: зафиксировано 3 технологических нарушения против 7 в 2023 году.

## Ключевые показатели
Таблица 1. Основные показатели, план-факт
| Показатель | Ед. изм. | План 2024 | Факт 2024 | Откл., % |
|---|---|---|---|---|
| Выработка электроэнергии | млрд кВт·ч | 18,7 | 19,1 | 2,1 |
| Отпуск тепла | тыс. Гкал | 5 420 | 5 388 | −0,6 |
| КИУМ | % | 51,0 | 52,3 | 2,5 |
| Затраты на ремонты | млн руб. | 1 300,0 | 1 234,5 | −5,0 |
| Технологические нарушения | шт. | ≤ 5 | 3 | — |

## Задачи на 2025 год
- Завершить модернизацию энергоблока № 2 до 1 декабря 2025 года.
- Довести КИУМ до уровня не ниже 53 %.
- Внедрить систему предиктивной диагностики на 2 энергоблоках.
"""

app = typer.Typer(add_completion=False, help="deckgen: текст + шаблон PPTX -> редактируемая презентация")


def _demo_input() -> tuple[str, str]:
    samples = sorted((REPO_ROOT / "data" / "samples").glob("01_*"))
    if samples:
        return samples[0].read_text(encoding="utf-8"), str(samples[0])
    return DEMO_TEXT, "встроенный пример (data/samples/01_* не найден)"


def _printer(t0: float):
    def emit(stage: str, payload: dict) -> None:
        extra = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "metrics")
        if "metrics" in payload and payload["metrics"]:
            extra += ", " + ", ".join(f"{k}={v:.2f}" for k, v in payload["metrics"].items())
        typer.echo(f"[{time.perf_counter() - t0:6.1f}s] {stage:<13} {extra}")
    return emit


@app.command()
def main(input: Optional[Path] = typer.Option(None, "--input", "-i", help="Текст (.md/.txt)"),
         template: Path = typer.Option(DEFAULT_TEMPLATE, "--template", "-t", help="Шаблон PPTX"),
         output: Optional[Path] = typer.Option(None, "--output", "-o", help="Куда сохранить PPTX"),
         demo: bool = typer.Option(False, "--demo", help="Готовый пример data/samples/01_*"),
         no_llm: bool = typer.Option(False, "--no-llm", help="Без LLM: детерминированный план из секций"),
         env_file: str = typer.Option(".env", "--env-file", help="Файл с LLM_* переменными")) -> None:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]  # кириллица в консоли Windows
    except Exception:
        pass
    load_env_file(env_file)
    if demo:
        text, origin = _demo_input()
        output = output or Path("out") / "demo.pptx"
    elif input is not None:
        text, origin = input.read_text(encoding="utf-8"), str(input)
    else:
        raise typer.BadParameter("нужен --input или --demo")
    output = output or Path("out") / "result.pptx"
    output.parent.mkdir(parents=True, exist_ok=True)

    modules, sources = default_modules(use_llm=not no_llm)
    typer.echo(f"Вход: {origin}\nШаблон: {template}\nМодули: " + ", ".join(f"{k}={v}" for k, v in sources.items()))
    t0 = time.perf_counter()
    report, plan = run(text, str(template), str(output), modules, _printer(t0))

    base = output.with_suffix("")
    Path(f"{base}.report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    Path(f"{base}.plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    errors = [i for i in report.issues if i.severity == "error"]
    typer.echo("\n=== Отчёт ===")
    typer.echo(f"Файл: {output}  ({len(plan.slides)} слайдов в плане, {time.perf_counter() - t0:.1f} с)")
    typer.echo(f"Статус: {'OK' if report.ok else 'есть замечания'}; ошибок: {len(errors)}, "
               f"предупреждений: {len(report.issues) - len(errors)}")
    typer.echo("Метрики: " + json.dumps(report.metrics, ensure_ascii=False))
    for it in report.issues[:20]:
        where = f"слайд {it.slide_index + 1}: " if it.slide_index is not None else ""
        typer.echo(f"  [{it.severity}] {it.code.value} {where}{it.message}")
    if len(report.issues) > 20:
        typer.echo(f"  … ещё {len(report.issues) - 20}, см. {base}.report.json")
    typer.echo(f"План: {base}.plan.json\nОтчёт: {base}.report.json")


if __name__ == "__main__":
    app()
