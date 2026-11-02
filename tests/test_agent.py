"""Agent 循环与校验器的边界测试（MockLLM，离线）。"""
from pathlib import Path

import pytest

from kqa.agent import run_agent
from kqa.corpus import Corpus
from kqa.llm import LLMTurn, MockLLM, call
from kqa.tools import ToolBox
from kqa.validator import validate_finish

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus(ROOT / "data/docs.json")
Q_D01 = "员工应在费用发生之日起 30 天内提交报销申请。"
Q_D02 = "费用报销申请须在费用发生后 15 天内提交"


def T(*calls, content=None):
    return LLMTurn(content, list(calls))


def finish(i=0, **kw):
    kw.setdefault("gaps", []); kw.setdefault("reason", "r")
    return call("finish", i, **kw)


# ======================= 校验器 =======================
class TestValidator:
    def v(self, args, read=("D01", "D02", "D03", "D06"), question=""):
        return validate_finish(args, CORPUS, set(read), question=question)

    def test_valid_answer(self):
        assert self.v({"status": "answered", "answer": "30 天内提交。", "citations": [{"doc_id": "D01", "quote": Q_D01}]}) == []

    def test_number_mismatch_rejected(self):
        # 关键缺陷：引用 D01（30 天）却回答 15 天
        errs = self.v({"status": "answered", "answer": "报销期限 15 天。", "citations": [{"doc_id": "D01", "quote": Q_D01}]})
        assert any("['15']" in e for e in errs)

    def test_number_from_question_allowed(self):
        assert self.v({"status": "answered", "answer": "入职 3 年属于满 1 年不满 10 年，年假 5 天。",
                       "citations": [{"doc_id": "D03", "quote": "入职满 1 年不满 10 年的员工，每年享有带薪年假 5 天。"}]},
                      question="入职 3 年的员工年假有几天？") == []

    def test_doc_ids_and_version_dates_not_counted_as_claims(self):
        assert self.v({"status": "answered", "answer": "依据D06（2026-05-01 生效，替代D05），每周 2 天。",
                       "citations": [{"doc_id": "D06", "quote": "员工每周可远程办公 2 天"}]}) == []

    def test_conflict_needs_two_docs(self):
        errs = self.v({"status": "conflict", "answer": "有冲突", "citations": [{"doc_id": "D01", "quote": Q_D01}]})
        assert any("至少 2 份" in e for e in errs)

    @pytest.mark.parametrize("bad", ["D01", ["D01"], [{"doc_id": "D01", "quote": Q_D01}, "x"]])
    def test_malformed_citations(self, bad):
        assert any("列表" in e for e in self.v({"status": "answered", "answer": "a", "citations": bad}))

    def test_empty_answer(self):
        assert any("answer" in e for e in self.v({"status": "no_info", "answer": " ", "citations": []}))

    def test_no_info_with_citation_rejected(self):
        assert self.v({"status": "no_info", "answer": "无", "citations": [{"doc_id": "D01", "quote": Q_D01}]})

    def test_clarification_needs_question(self):
        assert self.v({"status": "need_clarification", "answer": "请说明", "citations": []})
        assert self.v({"status": "need_clarification", "answer": "请说明", "citations": [],
                       "clarifying_question": "您指哪项标准？"}) == []


# ======================= 循环 =======================
def test_finish_mixed_with_other_calls_is_rejected():
    script = [
        T(call("read_doc", doc_id="D01", reason="r"),
          finish(status="answered", answer="30 天", citations=[{"doc_id": "D01", "quote": Q_D01}])),
        T(finish(1, status="answered", answer="30 天", citations=[{"doc_id": "D01", "quote": Q_D01}])),
    ]
    r = run_agent("报销期限", MockLLM(script), CORPUS)
    assert "同一轮" in r.trace[1]["result"]
    assert r.status == "answered" and r.steps_used == 2


def test_duplicate_successful_call_not_reexecuted():
    script = [T(call("search_docs", 0, query="年假", reason="r")),
              T(call("search_docs", 1, query="年假", reason="r")),
              T(call("search_docs", 2, query="年假 天数", reason="换个说法"))]
    r = run_agent("年假", MockLLM(script), CORPUS, max_steps=3)
    assert r.trace[1]["result"].startswith("ERROR: [duplicate]")
    assert "命中" in r.trace[2]["result"]


def test_failed_call_can_be_retried_with_same_args():
    script = [T(call("search_docs", 0, query="密码", reason="r")),
              T(call("search_docs", 1, query="密码", reason="重试"))]
    r = run_agent("密码", MockLLM(script), CORPUS, ToolBox(CORPUS, {"search_docs": 1}), max_steps=2)
    assert "duplicate" not in r.trace[1]["result"] and "命中" in r.trace[1]["result"]


def test_max_tool_calls():
    many = T(*[call("read_doc", i, doc_id=f"D0{i}", reason="r") for i in range(1, 6)])
    r = run_agent("q", MockLLM([many]), CORPUS, max_tool_calls=3)
    assert r.status == "incomplete" and r.stop_reason == "max_tool_calls" and r.tool_calls_used == 3


def test_max_steps_and_stop_reason():
    script = [T(call("search_docs", i, query=f"报销 {i}", reason="r")) for i in range(5)]
    r = run_agent("q", MockLLM(script), CORPUS, max_steps=2)
    assert (r.status, r.stop_reason, r.steps_used) == ("incomplete", "max_steps", 2)


def test_llm_exception_returns_incomplete():
    class Boom:
        def chat(self, *a):
            raise TimeoutError("api timeout")
    r = run_agent("q", Boom(), CORPUS)
    assert (r.status, r.stop_reason) == ("incomplete", "llm_error") and r.answer and not r.citations


def test_text_reply_is_nudged_to_finish():
    script = [T(content="报销期限是 30 天"),
              T(call("read_doc", doc_id="D01", reason="r")),
              T(finish(status="answered", answer="30 天", citations=[{"doc_id": "D01", "quote": Q_D01}]))]
    r = run_agent("q", MockLLM(script), CORPUS)
    assert r.trace[0]["action"] == "text_reply" and r.status == "answered"


def test_bad_json_args():
    script = [T(call("search_docs", __parse_error__="{bad"))]
    r = run_agent("q", MockLLM(script), CORPUS, max_steps=1)
    assert "合法 JSON" in r.trace[0]["result"]


def test_trace_has_required_fields():
    script = [T(call("search_docs", query="报销 期限", reason="检索")),
              T(call("read_doc", 0, doc_id="D01", reason="a"), call("read_doc", 1, doc_id="D02", reason="b")),
              T(finish(status="conflict", answer="D01 为 30 天，D02 为 15 天，请确认。",
                       citations=[{"doc_id": "D01", "quote": Q_D01}, {"doc_id": "D02", "quote": Q_D02}]))]
    r = run_agent("报销期限多久", MockLLM(script), CORPUS)
    assert r.status == "conflict"
    for t in r.trace:
        assert {"step", "action", "args", "reason", "result", "next_action"} <= set(t)
    assert [t["next_action"] for t in r.trace] == ["read_doc", "read_doc", "finish", "结束"]


def test_reason_not_mutated_in_original_call():
    tc = call("search_docs", query="年假", reason="r")
    run_agent("q", MockLLM([T(tc)]), CORPUS, max_steps=1)
    assert tc.args["reason"] == "r"  # 不修改模型返回的原对象，消息历史保持原样


# ======================= 过程证据 =======================
def test_no_info_without_search_rejected():
    script = [T(finish(status="no_info", answer="资料中没有", citations=[])),
              T(call("search_docs", query="股票期权", reason="r")),
              T(finish(1, status="no_info", answer="资料中没有", citations=[]))]
    r = run_agent("期权怎么行权", MockLLM(script), CORPUS)
    assert "search_docs" in r.trace[0]["result"] and r.status == "no_info" and r.steps_used == 3


def test_tool_failed_without_failure_rejected():
    script = [T(call("search_docs", query="年假", reason="r")),
              T(finish(status="tool_failed", answer="工具失败", citations=[]))]
    r = run_agent("年假", MockLLM(script), CORPUS, max_steps=2)
    assert "没有观察到" in r.trace[1]["result"] and r.status == "incomplete"


def test_tool_failed_after_real_failures_accepted():
    script = [T(call("search_docs", 0, query="年假", reason="r")),
              T(call("search_docs", 1, query="年假", reason="重试")),
              T(finish(status="tool_failed", answer="检索服务不可用，未能完成。", citations=[]))]
    r = run_agent("年假", MockLLM(script), CORPUS, ToolBox(CORPUS, {"search_docs": -1}))
    assert r.status == "tool_failed"


def test_clarification_cannot_leak_unverified_numbers():
    # 真实运行中出现过：追问里直接写出"600 元 / 400 元"，但没有读原文、没有引用
    v = lambda a: validate_finish(a, CORPUS, {"D04"}, question="那个标准是多少？")
    leak = {"status": "need_clarification", "answer": "候选：差旅住宿标准，一线城市 600 元，其他城市 400 元。",
            "citations": [], "clarifying_question": "您是问差旅住宿标准吗？"}
    assert any("['400', '600']" in e for e in v(leak))
    ok = {**leak, "answer": "检索到候选《差旅住宿标准》，请确认。"}
    assert v(ok) == []
    cited = {**leak, "citations": [{"doc_id": "D04", "quote": "一线城市（北京、上海、广州、深圳）出差住宿标准为每晚 600 元。"},
                                   {"doc_id": "D04", "quote": "其他城市出差住宿标准为每晚 400 元。"}]}
    assert v(cited) == []
