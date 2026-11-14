# Agent Evals

`cases.json` 是 Offer Compass V4.5 的可重复评测集，同时包含端到端 Agent Run 和独立补丁校验用例。

```powershell
python evaluate_agent.py
```

输出：

- `results/v4.5-baseline.json`：机器可读指标与失败明细；
- `results/v4.5-baseline.md`：GitHub 可读的基线报告。

当前核心指标：用例通过率、证据引用有效率、投递判断准确率和工具成功率。

