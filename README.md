# 企业知识问答 Agent（小影校招 AI Agent 笔试 · 第 3 题）

帮助员工查询制度和产品问题：Agent 自主检索合成文档库，给出**带原文引用**的回答；资料冲突时说明冲突并请求确认，资料中没有时如实说明，问题不清时追问。

- 原生 ReAct 循环：模型选择动作 → 执行工具 → 观察结果 → 模型决定下一步或结束
- 硬约束由代码保证：引用逐字校验、数字溯源、重试额度、步数和调用上限
- 真实模型（gpt-5.4-mini）用例 **11/11**，离线测试 **77 个**

## 评审入口

| 想看什么 | 去哪里 |
|---|---|
| 最终运行结果（11 个用例） | [`examples/results.md`](examples/results.md) |
| 每个用例的完整 Trace | [`examples/traces/`](examples/traces/) |
| 迭代过程（5 轮运行） | [`examples/runs/index.md`](examples/runs/index.md) |
| 需求、用例表、成功标准 | [`docs/01-requirements.md`](docs/01-requirements.md) |
| 调用流程、动态决策、技术取舍 | [`docs/02-solution.md`](docs/02-solution.md) |
| 测试预期与实际结果、失败分析、限制 | [`docs/03-evaluation.md`](docs/03-evaluation.md) |
| AI 辅助范围与个人贡献 | [`docs/04-ai-usage.md`](docs/04-ai-usage.md) |
| 复盘 | [`docs/05-retrospective.md`](docs/05-retrospective.md) |

## 环境与安装

需要 Python 3.10 或以上。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 填入 LLM_API_KEY；离线测试和 Mock 不需要
```

`.env` 中的变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 跑真实模型时必填 | OpenAI 或其他兼容服务的 Key |
| `LLM_BASE_URL` | 否 | 留空表示 OpenAI 官方；使用 DeepSeek 等服务时填对应地址 |
| `LLM_MODEL` | 否 | 默认 `gpt-5.4-mini` |
| `LLM_REASONING_EFFORT` / `LLM_TEMPERATURE` | 否 | 只在填写时发送；被服务端拒绝时自动去掉（原因见 `.env.example`） |

## 运行

```bash
# 1. 离线测试（无需 Key）
pytest -q                                                    # 77 passed

# 2. 离线 Mock 演示（无需 Key；只验证流程，不代表真实模型的决策效果）
cd src
python -m kqa.cli "报销期限多久？" --mock conflict
python -m kqa.cli "忘记密码怎么办？" --mock tool_retry --fail search_docs:1
cd ..

# 3. 真实模型：单题
cd src
python -m kqa.cli "入职 3 年的员工年假有几天？"
python -m kqa.cli "报销期限多久？"                                      # 冲突 → conflict
python -m kqa.cli "报销期限多久？" --data ../data/docs_no_conflict.json  # 替换文档 → answered
python -m kqa.cli "年假要提前几天申请？" --fail search_docs:-1           # 工具持续失败 → tool_failed
python -m kqa.cli "报销期限多久？" --max-steps 2                         # 步数上限 → incomplete
python -m kqa.cli --chat                                                 # 多轮对话，可测上下文追问
cd ..

# 4. 真实模型：批量跑全部用例
python scripts/run_cases.py --label my-run
python scripts/run_cases.py --only C11 --repeat 3 --label stability     # 部分用例，多次运行
```

命令行参数：`--fail 工具名:次数`（-1 表示一直失败）、`--max-steps`、`--max-retries`、`--data`、`--history 文件`、`--trace-out 文件`。

## 输出格式

```json
{
  "status": "conflict",
  "answer": "两份制度对报销期限规定不一致……请确认适用哪份制度。",
  "citations": [{"doc_id": "D01", "quote": "员工应在费用发生之日起 30 天内提交报销申请。"},
                {"doc_id": "D02", "quote": "费用报销申请须在费用发生后 15 天内提交，逾期不予受理。"}],
  "gaps": ["未找到原文明确的替代/废止关系"],
  "clarifying_question": "请确认适用哪份制度？",
  "stop_reason": "finished", "steps_used": 3, "tool_calls_used": 4,
  "trace": [{"step": 1, "action": "search_docs", "args": {"query": "报销期限"},
             "reason": "先检索报销期限的书面表述", "result": "4 条命中: …", "next_action": "read_doc"}]
}
```

`status` 的取值包括 `answered`、`conflict`、`no_info`、`need_clarification`、`tool_failed` 和 `incomplete`，含义见 [`docs/02-solution.md`](docs/02-solution.md)。

## 目录

```
src/kqa/          agent.py（循环）  tools.py（工具）  validator.py（校验）
                  corpus.py（BM25）  llm.py（模型接入/Mock）  cli.py
scripts/          run_cases.py（批量评测）
tests/            77 个离线测试
data/             docs.json（9 份合成文档）  docs_no_conflict.json（去掉 D02，用于 C09）
examples/         cases.json（11 个用例）  traces/  results.md  runs/（每次运行存档）
docs/             01–05
```

## 已知限制

- 所有真实运行都在默认推理强度下完成：gpt-5.4-mini 在 Chat Completions 接口上不支持"工具调用 + reasoning_effort"。
- 样本量小（11 个用例），C11 的执行路径在多次运行间不稳定（最终状态一致）。
- 数字溯源是启发式规则；引用只校验"原文中存在"，不校验"原文支持结论"。
- BM25 无法匹配同义词，依赖模型改写查询。

详见 [`docs/03-evaluation.md`](docs/03-evaluation.md) 第 6 节。

## 技术栈

原生实现，未使用 Agent 框架（理由见 [`docs/02-solution.md`](docs/02-solution.md) 第 6 节）。依赖：`openai`、`rank_bm25`、`jieba`、`python-dotenv`、`pytest`。
