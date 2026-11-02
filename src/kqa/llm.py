"""模型接入：OpenAI 兼容接口 + 离线 MockLLM。

MockLLM 只按脚本返回 tool call，用来验证流程和边界，不能证明真实模型的决策效果。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                "name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}} for c in self.tool_calls]
        return msg


class OpenAIChat:
    """OpenAI 兼容的 Chat Completions 接入。

    可选参数只在 .env 里填写时才发送，因为不同模型支持的参数不同：
      LLM_TEMPERATURE       旧模型（gpt-4o 系列、DeepSeek 等）可设 0 提高可复现性
      LLM_REASONING_EFFORT  GPT-5.x 推理模型可设 none/low/medium/high
    若服务端拒绝某个可选参数，自动去掉该参数重试一次，并在之后的调用中不再发送。
    """

    OPTIONAL_PARAMS = ("temperature", "reasoning_effort")

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        from openai import OpenAI
        self.model = model or os.getenv("LLM_MODEL") or "gpt-5.4-mini"
        self.client = OpenAI(api_key=api_key or os.getenv("LLM_API_KEY"),
                             base_url=base_url or os.getenv("LLM_BASE_URL") or None)
        self.extra: dict = {}
        if os.getenv("LLM_TEMPERATURE"):
            self.extra["temperature"] = float(os.environ["LLM_TEMPERATURE"])
        if os.getenv("LLM_REASONING_EFFORT"):
            self.extra["reasoning_effort"] = os.environ["LLM_REASONING_EFFORT"]
        self.dropped_params: list[str] = []

    def _create(self, messages, tools):
        from openai import BadRequestError
        try:
            return self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                       tool_choice="auto", **self.extra)
        except BadRequestError as e:
            bad = [p for p in self.OPTIONAL_PARAMS if p in self.extra and p in str(e)]
            if not bad:
                raise
            for p in bad:
                self.extra.pop(p)
                self.dropped_params.append(f"{p}: {str(e)[:200]}")  # 记录服务端给出的原因
            return self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                       tool_choice="auto", **self.extra)

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMTurn:
        r = self._create(messages, tools)
        m = r.choices[0].message
        calls = []
        for c in m.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__parse_error__": c.function.arguments}
            calls.append(ToolCall(c.id, c.function.name, args))
        return LLMTurn(m.content, calls)


class MockLLM:
    """按顺序返回预设的 LLMTurn。脚本用完后返回空回复（会触发循环的提醒/上限逻辑）。"""

    def __init__(self, script: list[LLMTurn]):
        self.script = list(script)
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMTurn:
        self.seen_messages.append(list(messages))
        return self.script.pop(0) if self.script else LLMTurn(content="(mock 脚本已用完)")


def call(name: str, i: int = 0, **args) -> ToolCall:
    """测试/Mock 脚本里构造 tool call 的小工具。"""
    return ToolCall(id=f"call_{name}_{i}", name=name, args=args)
