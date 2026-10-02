# STATUS (обновлять свои строки; ✅ готово, 🔄 в работе, ⬜ не начато)
| ID | Владелец | Задача | Статус |
|---|---|---|---|
| BA-1..BA-6 | BA | см. tasks/04_BA.md | ⬜ |
| ML1-1..ML1-6 | ML1 | см. tasks/01_ML1.md — ML1-1 ✅ LLMClient; ML1-2 ✅ LLMPlanner (промпт v1, лимиты, map-reduce, санитизация); ML1-3 ✅ repair-цикл (remap индексов, лучший план, warning), fallback plan_without_llm; ML1-4 ✅ CLI (typer, стадии, *.report.json/*.plan.json, --demo/--no-llm; без сети создаёт PPTX), временные stubs.py до появления create() у ML2/BA/FS; ML1-5 ✅ scripts/eval_models.py (метрики, repair, лимиты, время, токены → docs/MODEL_COMPARISON.md); ML1-6 ✅ Yandex через тот же клиент (Api-Key, OpenAI-Project, авто-понижение режима), различия в PROMPTS.md. Ждём: data/samples+gold (BA), create() у модулей ML2/BA/FS, ключи API для eval | 🔄 |
| ML2-1..ML2-6 | ML2 | см. tasks/02_ML2.md | ⬜ |
| ML1-1..ML1-6 | ML1 | см. tasks/01_ML1.md | ⬜ |
| ML2-1 | ML2 | Profiler: ConventionAdapter (example_fields) | ✅ |
| ML2-2 | ML2 | Profiler: ProfileAdapter + templates/profiles/interrao.yaml | ✅ |
| ML2-3 | ML2 | Fitter: таблицы из SourceTable, выбор макета, кегль/деление слайдов | ✅ |
| ML2-4 | ML2 | Renderer: оба шаблона → PPTX, проверено в LibreOffice (PNG). Ждёт data/gold/*.plan.json от BA | 🔄 |
| ML2-5 | ML2 | Таблицы: стиль шаблона явно в ячейках, числа вправо, нарезка 12×6/30×8, узкие таблицы не растягиваются | ✅ |
| ML2-6 | ML2 | Р7: `renderer/sanity.check_pptx`, чистка пакета, `test_pptx_sanity.py`, `docs/R7_COMPAT_CHECKLIST.md`. Ручной прогон в Р7 — FS-6/BA | 🔄 |
| FS-1..FS-6 | FS | см. tasks/03_FS.md | ⬜ |
