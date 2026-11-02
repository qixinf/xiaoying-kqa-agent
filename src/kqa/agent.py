"""ReAct 循环：模型选择动作 → 执行工具 → 观察结果 → 模型决定下一步或 finish。

分工：模型决定"下一步做什么"；代码保证"不越界、不撒谎"。代码层的边界：
  - 步数上限 max_steps（一次模型调用 = 一步）与工具调用上限 max_tool_calls
  - finish 必须单独调用，并通过 validator 校验，否则打回给模型修正
  - 相同参数的成功调用不重复执行，防止原地打转
  - 模型 API 异常、参数非 JSON、未知工具都转成明确状态或观察结果，循环不崩溃
  - 每一步写入 Trace：步骤、动作、参数、返回摘要、决策理由、后续动作
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .corpus import Corpus
from .tools import TOOL_SCHEMAS, ToolBox
from .validator import validate_finish

DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_TOOL_CALLS = 12

SYSTEM_PROMPT = """你是公司内部的知识问答助手，只能依据工具返回的文档回答员工的制度或产品问题。

工作原则：
1. 先用 search_docs 检索。结果为空或片段并未回答问题时，换成制度里的书面说法或同义词再检索（最多改写 2 次）。
2. 准备引用的文档，必须先用 read_doc 读原文；引用 quote 必须逐字摘自原文。
3. 多份文档对同一问题说法不同时：分别读原文，检查适用范围、版本日期、是否写明"替代/废止"。
   - 原文能判定哪份有效 → status=answered，引用有效文档，并在 answer 中说明判定依据。
   - 原文无法判定 → status=conflict，列出各文档说法与引用，请用户确认适用哪份制度。绝不自行挑一个。
4. 检索不到依据，或检索到的文档并不包含答案 → status=no_info，在 gaps 说明缺什么，不得凭常识回答。
5. 问题指代不清、且对话历史也无法补全 → status=need_clarification 并给出追问。
6. 工具报错时看 retryable：为 true 可以重试；为 false 应修正参数或换文档 ID，不要原样重试。
   仍无法完成 → status=tool_failed，说明未能完成的原因，不得凭常识作答。
7. answer 中出现的数字必须能在引用的原文中找到。
8. 每次调用工具都在 reason 里写一句理由。finish 必须在看到工具结果之后单独调用，不要直接用文字回复。"""


@dataclass
class AgentResult:
    status: str  # answered / conflict / no_info / need_clarification / tool_failed / incomplete
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    stop_reason: str = ""  # finished / max_steps / max_tool_calls / llm_error
    steps_used: int = 0
    tool_calls_used: int = 0
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _summarize(obs: dict) -> str:
    """Trace 里只放返回摘要，不记录整篇原文。"""
    if not obs.get("ok"):
        tag = f"[{obs['error_type']}{', 可重试' if obs.get('retryable') else ''}] " if "error_type" in obs else ""
        details = "；".join(obs.get("details", []))
        return f"ERROR: {tag}{obs.get('error')}" + (f"：{details}" if details else "")
    data = obs["data"]
    if "hits" in data:
        if not data["hits"]:
            return f"0 条命中（query={data['query']}）"
        return f"{data['count']} 条命中: " + "; ".join(f"{h['doc_id']}#{h['para']} {h['snippet'][:20]}" for h in data["hits"])
    if "paragraphs" in data:
        return f"读取 {data['doc_id']}《{data['title']}》v{data['version_date']} 范围:{data['scope']} 共 {len(data['paragraphs'])} 段"
    return json.dumps(data, ensure_ascii=False)[:120]


def _fill_next_actions(trace: list[dict]) -> list[dict]:
    """给每条 Trace 补上"后续动作"，便于评审逐条对照模型如何根据观察调整。"""
    for i, t in enumerate(trace):
        if "next_action" not in t:
            t["next_action"] = trace[i + 1]["action"] if i + 1 < len(trace) else "结束"
    return trace


def _call_key(name: str, args: dict) -> str:
    return name + json.dumps(args, ensure_ascii=False, sort_keys=True)


def run_agent(question: str, llm, corpus: Corpus, toolbox: ToolBox | None = None,
              history: list[dict] | None = None, max_steps: int = DEFAULT_MAX_STEPS,
              max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS) -> AgentResult:
    toolbox = toolbox or ToolBox(corpus)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *(history or []),
                {"role": "user", "content": question}]
    trace: list[dict] = []
    read_doc_ids: set[str] = set()
    done_calls: dict[str, int] = {}  # 成功调用的 key → 首次出现的步骤
    tool_calls_used = 0
    evidence = {"searches_ok": 0, "unavailable": 0}  # 供 validator 核对 no_info / tool_failed 是否有依据

    def stop(status: str, stop_reason: str, answer: str, step: int, **kw) -> AgentResult:
        return AgentResult(status=status, answer=answer, stop_reason=stop_reason, steps_used=step,
                           tool_calls_used=tool_calls_used, trace=_fill_next_actions(trace), **kw)

    for step in range(1, max_steps + 1):
        # ① 模型选择动作
        try:
            turn = llm.chat(messages, TOOL_SCHEMAS)
        except Exception as e:  # 模型不可用：如实返回未完成，不伪造答案
            trace.append({"step": step, "action": "llm_error", "result": f"{type(e).__name__}: {str(e)[:120]}"})
            return stop("incomplete", "llm_error", "模型调用失败，任务未完成，未给出结论。", step,
                        gaps=["模型服务不可用"])

        if not turn.tool_calls:  # 直接文字回复不算完成：提醒模型走 finish
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append({"role": "user", "content": "请调用 finish 工具提交结果（含 status 与引用），不要直接文字回复。"})
            trace.append({"step": step, "action": "text_reply", "result": (turn.content or "")[:80],
                          "next_action": "代码提醒模型使用 finish"})
            continue

        messages.append(turn.to_message())
        finish_mixed = len(turn.tool_calls) > 1 and any(tc.name == "finish" for tc in turn.tool_calls)

        for tc in turn.tool_calls:
            if tool_calls_used >= max_tool_calls:
                return stop("incomplete", "max_tool_calls",
                            f"已达到工具调用上限 {max_tool_calls} 次，任务未完成，未给出结论。", step,
                            gaps=["工具调用次数上限内未能完成检索与核验"])
            tool_calls_used += 1
            args = dict(tc.args) if isinstance(tc.args, dict) else {}
            reason = str(args.pop("reason", ""))
            entry = {"step": step, "action": tc.name, "args": args, "reason": reason}

            # ② 执行（或由代码拦截）
            if "__parse_error__" in args:
                obs = {"ok": False, "error_type": "bad_args", "retryable": False, "error": "工具参数不是合法 JSON"}
            elif tc.name == "finish" and finish_mixed:
                obs = {"ok": False, "error_type": "bad_args", "retryable": False,
                       "error": "finish 不能与其他工具在同一轮调用：请先看到工具结果，再单独调用 finish"}
            elif tc.name == "finish":
                errors = validate_finish(args, corpus, read_doc_ids, question=question, evidence=evidence)
                if not errors:
                    entry["result"] = "校验通过，任务结束"
                    trace.append(entry)
                    return stop(args["status"], "finished", args.get("answer", ""), step,
                                citations=args.get("citations", []), gaps=args.get("gaps", []),
                                clarifying_question=args.get("clarifying_question"))
                obs = {"ok": False, "error_type": "validation", "retryable": False,
                       "error": "finish 校验未通过，请修正后重新提交", "details": errors}
            elif (key := _call_key(tc.name, args)) in done_calls:
                obs = {"ok": False, "error_type": "duplicate", "retryable": False,
                       "error": f"与第 {done_calls[key]} 步的调用完全相同，结果不会变化。请换参数、读其他文档或提交 finish"}
            else:
                obs = toolbox.call(tc.name, args)
                if obs.get("error_type") == "unavailable":
                    evidence["unavailable"] += 1
                if obs.get("ok") and tc.name == "search_docs":
                    evidence["searches_ok"] += 1
                if obs.get("ok"):
                    done_calls[key] = step
                    if tc.name == "read_doc":
                        read_doc_ids.add(obs["data"]["doc_id"])

            # ③ 观察结果回给模型，并记入 Trace
            entry["result"] = _summarize(obs)
            trace.append(entry)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(obs, ensure_ascii=False)})
        # ④ 下一轮由模型根据观察决定继续还是结束

    return stop("incomplete", "max_steps", f"已达到最大步数 {max_steps}，任务未完成，未给出结论。", max_steps,
                gaps=["步数上限内未能完成检索与核验"])
