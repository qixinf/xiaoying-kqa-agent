"""离线测试：工具、校验器、循环边界。全部用 MockLLM，不需要 API Key。"""
from pathlib import Path

from kqa.agent import run_agent
from kqa.corpus import Corpus
from kqa.llm import LLMTurn, MockLLM, call
from kqa.mock_scripts import MOCK_SCRIPTS
from kqa.tools import ToolBox
from kqa.validator import validate_finish

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus(ROOT / "data/docs.json")


# ---------- 工具层 ----------
def test_search_hits_expected_doc():
    hits = CORPUS.search("年假 天数")
    assert hits and hits[0]["doc_id"] == "D03"


def test_search_out_of_scope_returns_nothing():
    assert CORPUS.search("股票期权行权") == []


def test_fail_injection_then_recover():
    tb = ToolBox(CORPUS, {"search_docs": 1})
    assert tb.call("search_docs", {"query": "密码"})["ok"] is False
    assert tb.call("search_docs", {"query": "密码"})["ok"] is True


def test_read_unknown_doc():
    assert ToolBox(CORPUS).call("read_doc", {"doc_id": "D99"})["ok"] is False


# ---------- 校验器 ----------
def test_validator_rejects_fabricated_quote():
    errs = validate_finish({"status": "answered", "citations": [{"doc_id": "D01", "quote": "报销期限 15 天"}]},
                           CORPUS, {"D01"})
    assert any("逐字" in e for e in errs)


def test_validator_rejects_unread_doc():
    errs = validate_finish({"status": "answered",
                            "citations": [{"doc_id": "D03", "quote": "年假需提前 3 个工作日在 HR 系统提交申请。"}]},
                           CORPUS, set())
    assert any("未用 read_doc" in e for e in errs)


def test_validator_conflict_needs_two_docs():
    errs = validate_finish({"status": "conflict",
                            "citations": [{"doc_id": "D01", "quote": "员工应在费用发生之日起 30 天内提交报销申请。"}]},
                           CORPUS, {"D01"})
    assert any("至少 2 份" in e for e in errs)


# ---------- 循环 ----------
def test_normal_path():
    r = run_agent("年假几天", MockLLM(MOCK_SCRIPTS["normal"]), CORPUS)
    assert r.status == "answered" and r.citations[0]["doc_id"] == "D03"
    assert [t["action"] for t in r.trace] == ["search_docs", "read_doc", "finish"]


def test_conflict_path():
    r = run_agent("报销期限多久", MockLLM(MOCK_SCRIPTS["conflict"]), CORPUS)
    assert r.status == "conflict" and {c["doc_id"] for c in r.citations} == {"D01", "D02"}


def test_tool_failure_is_observed_and_retried():
    r = run_agent("忘记密码", MockLLM(MOCK_SCRIPTS["tool_retry"]), CORPUS, ToolBox(CORPUS, {"search_docs": 1}))
    assert r.trace[0]["result"].startswith("ERROR")
    assert r.status == "answered"


def test_bad_finish_is_bounced_back_to_model():
    script = [
        LLMTurn(None, [call("read_doc", doc_id="D01", reason="r")]),
        LLMTurn(None, [call("finish", 0, status="answered", answer="15 天",
                            citations=[{"doc_id": "D01", "quote": "15 天内提交"}], gaps=[], reason="r")]),
        LLMTurn(None, [call("finish", 1, status="answered", answer="30 天",
                            citations=[{"doc_id": "D01", "quote": "员工应在费用发生之日起 30 天内提交报销申请。"}],
                            gaps=[], reason="r")]),
    ]
    r = run_agent("报销期限", MockLLM(script), CORPUS)
    assert r.trace[1]["result"].startswith("ERROR")  # 第一次 finish 被打回
    assert r.status == "answered" and r.answer == "30 天"


def test_step_limit_returns_incomplete():
    script = [LLMTurn(None, [call("search_docs", i, query="报销", reason="r")]) for i in range(5)]
    r = run_agent("报销期限", MockLLM(script), CORPUS, max_steps=2)
    assert r.status == "incomplete" and r.steps_used == 2
