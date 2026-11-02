"""业务工具层：search_docs / read_doc，给模型的 JSON Schema，以及失败注入。

设计约定（工具契约）：
1. 所有工具都返回统一结构，绝不把异常抛到 agent 循环外：
     成功  {"ok": True,  "data": {...}}
     失败  {"ok": False, "error_type": ..., "retryable": bool, "error": "给模型看的说明"}
2. error_type 只有四种：
     unavailable  服务不可用（含注入的失败）  retryable=True   → 模型可重试；连续失败超过 max_retries 后变为 False
     not_found    文档 ID 不存在             retryable=False  → 模型应换 ID 或重新检索
     bad_args     参数缺失或非法             retryable=False  → 模型应修正参数
     internal     未预料的内部错误           retryable=False  → 模型应停止并报告
   retryable 让"要不要重试"成为基于观察结果的决策，而不是代码里写死的自动重试。
3. finish 也作为工具暴露给模型，但不在 ToolBox 里执行：由 agent 循环调用 validator 校验。
"""
from __future__ import annotations

import re

from .corpus import Corpus

DEFAULT_TOP_K = 4
DEFAULT_MAX_RETRIES = 1  # 每个工具连续 unavailable 后允许的重试次数（理由见 docs/02-solution.md）
MAX_TOP_K = 8
MAX_QUERY_LEN = 100

REASON = {"type": "string", "description": "一句话说明为什么调用这个工具（写入 Trace 作为决策依据）"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_docs",
        "description": (
            "在公司制度/产品 FAQ 文档库中做关键词检索（BM25，按段落），返回最相关的片段，"
            "含文档 ID、段落号、标题、版本日期和适用范围。注意：只做字面匹配，口语或同义词可能查不到；"
            "结果为空或片段并未回答问题时，可改用制度里的书面说法再检索。"),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索词，建议用制度书面用语，如“年假 天数”而非“能歇几天”"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_TOP_K,
                      "description": f"返回片段数，默认 {DEFAULT_TOP_K}"},
            "reason": REASON}, "required": ["query", "reason"]}}},
    {"type": "function", "function": {
        "name": "read_doc",
        "description": "按文档 ID 读取完整原文（含适用范围、版本日期和全部段落）。引用前必须先读原文；"
                       "多份文档说法不一致时，用它核对适用范围、版本日期和是否写明替代关系。",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string", "description": "如 D03"},
            "reason": REASON}, "required": ["doc_id", "reason"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "提交最终结果并结束任务。引用的 quote 必须逐字摘自 read_doc 返回的原文。",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["answered", "conflict", "no_info", "need_clarification", "tool_failed"]},
            "answer": {"type": "string", "description": "给员工的回答；conflict 时列出各文档说法并请求确认"},
            "citations": {"type": "array", "items": {"type": "object", "properties": {
                "doc_id": {"type": "string"}, "quote": {"type": "string"}}, "required": ["doc_id", "quote"]}},
            "gaps": {"type": "array", "items": {"type": "string"}, "description": "资料缺口或待确认事项"},
            "clarifying_question": {"type": "string"},
            "reason": REASON},
            "required": ["status", "answer", "citations", "gaps", "reason"]}}},
]

BUSINESS_TOOLS = {"search_docs", "read_doc"}


class ToolError(Exception):
    def __init__(self, error_type: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.error_type, self.retryable = error_type, retryable


def _err(e: ToolError) -> dict:
    return {"ok": False, "error_type": e.error_type, "retryable": e.retryable, "error": str(e)}


def normalize_doc_id(raw) -> str:
    """容忍模型常见写法：' d3 '、'D3'、'D03' → 'D03'。"""
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"D0*(\d+)", s)
    return f"D{int(m.group(1)):02d}" if m else s


class ToolBox:
    """执行业务工具。

    fail_plan 用于注入失败，验证模型能否观察并调整：
        {"search_docs": 1}   前 1 次调用失败，之后恢复
        {"search_docs": -1}  一直失败

    max_retries 由代码强制：同一工具连续 unavailable 的次数超过 max_retries 后，
    返回的错误 retryable=False，并提示停止。是否停止仍由模型决定，但代码不再给出"可重试"信号。
    任何一次成功调用都会把该工具的连续失败计数清零。
    """

    def __init__(self, corpus: Corpus, fail_plan: dict[str, int] | None = None,
                 max_retries: int = DEFAULT_MAX_RETRIES):
        self.corpus = corpus
        self.fail_plan = dict(fail_plan or {})
        self.max_retries = max(0, int(max_retries))
        self.calls: dict[str, int] = {}
        self.consecutive_failures: dict[str, int] = {}

    # ---- 失败注入 ----
    def _maybe_fail(self, name: str) -> None:
        n = self.calls.get(name, 0)
        self.calls[name] = n + 1
        budget = self.fail_plan.get(name, 0)
        if budget == -1 or n < budget:
            raise ToolError("unavailable", f"{name} 服务暂时不可用（注入失败，第 {n + 1} 次调用）", retryable=True)

    def _apply_retry_budget(self, name: str, result: dict) -> dict:
        """按工具统计连续 unavailable；超过上限后把 retryable 改为 False。"""
        if result.get("ok"):
            self.consecutive_failures[name] = 0
            return result
        if result.get("error_type") != "unavailable":
            return result  # 参数错误、not_found 不计入，也不清零
        n = self.consecutive_failures.get(name, 0) + 1
        self.consecutive_failures[name] = n
        retries_used = n - 1
        if retries_used >= self.max_retries:
            result["retryable"] = False
            result["error"] += (f"。已连续失败 {n} 次（重试上限 {self.max_retries} 次已用完），"
                                "请停止调用该工具，以 status=tool_failed 如实报告。")
        else:
            result["error"] += f"。可重试，剩余重试次数 {self.max_retries - retries_used}。"
        return result

    # ---- 具体工具 ----
    def search_docs(self, query, top_k=DEFAULT_TOP_K) -> dict:
        query = str(query or "").strip()
        if not query:
            raise ToolError("bad_args", "query 不能为空")
        if len(query) > MAX_QUERY_LEN:
            raise ToolError("bad_args", f"query 过长（>{MAX_QUERY_LEN} 字），请提炼关键词")
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            raise ToolError("bad_args", "top_k 必须是整数")
        top_k = max(1, min(top_k, MAX_TOP_K))
        self._maybe_fail("search_docs")
        hits = self.corpus.search(query, top_k=top_k)
        for h in hits:
            h["scope"] = self.corpus.docs[h["doc_id"]].scope
        data = {"query": query, "count": len(hits), "hits": hits}
        if not hits:
            data["hint"] = "无字面匹配。可换成制度书面用语或同义词再检索；若多次改写仍无结果，可判定资料不足。"
        return data

    def read_doc(self, doc_id) -> dict:
        did = normalize_doc_id(doc_id)
        if not did:
            raise ToolError("bad_args", "doc_id 不能为空")
        self._maybe_fail("read_doc")
        doc = self.corpus.read(did)
        if doc is None:
            raise ToolError("not_found", f"文档 {did} 不存在。可用 search_docs 确认文档 ID。")
        return doc

    # ---- 统一入口 ----
    def call(self, name: str, args: dict) -> dict:
        result = self._dispatch(name, args)
        if name in BUSINESS_TOOLS:
            result = self._apply_retry_budget(name, result)
        return result

    def _dispatch(self, name: str, args: dict) -> dict:
        args = args if isinstance(args, dict) else {}
        try:
            if name == "search_docs":
                return {"ok": True, "data": self.search_docs(args.get("query"), args.get("top_k", DEFAULT_TOP_K))}
            if name == "read_doc":
                return {"ok": True, "data": self.read_doc(args.get("doc_id"))}
            raise ToolError("bad_args", f"未知工具 {name}，可用工具：search_docs、read_doc、finish")
        except ToolError as e:
            return _err(e)
        except Exception as e:  # 兜底：任何实现缺陷都转成观察结果，不让循环崩溃
            return _err(ToolError("internal", f"{name} 内部错误：{type(e).__name__}: {e}"))
