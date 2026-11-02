"""离线 Mock 脚本：只演示流程能跑通，不代表真实模型的决策效果。"""
from .llm import LLMTurn, call

MOCK_SCRIPTS = {
    # 正常路径：检索 → 读原文 → 回答
    "normal": [
        LLMTurn(None, [call("search_docs", query="年假 天数", reason="先检索年假制度")]),
        LLMTurn(None, [call("read_doc", doc_id="D03", reason="命中 D03，读原文以便逐字引用")]),
        LLMTurn(None, [call("finish", status="answered", answer="入职满 1 年不满 10 年，每年带薪年假 5 天。",
                            citations=[{"doc_id": "D03", "quote": "入职满 1 年不满 10 年的员工，每年享有带薪年假 5 天。"}],
                            gaps=[], reason="D03 原文直接回答了问题")]),
    ],
    # 冲突路径：两份文档说法不同 → 都读 → 返回 conflict
    "conflict": [
        LLMTurn(None, [call("search_docs", query="报销 期限", reason="检索报销期限")]),
        LLMTurn(None, [call("read_doc", 0, doc_id="D01", reason="D01 写 30 天，读原文核对范围"),
                       call("read_doc", 1, doc_id="D02", reason="D02 写 15 天，读原文核对范围")]),
        LLMTurn(None, [call("finish", status="conflict",
                            answer="两份制度说法不一致：D01 为 30 天，D02 为 15 天，且原文未说明适用范围与优先级。请确认适用哪份制度。",
                            citations=[{"doc_id": "D01", "quote": "员工应在费用发生之日起 30 天内提交报销申请。"},
                                       {"doc_id": "D02", "quote": "费用报销申请须在费用发生后 15 天内提交"}],
                            gaps=["D01 与 D02 的适用范围和优先级"], reason="原文无法判定哪份有效")]),
    ],
    # 工具失败路径：检索失败 → 重试 → 成功
    "tool_retry": [
        LLMTurn(None, [call("search_docs", 0, query="忘记密码", reason="检索密码重置")]),
        LLMTurn(None, [call("search_docs", 1, query="忘记密码 重置", reason="上次报错，重试一次")]),
        LLMTurn(None, [call("read_doc", doc_id="D08", reason="命中 D08，读原文")]),
        LLMTurn(None, [call("finish", status="answered", answer="可在 IT 自助门户通过手机验证码重置密码。",
                            citations=[{"doc_id": "D08", "quote": "忘记密码可在 IT 自助门户通过手机验证码重置。"}],
                            gaps=[], reason="D08 原文支持")]),
    ],
}
