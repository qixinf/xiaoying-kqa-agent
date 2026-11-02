"""命令行入口。

python -m kqa.cli "报销期限多久？"                     # 真实模型
python -m kqa.cli "报销期限多久？" --mock conflict       # 离线 Mock（仅验证流程）
python -m kqa.cli "忘记密码怎么办" --fail search_docs:1  # 注入工具失败
python -m kqa.cli --chat                                 # 多轮对话（上下文追问）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .agent import run_agent
from .corpus import Corpus
from .tools import ToolBox

ROOT = Path(__file__).resolve().parents[2]


def parse_fail(specs: list[str]) -> dict[str, int]:
    plan = {}
    for s in specs or []:
        name, _, n = s.partition(":")
        plan[name] = int(n or 1)
    return plan


def build_llm(mock: str | None):
    if mock:
        from .llm import MockLLM
        from .mock_scripts import MOCK_SCRIPTS
        return MockLLM(MOCK_SCRIPTS[mock])
    from .llm import OpenAIChat
    return OpenAIChat()


def main() -> None:
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="企业知识问答 Agent")
    p.add_argument("question", nargs="?")
    p.add_argument("--data", default=str(ROOT / "data/docs.json"))
    p.add_argument("--history", help="JSON 文件：[{role, content}, ...]")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--max-retries", type=int, default=1, help="每个工具连续服务失败后允许的重试次数")
    p.add_argument("--fail", action="append", help="注入失败，如 search_docs:1 或 read_doc:-1")
    p.add_argument("--mock", help="离线 Mock 脚本名：normal / conflict / tool_retry")
    p.add_argument("--trace-out", help="把完整结果和 Trace 写到该 JSON 文件")
    p.add_argument("--chat", action="store_true", help="多轮对话模式")
    a = p.parse_args()

    corpus = Corpus(a.data)
    history = json.loads(Path(a.history).read_text(encoding="utf-8")) if a.history else []

    if a.chat:
        llm = build_llm(a.mock)
        while True:
            q = input("\n你：").strip()
            if q in {"", "exit", "quit"}:
                break
            r = run_agent(q, llm, corpus, ToolBox(corpus, parse_fail(a.fail), a.max_retries), history, a.max_steps)
            print(f"[{r.status}] {r.answer}")
            for c in r.citations:
                print(f"  - {c['doc_id']}: {c['quote']}")
            # 只把最终问答放进历史，不放中间工具消息，控制上下文长度
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": r.answer}]
        return

    if not a.question:
        p.error("需要提供问题，或使用 --chat")
    r = run_agent(a.question, build_llm(a.mock), corpus, ToolBox(corpus, parse_fail(a.fail), a.max_retries), history, a.max_steps)
    out = {"question": a.question, "mode": f"mock:{a.mock}" if a.mock else "real_llm", **r.to_dict()}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if a.trace_out:
        Path(a.trace_out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
