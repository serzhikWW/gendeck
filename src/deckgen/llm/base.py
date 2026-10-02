"""Единственная точка входа в LLM. Владелец: ML1. Остальные модули НЕ импортируют httpx/openai напрямую."""
from __future__ import annotations
import os
from typing import Optional, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLMConfig(BaseModel):
    base_url: str = os.getenv("LLM_BASE_URL", "")
    api_key: str = os.getenv("LLM_API_KEY", "")
    model: str = os.getenv("LLM_MODEL", "gpt-oss-120b")
    structured_mode: str = os.getenv("LLM_STRUCTURED_MODE", "json_schema")
    timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "120"))

class LLMClient:
    """generate(messages, schema) -> экземпляр schema. Реализация (ML1): OpenAI-совместимый /chat/completions,
    structured_mode json_schema -> json_object -> prompt_only (фолбэк), валидация pydantic, repair-повтор до 2 раз."""
    def __init__(self, cfg: Optional[LLMConfig] = None): self.cfg = cfg or LLMConfig()
    def generate(self, messages: list[dict], schema: Type[T]) -> T:
        raise NotImplementedError("ML1: реализовать")
