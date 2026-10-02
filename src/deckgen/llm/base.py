"""Единственная точка входа в LLM. Владелец: ML1. Остальные модули НЕ импортируют httpx/openai напрямую.

OpenAI-совместимый POST {base_url}/chat/completions. Режимы structured output (LLM_STRUCTURED_MODE):
  json_schema  -> response_format={"type":"json_schema", ...}  (vLLM, OpenAI, Yandex AI Studio)
  json_object  -> response_format={"type":"json_object"} + схема в системном сообщении
  prompt_only  -> схема в системном сообщении, JSON вырезается из текста ответа
Если сервер отвечает 400 на response_format — режим понижается по цепочке автоматически.
Ответ валидируется pydantic; при ошибке — repair-повтор с текстом ошибки (до MAX_REPAIRS раз).
Сетевые ошибки/429/5xx — ретраи с экспоненциальным backoff. Все вызовы пишутся в out/llm_log.jsonl.
"""
from __future__ import annotations
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

T = TypeVar("T", bound=BaseModel)

MODES = ("json_schema", "json_object", "prompt_only")
MAX_REPAIRS = 2
PLACEHOLDER_URLS = {"", "https://example/v1"}


class LLMError(RuntimeError):
    """Модель ответила, но валидный объект получить не удалось (после repair)."""


class LLMUnavailable(LLMError):
    """Эндпоинт не настроен или недоступен (сеть/5xx после ретраев, 401/403/404)."""


def load_env_file(path: str = ".env") -> None:
    """Минимальный загрузчик .env без зависимостей. Уже заданные переменные окружения не перетираются."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if val[:1] in "\"'" and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), val)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_json(name: str) -> dict:
    raw = _env(name)
    return json.loads(raw) if raw else {}


class LLMConfig(BaseModel):
    """Все параметры — из env (читаются в момент создания объекта, а не при импорте)."""
    base_url: str = Field(default_factory=lambda: _env("LLM_BASE_URL"))
    api_key: str = Field(default_factory=lambda: _env("LLM_API_KEY"))
    model: str = Field(default_factory=lambda: _env("LLM_MODEL", "gpt-oss-120b"))
    structured_mode: str = Field(default_factory=lambda: _env("LLM_STRUCTURED_MODE", "json_schema"))
    timeout_s: float = Field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "120")))
    temperature: float = Field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.2")))
    max_tokens: int = Field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "8000")))
    # gpt-oss (vLLM/провайдеры): "low" сильно сокращает reasoning и латентность. Пусто = не отправлять.
    reasoning_effort: str = Field(default_factory=lambda: _env("LLM_REASONING_EFFORT"))
    # Произвольные поля тела запроса, напр. Qwen3: {"chat_template_kwargs": {"enable_thinking": false}}
    extra_body: dict[str, Any] = Field(default_factory=lambda: _env_json("LLM_EXTRA_BODY"))
    # Схема авторизации: "Bearer" (OpenAI/vLLM) или "Api-Key" (Yandex Cloud API-ключ)
    auth_scheme: str = Field(default_factory=lambda: _env("LLM_AUTH_SCHEME", "Bearer"))
    extra_headers: dict[str, str] = Field(default_factory=lambda: _env_json("LLM_EXTRA_HEADERS"))
    max_retries: int = Field(default_factory=lambda: int(_env("LLM_MAX_RETRIES", "3")))
    backoff_s: float = Field(default_factory=lambda: float(_env("LLM_BACKOFF_S", "1.5")))
    log_path: str = Field(default_factory=lambda: _env("LLM_LOG_PATH", "out/llm_log.jsonl"))


@dataclass
class LLMStats:
    calls: int = 0
    repairs: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


def extract_json(text: str) -> str:
    """Вырезает первый сбалансированный JSON-объект из текста (```json-блоки, <think>, преамбулы)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fence:
        return fence.group(1)
    start = text.find("{")
    if start < 0:
        return text.strip()
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _schema_instruction(schema: Type[BaseModel]) -> str:
    return ("Ответь ТОЛЬКО одним JSON-объектом без пояснений и без markdown, строго по JSON-схеме:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False))


class LLMClient:
    """generate(messages, schema) -> экземпляр schema. Смена модели = смена env, код не меняется."""

    def __init__(self, cfg: Optional[LLMConfig] = None, transport: Optional[httpx.BaseTransport] = None):
        self.cfg = cfg or LLMConfig()
        if self.cfg.structured_mode not in MODES:
            raise ValueError(f"LLM_STRUCTURED_MODE={self.cfg.structured_mode!r}, допустимо: {MODES}")
        self.effective_mode = self.cfg.structured_mode
        self.stats = LLMStats()
        self._transport = transport

    # ---------- публичный API ----------
    def generate(self, messages: list[dict], schema: Type[T]) -> T:
        if self.cfg.base_url.rstrip("/") in PLACEHOLDER_URLS and self._transport is None:
            raise LLMUnavailable("LLM_BASE_URL не задан (см. .env.example)")
        history = list(messages)
        last_err = ""
        for attempt in range(MAX_REPAIRS + 1):
            content = self._complete(history, schema, attempt)
            try:
                raw = content if self.effective_mode == "json_schema" else extract_json(content)
                try:
                    return schema.model_validate_json(raw)
                except ValidationError:
                    if raw is content:  # json_schema, но модель всё же обернула ответ в текст
                        return schema.model_validate_json(extract_json(content))
                    raise
            except ValidationError as e:
                last_err = self._short_error(e)
            if attempt < MAX_REPAIRS:
                self.stats.repairs += 1
                history = history + [
                    {"role": "assistant", "content": content[:6000]},
                    {"role": "user", "content": "Ответ не прошёл проверку схемы. Ошибки:\n" + last_err
                     + "\nИсправь и верни ТОЛЬКО корректный JSON целиком, без пояснений."},
                ]
        raise LLMError(f"Невалидный ответ модели после {MAX_REPAIRS} repair-повторов: {last_err}")

    # ---------- внутреннее ----------
    @staticmethod
    def _short_error(e: ValidationError) -> str:
        lines = []
        for err in e.errors()[:15]:
            loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
            lines.append(f"- {loc}: {err.get('msg')}")
        return "\n".join(lines)

    def _payload(self, messages: list[dict], schema: Type[BaseModel]) -> dict:
        mode = self.effective_mode
        msgs = list(messages)
        if mode != "json_schema":
            instr = _schema_instruction(schema)
            if msgs and msgs[0].get("role") == "system":
                msgs[0] = {"role": "system", "content": msgs[0]["content"] + "\n\n" + instr}
            else:
                msgs.insert(0, {"role": "system", "content": instr})
        body: dict[str, Any] = {"model": self.cfg.model, "messages": msgs,
                                "temperature": self.cfg.temperature, "max_tokens": self.cfg.max_tokens}
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}}
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
        body.update(self.cfg.extra_body)
        return body

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"{self.cfg.auth_scheme} {self.cfg.api_key}"
        h.update(self.cfg.extra_headers)
        return h

    def _complete(self, messages: list[dict], schema: Type[BaseModel], attempt: int) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        net_try = 0
        while True:
            body = self._payload(messages, schema)
            t0 = time.perf_counter()
            resp: Optional[httpx.Response] = None
            error = ""
            try:
                with httpx.Client(timeout=self.cfg.timeout_s, transport=self._transport) as http:
                    resp = http.post(url, json=body, headers=self._headers())
            except httpx.HTTPError as e:
                error = f"{type(e).__name__}: {e}"
            latency = (time.perf_counter() - t0) * 1000
            data: dict = {}
            if resp is not None:
                try:
                    data = resp.json()
                except ValueError:
                    data = {"text": resp.text[:2000]}
                if resp.status_code >= 400:
                    error = f"HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)[:500]}"
            content = ""
            if resp is not None and resp.status_code < 400:
                try:
                    content = data["choices"][0]["message"].get("content") or ""
                except (KeyError, IndexError, TypeError):
                    error = "Некорректный ответ: нет choices[0].message"
            usage = data.get("usage") or {} if isinstance(data, dict) else {}
            self._log(body, content, usage, latency, error, attempt)
            self.stats.calls += 1
            self.stats.latency_ms += latency
            self.stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.stats.completion_tokens += int(usage.get("completion_tokens") or 0)

            status = resp.status_code if resp is not None else None
            if not error:
                return content
            if status == 400 and "response_format" in body and self._downgrade():
                continue  # провайдер не поддерживает режим — пробуем следующий, без ретрая сети
            if status in (401, 403, 404):
                raise LLMUnavailable(error)
            retryable = status is None or status == 429 or status >= 500 or (status < 400 and not content)
            if not retryable:
                raise LLMError(error)
            if net_try >= self.cfg.max_retries:
                raise LLMUnavailable(f"{error} (после {net_try} ретраев)")
            net_try += 1
            time.sleep(self.cfg.backoff_s * (2 ** (net_try - 1)))

    def _downgrade(self) -> bool:
        i = MODES.index(self.effective_mode)
        if i + 1 >= len(MODES):
            return False
        self.effective_mode = MODES[i + 1]
        return True

    def _log(self, body: dict, content: str, usage: dict, latency: float, error: str, attempt: int) -> None:
        if not self.cfg.log_path:
            return
        try:
            p = Path(self.cfg.log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": self.cfg.model, "mode": self.effective_mode,
                   "repair_attempt": attempt, "latency_ms": round(latency, 1), "usage": usage, "error": error,
                   "request": {k: v for k, v in body.items() if k != "response_format"}, "response": content}
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass  # лог не должен ронять генерацию
