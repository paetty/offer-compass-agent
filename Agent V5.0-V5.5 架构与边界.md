# Offer Compass Agent V5.0—V5.5

## 产品定位

Offer Compass 是面向求职者的证据约束型投递 Agent。它把“发现岗位—判断是否值得投—补问关键证据—生成可信简历—人工审阅—辅助填写—记录进度”连接成一条可恢复、可审计的任务链。

它不是批量海投机器人。登录、密码、验证码、不确定字段和最终提交始终由用户处理。

## V5.0 · 兼容式工程拆分

- 保留 `Offer Compass.html`、`/api/workspace`、`/api/agent/run` 和原有 Word/PDF 审阅能力。
- 新增 `offer_compass_v5/`，拆分 schemas、jobs、applications、browser、security、store、orchestrator 和 API facade。
- 所有 V5 数据采用有序 SQLite migration；不修改或搬迁用户已有 workspace 数据。
- 增加 `/api/v5/*`，旧 API 继续工作。

## V5.1 · 可恢复显式 Agent Graph

V5 没有为了框架而引入 LangChain/LangGraph。参考 RecruitOps 的做法，图节点由确定性 Python 服务与现有证据 Agent 组成：

```text
load_job
  → evidence_agent
  → human_clarification（可暂停）
  → verified_draft
  → application_package
```

任务状态保存在 `v5_tasks`；Agent 内部 Run 仍保存在 `agent_runs` 和 `agent_tool_calls`。因此用户回答关键追问时可携带同一 `task_id` 和 `agent_run_id` 继续。

## V5.2 · 可信岗位发现 MVP

- 后端支持 Greenhouse 公开招聘页。
- 只访问固定 HTTPS API，不携带浏览器 Cookie。
- 按岗位、行业和城市偏好筛选。
- `source_id + external_id` 形成稳定岗位身份；重复刷新执行 upsert。
- 保存原始 JD、官方链接、内容哈希、首次与最近发现时间。
- 抓取失败与“没有岗位”分离，不伪造成功状态。

当前只声称 Greenhouse MVP 覆盖，不声称兼容所有公司官网。

## V5.3 · 岗位级申请包

值得投且不再等待追问的岗位会形成独立 application package，绑定：

- 岗位和官方链接
- Agent Run
- 投递判断
- 简历证据与缺口
- 通过事实校验的简历补丁
- 投递阶段与阶段证据

状态变化写入 `application_events`，不直接抹掉历史。

## V5.4 · 半自动填写边界

`/api/v5/browser/plan` 只生成字段映射计划，不执行网页脚本，也没有提交工具。`browser_extension/` 提供可选的本地 Chrome 扩展：只有用户点击扩展时才获得当前标签页权限，按照相同边界填写可靠字段。

强制跳过：

- password / hidden 字段
- OTP、验证码、身份证、银行卡等敏感字段
- 最终提交按钮
- 没有可靠候选人字段映射的输入项

网页文字被当作不可信证据。发现“忽略之前指令”“上传 Cookie”“绕过验证码”等提示注入特征时，整份计划失败关闭。

## V5.5 · 回归、行动安全与 CI

- V4.5 的 12 个证据与改写用例继续运行。
- 新增岗位筛选、去重、Agent Graph、申请包、表单边界、提示注入、PII 遮盖和阶段历史评测。
- GitHub Actions 在 Windows 上运行单元测试和两组冻结评测。

固定用例通过只代表回归基线，不代表任意招聘网站、任意 JD 或任意简历的泛化准确率。

## V5.6 · 两层岗位评分

- 第一层对全部有效岗位运行确定性本地筛选，不调用模型；分数来自偏好、JD 完整度和可定位的经历证据。
- 第二层只处理第一层的入围与待观察岗位，且必须由用户明确同意使用模型额度。
- 用户可限制本轮岗位数和 Token 预算；界面显示新调用、缓存命中与实际用量。
- `岗位 + JD 哈希 + 档案哈希 + Prompt 版本 + 模型` 构成缓存键，输入未变化时不重复调用模型。
- 模型只接收已定位并脱敏的简历证据行；返回的越界证据行会被丢弃。
- 深度分数用于排序和辅助判断，不替代后续证据 Agent、人工审阅或最终投递决策。

## API

### 读取

- `GET /api/v5/capabilities`
- `GET /api/v5/jobs?limit=50`
- `GET /api/v5/applications?limit=50`
- `GET /api/v5/tasks?limit=30`

### 操作

- `POST /api/v5/jobs/discover`
- `POST /api/v5/jobs/screen`
- `POST /api/v5/jobs/deep-score`
- `POST /api/v5/agent/run`
- `POST /api/v5/applications`
- `POST /api/v5/browser/plan`

所有写请求只接受本机同源页面。当前版本没有最终提交接口。

### 可选浏览器助手

在 Chrome 的“管理扩展程序”中启用开发者模式，选择“加载已解压的扩展程序”，指向 `browser_extension/`。扩展不申请招聘网站常驻权限或 Cookie 权限；个人资料只保存在扩展本地存储。它不是网站兼容性承诺，动态组件或特殊 ATS 字段仍需用户手工填写。
