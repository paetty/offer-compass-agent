# Offer Compass

Offer Compass 是一个“证据约束型”求职 Agent MVP：用户提供个人档案和岗位 JD 后，系统核对简历证据、识别能力缺口、评估匹配度，并生成可逐条审核的定制简历草稿。

当前版本为 **Agent V4.5**：前端只负责交互，Python Agent Runtime 负责状态迁移，受约束工具负责档案读取、JD 解析、证据检索、匹配判断、关键追问、结构化改写和独立事实校验。每个 Run 和工具调用均保存到本地 SQLite，可恢复、可追溯，并有可重复的 Agent 评测基线。

## MVP 能力

- 本地 SQLite 档案、JD 分析和投递记录
- 技能熟练度与经历证据分层
- JD 多维匹配、缺口清单与投递判断
- 严格基于已知事实的简历改写，不虚构经历或数据
- Word / PDF 原件关联、逐条采纳或保留原文
- Word 原格式导出，优先使用本机 Word 生成高保真 PDF 预览
- PDF 限制修改模式与版式风险防护
- 持久化 Agent Run、状态机和工具调用轨迹
- 历史 Run 列表、刷新恢复和轨迹详情
- 12 个安全与任务成功评测用例

## Agent V4.5 架构

```text
Offer Compass.html
       ↓ POST /api/agent/run
offer_compass_runtime.py
       ↓ 受约束工具调用
offer_compass_tools.py
       ↓
SQLite: agent_runs + agent_tool_calls
```

当前工具：

- `get_candidate_profile`
- `parse_job_description`
- `retrieve_resume_evidence`
- `assess_job_fit`
- `create_clarifying_question`
- `record_candidate_clarification`
- `generate_resume_patches`
- `verify_resume_patches`

当前状态：`jd_received` → `evidence_retrieval` → `clarification_required` / `fit_assessed` → `rewrite_generated` → `ready_for_review` / `verification_failed`。

详细设计见 `Agent V4.3 安全改写与证据协议.md`。

## V4.5 评测基线

| 指标 | 基线 |
|---|---:|
| 用例通过率 | 100% (12/12) |
| 证据引用有效率 | 100% |
| 投递判断准确率 | 100% |
| 工具调用成功率 | 100% |

这是当前 12 个固定场景的回归基线，不代表对任意真实 JD 的泛化准确率。详细用例和失败信息见 `evals/`。

## 本地启动

需要 Windows 和 Python 3.7+。Word 高保真预览需要安装 Microsoft Word 及 `pywin32`；不可用时会自动回退到网页近似预览。

```powershell
python offer_compass_server.py --port 8765 --open
```

或双击 `启动 Offer Compass.cmd`。

访问 <http://127.0.0.1:8765/>。用户档案默认保存在本地 `data/offer-compass.sqlite3`，不纳入 Git。

## 测试

```powershell
python -m unittest -v test_agent_runtime.py
python evaluate_agent.py
node .offer-review-qa.cjs
```

测试覆盖 Agent 状态恢复、工具轨迹、证据引用、安全改写、Word / PDF 预览、逐条审阅、文件导出和数据库恢复。

## Prompt 工程

- `Offer Compass 工程 Prompt（MVP）.md`：产品流程、状态与实现边界
- `简历修改 Agent Prompt V3.md`：证据约束、改写边界与审核逻辑
- `简历修改 Agent Prompt V4.3.md`：工具调用、证据分级、结构化补丁和独立校验协议
- `Agent V4.5 运行历史与评测.md`：任务恢复、轨迹界面、评测指标与基线边界

## 隐私

不要将真实简历、联系方式、头像、本地数据库或 API Key 提交到公开仓库。

