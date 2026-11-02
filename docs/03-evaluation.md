# 03 评估：测试预期、实际结果、失败与限制

## 1. 评估方法

验证分为两类，各自证明不同的东西：

| 类型 | 运行方式 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 离线测试（77 个） | `pytest -q`，使用 MockLLM 或直接调用函数 | 工具契约、校验规则、循环边界这些**确定性代码**的行为正确 | 真实模型会不会做出正确决策 |
| 真实模型用例（11 个） | `python scripts/run_cases.py`，使用 gpt-5.4-mini | 模型根据工具结果**调整动作**的能力，以及代码约束在真实运行中生效 | 结果具有统计意义（样本少，见第 6 节） |

Mock 只用来验证流程。本文中所有关于"模型决策"的结论，都来自真实模型运行。

## 2. 真实模型最终结果

- 运行：[`examples/runs/20261101-211406-final-v3`](../examples/runs/20261101-211406-final-v3/results.md)，内容与 [`examples/results.md`](../examples/results.md) 相同
- 模型：`gpt-5.4-mini`；代码版本：`dabf831`；通过：**11/11**
- 每个用例的完整 Trace：[`examples/traces/`](../examples/traces/)

| 用例 | 验收点 | 预期 | 实际 | 动作序列（✗ = 该步返回错误） | Trace |
|---|---|---|---|---|---|
| C01 | 明确答案 | answered / D03 | answered / D03 | search → read → finish | [C01](../examples/traces/C01.json) |
| C02 | 文档冲突 | conflict / D01+D02 | conflict / D01+D02 | search → read D01 ∥ read D02 → finish | [C02](../examples/traces/C02.json) |
| C03 | 资料外问题 | no_info | no_info | search ×3 → finish | [C03](../examples/traces/C03.json) |
| C04 | 改写后的相似问题 | answered / D03 | answered / D03 | search → read → finish | [C04](../examples/traces/C04.json) |
| C05 | 上下文追问 | answered / D04 | answered / D04 | search → read → finish | [C05](../examples/traces/C05.json) |
| C06 | 工具失败（可恢复） | answered / D08 | answered / D08 | search✗ → search → read → finish | [C06](../examples/traces/C06.json) |
| C07 | 工具失败（不可恢复） | tool_failed | tool_failed | search✗ → search✗ → finish | [C07](../examples/traces/C07.json) |
| C08 | 冲突可查证 | answered / D06 | answered / D06 | search → read D05 ∥ read D06 → finish | [C08](../examples/traces/C08.json) |
| C09 | 替换文档后动作改变 | answered / D01 | answered / D01 | search → read → finish | [C09](../examples/traces/C09.json) |
| C10 | 步数上限 | incomplete | incomplete | search → read ∥ read → 到达上限 | [C10](../examples/traces/C10.json) |
| C11 | 追问 | need_clarification | need_clarification | 文字回复 → 被提醒 → finish | [C11](../examples/traces/C11.json) |

**试卷要求的覆盖情况**
- 三类场景：正常（C01、C04、C05、C08、C09）、信息或资料不足（C03、C11）、工具失败（C06、C07）。
- 题目的 6 个必须验收点：明确答案 C01、文档冲突 C02/C08、资料外问题 C03、改写后的相似问题 C04、上下文追问 C05、工具失败 C06/C07。
- 执行上限：C10。

## 3. 模型根据工具结果调整动作：关键证据

### 3.1 同一问题、不同文档 → 不同动作（C02 与 C09）

题目的审核要点之一是"替换文档内容，核对答案与动作是否改变"。

| | 文档库 | 检索命中 | 读取 | 结果 |
|---|---|---|---|---|
| [C02](../examples/traces/C02.json) | 完整 | D01、D02 | 两份都读 | **conflict**，列出 30 天和 15 天两种说法，追问"适用哪份制度" |
| [C09](../examples/traces/C09.json) | 去掉 D02 | 只有 D01 | 只读 D01 | **answered**，30 天 |

C02 的 gaps 写明："未找到原文明确的替代/废止关系，无法判定最终适用版本"。模型没有擅自选择较新的 D02（15 天），符合题目参考实例的要求。

### 3.2 冲突能查证时不追问（C08）

旧版 D05 在检索结果中排第一（这是有意设计的，见 `tests/test_retrieval.py`）。模型读了两份文档，引用 D06 原文"本版本自 2026-05-01 起生效，替代 D05"作为判定依据，回答 2 天，并说明旧版是 1 天。C02 和 C08 的对比说明，模型区分了"真正的冲突"和"可以查证的版本更替"。

### 3.3 工具失败：观察到错误后调整（C06 与 C07）

```
C06  1 search_docs "忘记密码 重置 密码"  ERROR [unavailable, 可重试] 剩余重试次数 1
     2 search_docs "重置密码"            2 条命中 → read D08 → answered
C07  1 search_docs "年假 申请 提前 几天"  ERROR [unavailable, 可重试]
     2 search_docs "年休假 申请 提前 天数" ERROR [unavailable] 重试上限已用完，请停止…
     3 finish tool_failed："未能完成检索，暂时无法确认年假需要提前几天申请。"
```

两个用例里，模型重试时都**换了查询词**，没有原样重发。C07 在额度用完后立即停止，没有凭常识回答"3 天"，尽管这个答案碰巧正确。

### 3.4 校验器打回 → 模型修正（真实运行）

[validator-v3 / C11.r2](../examples/runs/20261101-211319-validator-v3/C11.r2.json)：

```
1 search_docs  命中 D04 三段
2 finish       ERROR [validation] citations[0] 引用的 D04 本轮未用 read_doc 读取过原文 …
3 read_doc     读取 D04
4 finish       校验通过（need_clarification，附 D04 引用）
```

这是"代码把关 → 模型根据观察调整"在真实运行中的直接证据。

### 3.5 查询改写（C03、C04）

- C03：股票期权 行权 → 股权激励 行权 → 期权 归属 行权，3 次都是 0 条命中，然后停止，正好用满"最多改写 2 次"的额度。
- C04：口语问题"一年能歇几天带薪的假"，模型首次检索就改写成书面用语"带薪年假 天数 工作满两年"，命中 D03。

## 4. 迭代过程与修改记录

完整记录见 [`examples/runs/index.md`](../examples/runs/index.md)。

| 运行 | 代码版本 | 范围 | 通过 | 改了什么、为什么 |
|---|---|---|---|---|
| [baseline](../examples/runs/20261101-210018-baseline/results.md) | a2f38c5 | 全部 | 10/11 | — |
| [prompt-v2](../examples/runs/20261101-210703-prompt-v2/results.md) | 731be02 | C11、C05 各 3 次 | 6/6 | baseline 中 C11 失败（见下文），修改 prompt 第 5 条；同时跑 C05 做回归 |
| [final](../examples/runs/20261101-210757-final/results.md) | 731be02 | 全部 | 11/11 | 全量回归 |
| [validator-v3](../examples/runs/20261101-211319-validator-v3/results.md) | dabf831 | C11 3 次 | 3/3 | prompt-v2 中发现追问里夹带了未经核实的数字，扩展数字溯源规则 |
| [final-v3](../examples/runs/20261101-211406-final-v3/results.md) | dabf831 | 全部 | 11/11 | 用最终提交的代码重跑全量，作为交付结果 |

### 失败 1：C11 用假设句式作答（baseline）

- **现象**：问题是"那个标准是多少？"，没有对话历史。模型回答"**如果你问的是**差旅住宿标准：一线城市 600 元……"，状态为 answered（[baseline/C11](../examples/runs/20261101-210018-baseline/C11.json)）。
- **分析**：引用全部合法，代码校验拦不住。问题在于模型替用户做了选择：文档库里恰好只有一种"标准"，模型就默认是它。
- **修改**：在 prompt 第 5 条中写明，有指代、但历史无法补全时必须追问；禁止用"如果你问的是……"的假设句式；可以把候选写进追问。
- **回归**：C11 3/3，C05（有历史的指代句）3/3 仍能正常回答。

### 失败 2：追问里夹带未经核实的数字（prompt-v2）

- **现象**：[prompt-v2/C11.r3](../examples/runs/20261101-210703-prompt-v2/C11.r3.json) 的状态正确（need_clarification），但回答里写着"一线城市 600 元，其他城市 400 元"，而模型**没有读原文，也没有引用**。
- **分析**：数字溯源规则原来只覆盖 answered 和 conflict，追问成了漏洞。
- **修改**：将规则扩展到 need_clarification，并同时检查 answer 和 clarifying_question（`dabf831`，新增测试 `test_clarification_cannot_leak_unverified_numbers`）。
- **回归**：validator-v3 中，C11.r2 被打回后补读了原文（见 3.4）。

### 开发过程中由离线测试或试跑发现的问题

| 问题 | 发现方式 | 修改 |
|---|---|---|
| 资料外问题检索结果全是无关噪声，不够真实 | 检索核对 | 新增近似干扰文档 D09《员工福利总览》 |
| 模型不检索也能返回 no_info，没有失败也能返回 tool_failed | 用假模型试跑评测脚本 | 校验器增加过程证据规则 |
| `finish` 和 `read_doc` 在同一轮时，finish 先于读取执行 | 审查循环 | 规定 finish 必须单独调用 |
| 中文紧挨文档 ID 时（如 `D01为30天`），正则 `\b` 不生效，文档 ID 里的数字被当成事实 | 单元测试 | 改用前后断言 |
| 参数错误会消耗失败注入的额度，导致 C06 失去测试意义 | 单元测试 | 参数校验放在失败注入之前 |

## 5. 离线测试

```bash
pytest -q   # 77 passed
```

| 文件 | 数量 | 覆盖内容 |
|---|---|---|
| `tests/test_tools.py` | 27 | 统一返回结构、四类错误、retryable、参数容错、doc_id 规范化、失败注入、重试额度（默认值、可配置、清零、按工具独立） |
| `tests/test_agent.py` | 25 | 校验器各项规则、finish 混调、重复调用、调用上限、步数上限、LLM 异常、文字回复提醒、非法 JSON、Trace 字段、过程证据、追问中的数字 |
| `tests/test_offline.py` | 12 | Mock 端到端：正常、冲突、工具重试、finish 打回后修正、步数上限 |
| `tests/test_retrieval.py` | 10 | 每个用例依赖的检索前提，例如 C08 中 D05 排在 D06 之前、"休假"查不到"年假" |
| `tests/test_llm.py` | 3 | 可选参数被拒后自动去掉并重试；其他 400 错误照常抛出 |

## 6. 限制与已知问题

1. **样本量小。** 11 个用例各跑 1 次，C11 和 C05 额外各跑了 3 次。11/11 说明核心链路可行，不代表在更多说法下的准确率。
2. **C11 的路径不稳定。** 7 次运行里，最终状态都正确，但过程各不相同：直接追问、先用文字回复再被提醒、检索后列出候选、被校验器打回后补读原文。模型对"追问前要不要先检索"拿不准。
3. **数字溯源是启发式规则。** 它只拦截数字层面的不一致，拦不住无数字的语义错误，比如把"一线城市"说成"所有城市"。中文数字（"两年"）不参与检查。
4. **引用只校验"原文中存在"，不校验"原文支持结论"。** 例如引用了一句真实原文，但它与回答无关，校验器无法识别。
5. **C10 依赖模型的速度。** max_steps=2 时，如果模型 2 步内就能结束，就测不到上限。本次运行中模型第 2 步并行读了两份文档，正好触达上限。
6. **BM25 的语义鸿沟。** 依赖模型改写查询，每次改写都多一次模型调用。
7. **数据是合成的，规模很小。** 9 份文档、20 个段落，不能代表真实制度库的复杂度（长文档、表格、跨文档引用等）。
