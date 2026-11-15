# Offer Compass

Offer Compass 是一个“证据约束型”求职 Agent：从公开来源发现岗位，核对简历证据，判断岗位是否值得投，追问会改变判断的关键信息，生成可逐条审核的定制简历，并在人类确认边界内准备投递。

当前版本为 **Agent V5.8**。它在完整保留 V4.5 简历证据与审阅能力的基础上，新增可信岗位发现、免费本地初筛、预算化模型深度评分、可恢复 Application Graph、岗位级申请包、投递阶段历史、半自动表单填写计划，以及 OpenAI / 阿里云百炼模型切换与岗位来源自动识别。

## Agent 能力

- 本地 SQLite 档案、JD 分析和投递记录
- 技能熟练度与经历证据分层
- JD 多维匹配、缺口清单与投递判断
- 严格基于已知事实的简历改写，不虚构经历或数据
- Word / PDF 原件关联、逐条采纳或保留原文
- Word 原格式导出，优先使用本机 Word 生成高保真 PDF 预览
- PDF 限制修改模式与版式风险防护
- 持久化 Agent Run、状态机和工具调用轨迹
- 历史 Run 列表、刷新恢复和轨迹详情
- 自动识别 Greenhouse 与字节跳动校园招聘公开来源，按偏好筛选、去重并保存
- 全岗位免费本地初筛：偏好、JD 完整度与经历证据共同评分
- 仅对入围岗位进行可选模型深度评分，支持岗位上限、Token 预算和指纹缓存
- 岗位 → 证据 Agent → 关键追问 → 已校验简历 → 申请包的持久化流程
- 岗位级投递状态与阶段证据历史
- 不接触密码、验证码和最终提交的安全填写计划
- OpenAI / 阿里云百炼（Model Studio）模型配置、连通测试与实际 Token 用量展示
- 自动适配 OpenAI 与阿里云 Responses 结构化输出格式；Qwen 岗位评分关闭高成本深度思考
- V4.5 证据回归与 V5.8 来源识别、模型边界、排序、缓存和行动安全冻结评测

## Agent V5.8 架构

```text
Greenhouse / 字节公开岗位 → 自动识别 → jobs.py → SQLite 岗位库
                         ↓
          scoring.py 本地初筛（0 Token）
                         ↓
    入围岗位 → OpenAI / 阿里云模型深度评分（预算＋缓存）
                         ↓
Offer Compass Agent 工作台 → ApplicationGraph
                         ↓
              V4.5 Evidence Agent Runtime
                         ↓
         关键追问 / 已校验简历补丁 / 申请包
                         ↓
           人工审阅 → 安全填写计划 → 用户提交
```

V5 没有为了框架而强行引入 LangChain/LangGraph。流程以显式节点、持久化状态和确定性 Python 工具实现；这保留了 V4.5 的证据与事实校验边界，也方便未来替换编排框架。

V5 模块位于 `offer_compass_v5/`：

- `orchestrator.py`：可暂停、可恢复的申请流程图
- `jobs.py`：可信来源岗位发现与去重
- `model_provider.py`：模型供应商、地址白名单、连通测试和统一调用
- `scoring.py`：本地初筛、模型深度评分、Token 预算和指纹缓存
- `applications.py`：岗位级申请包和阶段历史
- `browser.py`：只生成映射、不执行提交的填写计划
- `security.py`：提示注入拦截与敏感数据遮盖
- `store.py`：有序数据库迁移
- `schemas.py`：API 输入边界

完整设计见 `Agent V5.0-V5.5 架构与边界.md`、`岗位两层评分 Prompt V5.6.md` 和 `Agent V5.8 模型与岗位来源.md`。

## V4.5 兼容层

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

## 评测基线

| 指标 | 基线 |
|---|---:|
| 用例通过率 | 100% (12/12) |
| 证据引用有效率 | 100% |
| 投递判断准确率 | 100% |
| 工具调用成功率 | 100% |
| V5.8 来源、模型边界与行动安全 | 100% (13/13) |

这是当前 12 个固定场景的回归基线，不代表对任意真实 JD 的泛化准确率。详细用例和失败信息见 `evals/`。

V5.8 另有来源自动识别、模型地址白名单、岗位去重、免费初筛、深度评分缓存、Graph 暂停、申请包、表单权限、网页提示注入和 PII 遮盖评测。两组评测都会在 CI 中执行。

## 本地启动

需要 Windows 和 Python 3.7+。Word 高保真预览需要安装 Microsoft Word 及 `pywin32`；不可用时会自动回退到网页近似预览。

```powershell
python offer_compass_server.py --port 8765 --open
```

或双击 `启动 Offer Compass.cmd`。

如需使用阿里云百炼模型，双击 `启动 Offer Compass - 阿里云模型.cmd`，按提示输入兼容模式 Base URL、模型名和 API Key。Key 只保留在当前服务进程内，不写入数据库或浏览器。没有配置模型时，免费本地初筛仍然可用；只有点击“深度评分入围岗位”才会调用所配置的模型并产生模型侧 Token 用量。

访问 <http://127.0.0.1:8765/>。用户档案默认保存在本地 `data/offer-compass.sqlite3`，不纳入 Git。

## 测试

```powershell
python -m unittest -v test_agent_runtime.py test_v5_platform.py
python evaluate_agent.py
python evaluate_v5.py
node .offer-review-qa.cjs
```

测试覆盖 Agent 状态恢复、工具轨迹、证据引用、安全改写、Word / PDF 预览、逐条审阅、文件导出和数据库恢复。

岗位抓取目前支持 Greenhouse 企业公开招聘页和字节跳动校园招聘公开页；登录、验证码和最终投递不由 Agent 代替用户完成。

## Prompt 工程

- `Offer Compass 工程 Prompt（MVP）.md`：产品流程、状态与实现边界
- `简历修改 Agent Prompt V3.md`：证据约束、改写边界与审核逻辑
- `简历修改 Agent Prompt V4.3.md`：工具调用、证据分级、结构化补丁和独立校验协议
- `Agent V4.5 运行历史与评测.md`：任务恢复、轨迹界面、评测指标与基线边界
- `Agent V5.0-V5.5 架构与边界.md`：岗位发现、申请包、浏览器协助、安全与兼容策略
- `求职申请 Agent Prompt V5.5.md`：跨搜岗、判断、改写、填写和人工审批的统一 Prompt 边界
- `岗位两层评分 Prompt V5.6.md`：免费初筛、付费深评、Token 预算、缓存与证据边界
- `Agent V5.8 模型与岗位来源.md`：模型选择、连通测试、来源自动识别与失败边界

## 隐私

不要将真实简历、联系方式、头像、本地数据库或 API Key 提交到公开仓库。

