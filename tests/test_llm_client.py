"""ML1-1: LLMClient на фейковом HTTP (httpx.MockTransport + один тест с настоящим сокет-сервером)."""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from pydantic import BaseModel

from deckgen.llm.base import LLMClient, LLMConfig, LLMError, LLMUnavailable, extract_json, load_env_file


class Item(BaseModel):
    name: str
    value: str


def _cfg(tmp_path, mode="json_schema", **kw) -> LLMConfig:
    base = dict(base_url="http://fake/v1", api_key="k", model="m", structured_mode=mode,
                backoff_s=0, log_path=str(tmp_path / "llm_log.jsonl"))
    base.update(kw)
    return LLMConfig(**base)


def _resp(content: str, usage=None) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}})


class Recorder:
    """Фейковый сервер: отдаёт ответы по очереди и запоминает запросы."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "headers": dict(request.headers),
                              "body": json.loads(request.content)})
        r = self.responses.pop(0)
        return r() if callable(r) else r


def _client(cfg, rec) -> LLMClient:
    return LLMClient(cfg, transport=httpx.MockTransport(rec))


MSG = [{"role": "user", "content": "дай item"}]


def test_json_schema_mode_sends_response_format(tmp_path):
    rec = Recorder(_resp('{"name": "EBITDA", "value": "19,1"}'))
    out = _client(_cfg(tmp_path), rec).generate(MSG, Item)
    assert out == Item(name="EBITDA", value="19,1")
    body = rec.requests[0]["body"]
    assert rec.requests[0]["url"] == "http://fake/v1/chat/completions"
    assert body["model"] == "m"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["value"]["type"] == "string"
    assert rec.requests[0]["headers"]["authorization"] == "Bearer k"


def test_json_object_mode_puts_schema_in_prompt(tmp_path):
    rec = Recorder(_resp('{"name": "a", "value": "b"}'))
    _client(_cfg(tmp_path, "json_object"), rec).generate(MSG, Item)
    body = rec.requests[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert '"value"' in body["messages"][0]["content"]  # JSON-схема добавлена системным сообщением


def test_prompt_only_mode_extracts_json_from_text(tmp_path):
    rec = Recorder(_resp('Вот ответ:\n```json\n{"name": "a", "value": "≥ 10"}\n```\nГотово.'))
    out = _client(_cfg(tmp_path, "prompt_only"), rec).generate(MSG, Item)
    assert out.value == "≥ 10"
    assert "response_format" not in rec.requests[0]["body"]


def test_invalid_json_triggers_repair_then_success(tmp_path):
    rec = Recorder(_resp('{"name": "a"'), _resp('{"name": "a"}'), _resp('{"name": "a", "value": "1"}'))
    client = _client(_cfg(tmp_path), rec)
    out = client.generate(MSG, Item)
    assert out.value == "1"
    assert len(rec.requests) == 3
    # repair-сообщение содержит текст ошибки валидации и предыдущий ответ
    last_msgs = rec.requests[2]["body"]["messages"]
    assert last_msgs[-2]["role"] == "assistant"
    assert "value" in last_msgs[-1]["content"] and "Field required" in last_msgs[-1]["content"]
    assert client.stats.repairs == 2


def test_repair_exhausted_raises(tmp_path):
    rec = Recorder(*[_resp("not json") for _ in range(3)])
    with pytest.raises(LLMError):
        _client(_cfg(tmp_path), rec).generate(MSG, Item)
    assert len(rec.requests) == 3  # 1 + 2 repair


def test_network_retry_with_backoff(tmp_path):
    def boom():
        raise httpx.ConnectError("down")
    rec = Recorder(boom, httpx.Response(503, text="busy"), _resp('{"name": "a", "value": "b"}'))
    out = _client(_cfg(tmp_path), rec).generate(MSG, Item)
    assert out.name == "a" and len(rec.requests) == 3


def test_network_retries_exhausted_raise_unavailable(tmp_path):
    rec = Recorder(*[httpx.Response(500) for _ in range(10)])
    with pytest.raises(LLMUnavailable):
        _client(_cfg(tmp_path, max_retries=2), rec).generate(MSG, Item)
    assert len(rec.requests) == 3


def test_unsupported_response_format_downgrades_mode(tmp_path):
    rec = Recorder(httpx.Response(400, json={"error": {"message": "response_format json_schema is not supported"}}),
                   _resp('{"name": "a", "value": "b"}'))
    client = _client(_cfg(tmp_path), rec)
    client.generate(MSG, Item)
    assert rec.requests[1]["body"]["response_format"] == {"type": "json_object"}
    assert client.effective_mode == "json_object"


def test_reasoning_and_extra_body(tmp_path):
    rec = Recorder(_resp('{"name": "a", "value": "b"}'))
    cfg = _cfg(tmp_path, reasoning_effort="low",
               extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    _client(cfg, rec).generate(MSG, Item)
    body = rec.requests[0]["body"]
    assert body["reasoning_effort"] == "low"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_auth_scheme_and_extra_headers(tmp_path):
    rec = Recorder(_resp('{"name": "a", "value": "b"}'))
    cfg = _cfg(tmp_path, auth_scheme="Api-Key", extra_headers={"OpenAI-Project": "folder1"})
    _client(cfg, rec).generate(MSG, Item)
    h = rec.requests[0]["headers"]
    assert h["authorization"] == "Api-Key k" and h["openai-project"] == "folder1"


def test_log_written(tmp_path):
    rec = Recorder(_resp('{"name": "a", "value": "b"}', {"prompt_tokens": 7, "completion_tokens": 3}))
    client = _client(_cfg(tmp_path), rec)
    client.generate(MSG, Item)
    rows = [json.loads(l) for l in (tmp_path / "llm_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and rows[0]["usage"]["prompt_tokens"] == 7 and "latency_ms" in rows[0]
    assert rows[0]["request"]["messages"][-1]["content"] == "дай item"
    assert client.stats.prompt_tokens == 7 and client.stats.completion_tokens == 3


def test_unconfigured_endpoint_fails_fast(tmp_path):
    with pytest.raises(LLMUnavailable):
        LLMClient(_cfg(tmp_path, base_url="")).generate(MSG, Item)
    with pytest.raises(LLMUnavailable):
        LLMClient(_cfg(tmp_path, base_url="https://example/v1")).generate(MSG, Item)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen")
    monkeypatch.setenv("LLM_STRUCTURED_MODE", "prompt_only")
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"top_p": 0.9}')
    cfg = LLMConfig()
    assert (cfg.base_url, cfg.model, cfg.structured_mode, cfg.extra_body) == ("http://x/v1", "qwen", "prompt_only", {"top_p": 0.9})


def test_load_env_file_strips_inline_comments(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("LLM_MODEL_TEST_X=gpt-oss-120b\nLLM_MODE_TEST_X=json_schema   # json_schema | json_object\n"
                 "# comment\nLLM_Q_TEST_X=\"a b\"\n", encoding="utf-8")
    monkeypatch.delenv("LLM_MODEL_TEST_X", raising=False)
    monkeypatch.delenv("LLM_MODE_TEST_X", raising=False)
    monkeypatch.setenv("LLM_Q_TEST_X", "keep")  # уже заданная переменная окружения не перетирается
    load_env_file(str(p))
    import os
    assert os.environ["LLM_MODEL_TEST_X"] == "gpt-oss-120b"
    assert os.environ["LLM_MODE_TEST_X"] == "json_schema"
    assert os.environ["LLM_Q_TEST_X"] == "keep"


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert json.loads(extract_json('text {"a": {"b": "}"}} tail')) == {"a": {"b": "}"}}
    assert json.loads(extract_json('<think>{x}</think>```json\n{"a": 2}\n```')) == {"a": 2}


def test_real_socket_server(tmp_path):
    """Настоящий HTTP-сервер на localhost: невалидный ответ -> repair -> успех."""
    answers = ['```\n{"name": 1}\n```', '{"name": "ok", "value": "12,4"}']

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"choices": [{"message": {"content": answers.pop(0)}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = _cfg(tmp_path, base_url=f"http://127.0.0.1:{srv.server_port}/v1")
        assert LLMClient(cfg).generate(MSG, Item).value == "12,4"
    finally:
        srv.shutdown()


def test_yandex_style_config(tmp_path):
    """ML1-6: конфигурация Yandex AI Studio — только env/конфиг, код тот же."""
    rec = Recorder(httpx.Response(400, json={"error": {"message": "unsupported response_format"}}),
                   _resp('{"name": "a", "value": "b"}'))
    cfg = _cfg(tmp_path, base_url="https://ai.api.cloud.yandex.net/v1", model="gpt://b1gfolder/yandexgpt/latest",
               auth_scheme="Api-Key", extra_headers={"OpenAI-Project": "b1gfolder"})
    client = _client(cfg, rec)
    assert client.generate(MSG, Item).value == "b"
    first = rec.requests[0]
    assert first["url"] == "https://ai.api.cloud.yandex.net/v1/chat/completions"
    assert first["headers"]["authorization"] == "Api-Key k" and first["headers"]["openai-project"] == "b1gfolder"
    assert first["body"]["model"] == "gpt://b1gfolder/yandexgpt/latest" and "reasoning_effort" not in first["body"]
    assert client.effective_mode == "json_object"  # json_schema не поддержан -> понижение без правок кода
