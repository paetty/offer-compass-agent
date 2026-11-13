# Agent V4.3 安全改写与证据协议

## 新增能力

V4.2 将证据统一分为：

- `resume_experience`：简历原文中有任务、方法或结果；
- `resume_mention`：简历只提到关键词；
- `self_rating`：用户技能自评，不等于项目经历；
- `candidate_confirmation`：用户明确勾选确认的补充事实；
- `unconfirmed`：用户回答但未确认，不进入证据；
- `missing`：没有找到依据。

证据使用 `evidence_id` 和保留空行的原始 `line` 定位，不得因删除空行导致行号偏移。

V4.3 将改写输出改为结构化补丁：

```json
{
  "patch_id": "patch_01",
  "source_line": 23,
  "before": "简历原文",
  "after": "改写后文字",
  "reason": "与 JD 的关系",
  "requirement_id": "req_03",
  "evidence_ids": ["resume_line_0023"],
  "verified": true,
  "risk": "low"
}
```

## 独立校验

`verify_resume_patches` 不信任改写结果，逐条检查：

1. `source_line` 与 `before` 是否精确对应原文；
2. 原文是否唯一，避免修改错误段落；
3. 数字、比例和量化结果是否发生变化；
4. 是否新增原文没有的工具或能力；
5. 是否把“参与/协助”升级为“主导/独立负责”；
6. 文字增长是否超过安全边界；
7. 是否包含可追溯的 `evidence_ids`。

任意一项不通过，补丁进入 `rejected_patches`，不得进入用户审阅和导出。

## 人工决策边界

- 证据确认和补丁采纳是两次独立的用户决策。
- 用户确认一段补充经历，不等于自动采纳任何改写。
- 只有 `verified: true` 的补丁能显示在红绿审阅界面。
- 用户采纳后才能写回 Word；拒绝时保留原文。

## 状态流转

```text
evidence_retrieval
  ├─ clarification_required → record_candidate_clarification
  └─ fit_assessed
       → rewrite_generated
          → ready_for_review
          └─ verification_failed
```

