"""业务工具：search_docs / read_doc（+ 失败注入），以及给模型的 JSON Schema。

finish 也作为一个"工具"暴露给模型：模型用它结束任务，由 validator 做代码级校验。
"""
from __future__ import annotations

from .corpus import Corpus

REASON = {"type": "string", "description": "一句话说明为什么调用这个工具（写入 Trace 作为决策依据）"}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_docs",
        "description": "在制度/FAQ 文档库中做关键词检索，返回最相关的段落片段（含文档 ID、版本日期）。结果为空或不相关时可换同义词再检索。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索词，建议用制度里的书面说法"},
            "reason": REASON}, "required": ["query", "reason"]}}},
    {"type": "function", "function": {
        "name": "read_doc",
        "description": "按文档 ID 读取完整原文（含适用范围、版本日期和全部段落）。引用前必须先读原文。",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string"}, "reason": REASON}, "required": ["doc_id", "reason"]}}},
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


class ToolError(Exception):
    pass


class ToolBox:
    """fail_plan 用于注入工具失败：{"search_docs": 1} 表示前 1 次调用失败，-1 表示一直失败。"""

    def __init__(self, corpus: Corpus, fail_plan: dict[str, int] | None = None):
        self.corpus = corpus
        self.fail_plan = dict(fail_plan or {})
        self.calls: dict[str, int] = {}

    def _maybe_fail(self, name: str) -> None:
        n = self.calls.get(name, 0)
        self.calls[name] = n + 1
        budget = self.fail_plan.get(name, 0)
        if budget == -1 or n < budget:
            raise ToolError(f"{name} 服务暂时不可用（注入的失败，第 {n + 1} 次调用）")

    def call(self, name: str, args: dict) -> dict:
        """返回统一结构：{"ok": bool, "data"| "error": ...}。工具异常不会抛到 agent 循环外。"""
        try:
            if name == "search_docs":
                self._maybe_fail(name)
                hits = self.corpus.search(str(args.get("query", "")))
                return {"ok": True, "data": {"hits": hits, "count": len(hits)}}
            if name == "read_doc":
                self._maybe_fail(name)
                doc = self.corpus.read(str(args.get("doc_id", "")))
                if doc is None:
                    return {"ok": False, "error": f"文档 {args.get('doc_id')} 不存在"}
                return {"ok": True, "data": doc}
            return {"ok": False, "error": f"未知工具 {name}"}
        except ToolError as e:
            return {"ok": False, "error": str(e)}
