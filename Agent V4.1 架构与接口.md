# Offer Compass Agent V4.1

## 定位

V4.1 是一个可持久化、可审计的单 Agent 骨架。它不依赖模型自由修改数据或文件；Agent 只能通过受约束的 Python 工具读取档案、解析 JD、检索证据并产生投递判断。

V4.1 不包含简历改写工具和独立事实校验器，这两项属于后续 Safe Rewrite 阶段。

## 状态流转

```text
jd_received
  → evidence_retrieval
      → clarification_required → 等待用户回答 → evidence_retrieval
      → fit_assessed
```

每个 Run 使用独立 `run_id`。再次提交同一 `run_id` 时，Agent 从 SQLite 恢复状态；如果 JD 或档案内容发生变化，该 Run 会拒绝继续，避免将两个候选人或两个 JD 的证据混合。

## 统一接口

### 创建或继续 Run

`POST /api/agent/run`

```json
{
  "run_id": "",
  "goal": "判断是否值得投递",
  "jd": "岗位 JD 原文",
  "lines": ["简历原文行"],
  "levels": {"SQL": 2},
  "answer": "",
  "skip": false
}
```

返回：

```json
{
  "run_id": "run_...",
  "stage": "clarification_required",
  "status": "waiting",
  "verdict": "conditional",
  "question": "你在该项目中负责哪一部分？",
  "next_action": "wait_for_user",
  "tool_trace": []
}
```

### 恢复 Run

`GET /api/agent/run?run_id=run_...`

该接口只返回前端所需的公开状态，不返回完整档案快照和工具原始输入。

## 工具边界

| 工具 | 作用 | 不能做什么 |
|---|---|---|
| `get_candidate_profile` | 校验档案文本与技能等级 | 不改写档案 |
| `parse_job_description` | 抽取可引用的岗位要求 | 不补全 JD 没写的要求 |
| `retrieve_resume_evidence` | 将要求映射到简历原文 | 不把技能自评当成项目经历 |
| `assess_job_fit` | 输出保守的投递判断 | 不自动投递 |
| `create_clarifying_question` | 生成一个关键追问 | 不同时追问多个问题 |

## 持久化与审计

- `agent_runs`：保存 Run 状态、阶段、修订号和输入指纹。
- `agent_tool_calls`：保存工具顺序、输入、输出、状态、耗时与错误。
- 前端只显示工具摘要和耗时，不暴露完整档案快照。

## V4.1 验收标准

- 同一 Run 可在用户回答后继续。
- 缺少关键证据时只追问一个问题。
- 所有工具调用均有可审计记录。
- 更换 JD 或档案后不能沿用旧 Run。
- 不配置 API Key 也能完成本地工具编排。
- Word / PDF 审阅、导出和数据库恢复回归测试通过。

