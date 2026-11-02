"""OpenAIChat 的参数兼容逻辑（不联网，用假 client）。"""
from types import SimpleNamespace

import pytest
from openai import BadRequestError

from kqa.llm import OpenAIChat


def _resp(tool_calls=None, content="ok"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _bad_request(msg):
    response = SimpleNamespace(request=None, status_code=400, headers={})
    return BadRequestError(msg, response=response, body=None)


class FakeCompletions:
    def __init__(self, reject=()):
        self.reject, self.calls = set(reject), []

    def create(self, **kw):
        self.calls.append(kw)
        for p in self.reject:
            if p in kw:
                raise _bad_request(f"Unsupported parameter: '{p}' is not supported with this model.")
        return _resp()


def make(monkeypatch, env, reject=()):
    for k in ("LLM_MODEL", "LLM_TEMPERATURE", "LLM_REASONING_EFFORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    llm = OpenAIChat(api_key="test")
    fake = FakeCompletions(reject)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return llm, fake


def test_default_model_and_no_optional_params(monkeypatch):
    llm, fake = make(monkeypatch, {})
    llm.chat([], [])
    assert llm.model == "gpt-5.4-mini"
    assert "temperature" not in fake.calls[0] and "reasoning_effort" not in fake.calls[0]


def test_rejected_param_is_dropped_once(monkeypatch):
    llm, fake = make(monkeypatch, {"LLM_TEMPERATURE": "0", "LLM_REASONING_EFFORT": "low"}, reject=["temperature"])
    llm.chat([], []); llm.chat([], [])
    assert len(llm.dropped_params) == 1 and llm.dropped_params[0].startswith("temperature: ")
    assert len(fake.calls) == 3  # 第 1 次被拒 + 重试 + 第 2 次直接成功
    assert fake.calls[-1]["reasoning_effort"] == "low" and "temperature" not in fake.calls[-1]


def test_other_bad_request_is_raised(monkeypatch):
    llm, fake = make(monkeypatch, {"LLM_TEMPERATURE": "0"}, reject=["tools"])
    fake.reject = set()
    fake.create = lambda **kw: (_ for _ in ()).throw(_bad_request("invalid api key"))
    with pytest.raises(BadRequestError):
        llm.chat([], [])
