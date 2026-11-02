# 02 方案：调用流程、动态决策与技术取舍

## 1. 总体设计

一句话：**模型决定下一步做什么，代码保证不越界、不撒谎。**

- 模型负责的决策：检索什么词、要不要改写查询、读哪份原文、是否重试、冲突能否查证、何时停止，以及最终给出哪种状态。
- 代码负责的约束：步数和调用次数上限、重试额度、`finish` 的引用与数字校验、工具异常兜底、Trace 记录。

代码里没有任何"如果冲突就……""如果失败就重试"之类的业务分支。每一步执行什么，完全取决于模型返回的 tool call。

## 2. 模块与职责

| 文件 | 职责 |
|---|---|
| `src/kqa/corpus.py` | 加载合成文档；用 jieba 分词，建段落级 BM25 索引；提供 `search` 和 `read` |
| `src/kqa/tools.py` | 业务工具 `search_docs` 和 `read_doc`、给模型的 JSON Schema、统一错误契约、失败注入、重试额度 |
| `src/kqa/validator.py` | `finish` 的代码级校验：结构、引用、状态、过程证据、数字溯源 |
| `src/kqa/agent.py` | ReAct 循环、System Prompt、上限控制、Trace |
| `src/kqa/llm.py` | OpenAI 兼容接口接入（可选参数自适应），以及离线 `MockLLM` |
| `src/kqa/cli.py` | 命令行入口：单题、多轮对话、失败注入、Mock |
| `scripts/run_cases.py` | 用真实模型批量跑用例，保存 Trace、汇总表和每次运行的存档 |

## 3. 调用流程

```mermaid
flowchart TD
    Q[用户问题 + 可选对话历史] --> L{模型选择动作}
    L -->|search_docs| S[BM25 段落检索]
    L -->|read_doc| R[读取原文<br/>记入"已读文档"]
    L -->|finish| V{validator 校验}
    L -->|直接文字回复| N[代码提醒：请用 finish] --> L
    S --> O[观察结果写回消息<br/>并记入 Trace]
    R --> O
    V -->|不通过| O
    V -->|通过| E[返回结果<br/>status/answer/citations/gaps]
    O --> C{达到步数或<br/>调用上限?}
    C -->|否| L
    C -->|是| I[返回 incomplete<br/>不给结论]
```

每一轮的过程：

1. **模型选择动作**：调用 `llm.chat(messages, TOOL_SCHEMAS)`，记为一步。模型可以在一轮里并行发出多个调用，比如同时读两份文档。
2. **执行或拦截**：代码先做拦截检查（参数不是 JSON、`finish` 与其他调用混在同一轮、与已成功的调用完全重复），通过后再执行工具或校验 `finish`。
3. **观察**：工具结果以 `role=tool` 消息回给模型，同时写入 Trace（步骤、动作、参数、返回摘要、模型给出的理由）。
4. **下一轮**：模型根据观察结果决定继续还是结束。

### 结束状态

| status | 含义 | 由谁给出 |
|---|---|---|
| `answered` | 有原文依据，已回答（包括冲突但能查证出有效版本的情况） | 模型，经校验 |
| `conflict` | 多份文档说法不一致，原文无法判定，请用户确认 | 模型，经校验 |
| `no_info` | 资料中没有依据 | 模型，经校验 |
| `need_clarification` | 指代不清，对话历史也无法补全，需要追问 | 模型，经校验 |
| `tool_failed` | 工具持续失败 | 模型，经校验 |
| `incomplete` | 达到步数或调用上限，或模型 API 异常 | 代码 |

`stop_reason` 记录具体的结束原因：`finished`、`max_steps`、`max_tool_calls` 或 `llm_error`。

## 4. 动态决策：模型在哪里做选择

| 观察到的情况 | 模型可选的动作 | Trace 中的例子 |
|---|---|---|
| 检索为 0 条，或片段并未回答问题 | 改写查询（最多 2 次），或判定 `no_info` | C03：股票期权 → 股权激励 → 期权归属，3 次均为 0 条后返回 no_info |
| 命中多份文档且说法不同 | 读取全部原文，比对适用范围、版本日期和替代关系 | C02 返回 conflict；C08 根据 D06 中"替代 D05"的原文回答 2 天 |
| 文档库被替换，只剩一份文档 | 直接依据该文档回答 | C09 与 C02 是同一问题，动作从"请求确认"变为"直接回答" |
| 工具返回 `unavailable, 可重试` | 重试（可以换参数），或停止 | C06：换成"重置密码"重试成功；C07：额度用完后返回 tool_failed |
| 问题指代不清、没有历史 | 追问，可以列出候选 | C11 返回 need_clarification |
| 问题指代不清、但有历史 | 用历史补全后检索 | C05：从"成都住宿"补全为"深圳 出差 住宿 标准" |
| `finish` 被校验器打回 | 补读原文、修正引用或删去数字后重新提交 | validator-v3 C11.r2：引用 D04 前未读原文 → 被打回 → read_doc → 重新提交通过 |

## 5. 代码层的硬约束

### 5.1 工具错误契约（`tools.py`）

所有工具都返回 `{ok: true, data}` 或 `{ok: false, error_type, retryable, error}`，绝不把异常抛到循环外。

| error_type | 场景 | retryable |
|---|---|---|
| `unavailable` | 服务不可用（包括注入的失败） | 额度内为 true，额度用完后为 false |
| `not_found` | 文档 ID 不存在 | false |
| `bad_args` | 参数为空、过长、类型错误、未知工具 | false |
| `internal` | 实现缺陷（兜底） | false |

**重试额度由代码强制**：按工具分别统计连续的 `unavailable` 次数，默认 `max_retries = 1`。额度用完后 `retryable` 变为 false，并提示以 `tool_failed` 如实报告。任何一次成功调用都会清零计数；参数错误不计入。

### 5.2 循环边界（`agent.py`）

- `max_steps = 8`（一次模型调用为一步），`max_tool_calls = 12`。
- `finish` 必须单独调用。和其他工具在同一轮出现时会被打回，防止模型在看到原文之前就作答。
- 与已成功调用参数完全相同的调用不再执行，返回 `duplicate`，防止原地打转。失败的调用允许原样重试。
- 模型 API 异常时返回 `incomplete / llm_error`，不伪造答案。

### 5.3 finish 校验（`validator.py`）

| 类别 | 规则 |
|---|---|
| 结构 | status 合法；answer 非空；citations 为 `[{doc_id, quote}]` |
| 引用 | 文档存在；本轮已用 `read_doc` 读过；quote 是原文的逐字片段 |
| 状态 | answered 至少 1 条引用；conflict 至少引用 2 份不同文档；no_info 和 tool_failed 不带引用；need_clarification 必须有追问 |
| 过程证据 | no_info 之前至少成功检索过一次；tool_failed 之前确实观察到过服务失败 |
| 数字溯源 | answered、conflict、need_clarification 状态下，展示给用户的数字必须出现在引用原文、被引文档的版本日期或用户问题中 |

数字溯源是启发式规则，专门拦截"引用 D01（30 天）却回答 15 天"这类张冠李戴，不做语义判断。

## 6. 技术取舍

### 6.1 原生实现，而不是 LangGraph、Google ADK 或 AgentScope

| 维度 | 本题需求 | 框架的主要价值 |
|---|---|---|
| 工具 | 3 个 | 大量工具的编排 |
| Agent | 1 个 | 多 Agent 协作（ADK、AgentScope） |
| 状态 | 一次问答内的消息列表和已读文档集合 | 持久化、断点续跑（LangGraph checkpointer） |
| 考核重点 | 循环边界、引用把关、Trace | — |

框架的核心能力在本题用不上，而本题考核的循环边界和引用校验无论如何都要自己写。用了框架，循环会被框架接管，评审更难看清"模型决策"和"代码约束"的分界。原生循环（`agent.py`）约 170 行，每一行都可以解释。

**迁移路径**：如果需求扩展到长流程、人工审批中断或断点续跑，可以迁移到 LangGraph。`tools.py` 和 `validator.py` 不依赖循环实现，可以直接复用，作为图中的节点。

### 6.2 BM25 而不是向量检索

- **规模**：9 份文档、20 个段落，向量检索的语义召回优势体现不出来。
- **可复现**：结果是确定性的，所以每个用例依赖的检索前提都能写成断言（`tests/test_retrieval.py`）。
- **语义鸿沟交给模型**：比如"休假"查不到"年假"，这时由模型改写查询，这本身就是题目要求的"补充检索"决策。

已知局限：同义词无法匹配；没有可靠的相关性阈值（合法命中和噪声的分数可能接近），所以不设固定阈值，由模型判断片段是否回答了问题。规模扩大后的方案是混合检索：BM25 加向量检索，用 RRF 融合。

### 6.3 硬约束写在代码里，而不只写在 prompt 里

Prompt 只能"建议"，评审注入异常时最容易失守的就是只靠 prompt 的约束。本项目中每条关键约束都有代码保证：

| 约束 | prompt | 代码 |
|---|---|---|
| 引用必须来自原文 | 第 2 条 | validator：逐字匹配 + 已读检查 |
| 冲突不得擅自选择 | 第 3 条 | validator：conflict 至少引用 2 份文档 |
| 不得无依据回答 | 第 4、7 条 | validator：数字溯源、过程证据 |
| 重试上限 | 第 6 条 | ToolBox：重试额度用完后 retryable=false |
| 必须用 finish 结束 | 第 8 条 | 循环：文字回复不被接受，并提醒模型 |

### 6.4 模型与接口

- 模型：`gpt-5.4-mini`，通过 OpenAI 兼容的 Chat Completions 接口调用；改 `LLM_BASE_URL` 和 `LLM_MODEL` 即可换成其他兼容服务。
- `temperature` 和 `reasoning_effort` 只在 `.env` 里配置了才会发送。若被服务端拒绝，自动去掉该参数重试一次，原因记录在 `dropped_params` 中。

### 6.5 依赖

没有使用 Agent 框架。第三方库及其职责：

| 库 | 版本要求 | 职责 |
|---|---|---|
| `openai` | ≥ 1.40 | 调用 Chat Completions 和 tool calling |
| `rank_bm25` | 0.2.2 | BM25 打分 |
| `jieba` | 0.42.1 | 中文分词 |
| `python-dotenv` | ≥ 1.0 | 读取 `.env` |
| `pytest` | ≥ 8.0 | 离线测试 |
