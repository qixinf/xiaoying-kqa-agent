"""ReAct 循环：模型选动作 → 执行工具 → 观察 → 模型决定下一步或 finish。

边界全部在代码里：步数上限、finish 校验、工具异常兜底、Trace 记录。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .corpus import Corpus
from .tools import TOOL_SCHEMAS, ToolBox
from .validator import validate_finish

SYSTEM_PROMPT = """你是公司内部的知识问答助手，只能依据工具返回的文档回答员工的制度或产品问题。

工作原则：
1. 先用 search_docs 检索。结果为空或不相关时，换成制度里的书面说法或同义词再检索（最多改写 2 次）。
2. 准备引用的文档，必须先用 read_doc 读原文；引用 quote 必须逐字摘自原文。
3. 多份文档对同一问题说法不同时：分别读原文，检查适用范围、版本日期、是否写明"替代/废止"。
   - 原文能判定哪份有效 → status=answered，引用有效文档，并在 answer 中说明判定依据。
   - 原文无法判定 → status=conflict，列出各文档说法与引用，请用户确认适用哪份制度。绝不自行挑一个。
4. 检索不到依据 → status=no_info，在 gaps 说明缺什么，不得凭常识回答。
5. 问题指代不清、且对话历史也无法补全 → status=need_clarification 并给出追问。
6. 工具报错时看 retryable：为 true 可重试 1 次；为 false 应修正参数或换文档 ID，不要原样重试。
   仍无法完成 → status=tool_failed，说明未能完成的原因，不得凭常识作答。
7. 每次调用工具都在 reason 里写一句理由。必须调用 finish 结束任务，不要直接用文字回复。"""


@dataclass
class AgentResult:
    status: str  # answered / conflict / no_info / need_clarification / tool_failed / incomplete
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    steps_used: int = 0
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _summarize(obs: dict) -> str:
    """Trace 里只放返回摘要，避免记录整篇原文。"""
    if not obs.get("ok"):
        tag = f"[{obs['error_type']}{', 可重试' if obs.get('retryable') else ''}] " if "error_type" in obs else ""
        return f"ERROR: {tag}{obs.get('error')} {obs.get('details', '')}".strip()
    data = obs["data"]
    if "hits" in data:
        return f"{data['count']} 条命中: " + "; ".join(f"{h['doc_id']}#{h['para']} {h['snippet'][:20]}" for h in data["hits"])
    if "paragraphs" in data:
        return f"读取 {data['doc_id']}《{data['title']}》v{data['version_date']} 范围:{data['scope']} 共 {len(data['paragraphs'])} 段"
    return json.dumps(data, ensure_ascii=False)[:120]


def run_agent(question: str, llm, corpus: Corpus, toolbox: ToolBox | None = None,
              history: list[dict] | None = None, max_steps: int = 8) -> AgentResult:
    toolbox = toolbox or ToolBox(corpus)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *(history or []),
                {"role": "user", "content": question}]
    trace: list[dict] = []
    read_doc_ids: set[str] = set()

    for step in range(1, max_steps + 1):
        turn = llm.chat(messages, TOOL_SCHEMAS)

        if not turn.tool_calls:  # 模型没调工具就想直接回答：不接受，提醒它走 finish
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append({"role": "user", "content": "请调用 finish 工具提交结果（含 status 与引用），不要直接文字回复。"})
            trace.append({"step": step, "action": "text_reply", "note": (turn.content or "")[:80],
                          "next": "提醒模型使用 finish"})
            continue

        messages.append(turn.to_message())
        for tc in turn.tool_calls:
            reason = tc.args.pop("reason", "") if isinstance(tc.args, dict) else ""
            if "__parse_error__" in tc.args:
                obs = {"ok": False, "error": "工具参数不是合法 JSON"}
            elif tc.name == "finish":
                errors = validate_finish(tc.args, corpus, read_doc_ids)
                if not errors:
                    trace.append({"step": step, "action": "finish", "args": tc.args, "reason": reason,
                                  "result": "校验通过，任务结束"})
                    return AgentResult(status=tc.args["status"], answer=tc.args.get("answer", ""),
                                       citations=tc.args.get("citations", []), gaps=tc.args.get("gaps", []),
                                       clarifying_question=tc.args.get("clarifying_question"),
                                       steps_used=step, trace=trace)
                obs = {"ok": False, "error": "finish 校验未通过，请修正后重新提交", "details": errors}
            else:
                obs = toolbox.call(tc.name, tc.args)
                if tc.name == "read_doc" and obs.get("ok"):
                    read_doc_ids.add(obs["data"]["doc_id"])

            trace.append({"step": step, "action": tc.name, "args": tc.args, "reason": reason,
                          "result": _summarize(obs)})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(obs, ensure_ascii=False)})

    return AgentResult(status="incomplete", answer=f"已达到最大步数 {max_steps}，任务未完成，未给出结论。",
                       gaps=["步数上限内未能完成检索与核验"], steps_used=max_steps, trace=trace)
