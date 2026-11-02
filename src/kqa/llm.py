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
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        from openai import OpenAI
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.client = OpenAI(api_key=api_key or os.getenv("LLM_API_KEY"),
                             base_url=base_url or os.getenv("LLM_BASE_URL") or None)

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMTurn:
        r = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools,
                                                tool_choice="auto", temperature=0)
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
