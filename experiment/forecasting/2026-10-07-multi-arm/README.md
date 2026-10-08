# ForecastLab v2 正式评测（2026-10-07）

本目录保存 fix/full-pipeline-coverage 修复后的第一轮正式 v2 评测。

## 运行基线

- 远端分支：YOUessi/forecastlab:fix/full-pipeline-coverage
- 远端提交：71a95d1188df1733a7eb785ad615dea09da4816f
- Git tree：99339204249e2cf7e1ea8fa65eacbf0888edd71a
- 套件：eval/suites/forecastlab-v2.json
- 案例数：24
- 模型：deepseek-flash
- temperature：0
- full arm 与两个 baseline 使用同一 v2 问题/证据包

v2 离线 validator：READY 24，NEEDS REVIEW 0，BROKEN 0，strict cutoff-ready 24/24。

## 文件

- results/v2-full-run1.json：24-case full pipeline 原始结果与 审查/证据评估 诊断
- results/v2-baselines-run1.json：single-agent + evidence / no-evidence 两臂
- results/v2-combined-run1.json：三臂按 case 合并结果
- results/v2-combined-run1-score.json：Brier / coverage / BSS / 分类分层
- results/v2-analysis-summary.json：状态、matched-subset、成本等派生汇总
- results/v2-validation.json：v2 cutoff provenance 离线校验
- results/v2-full-run1-meta.json：分支、提交、tree、模型和温度元数据
- report.md：完整分析

## 核心结果

| 方法 | Coverage | Brier | 弃权回退 Brier | BSS vs 0.5 |
| --- | ---: | ---: | ---: | ---: |
| Full pipeline | 75.0% (18/24) | 0.1337（已答） | 0.1628 | +0.465 |
| Single Agent + 同证据 | 100% | 0.1443 | 0.1443 | +0.423 |
| Single Agent 无证据 | 100% | 0.1881 | 0.1881 | +0.247 |
| 恒定 50% | 100% | 0.2500 | 0.2500 | 0 |

注意：Full 的 0.1337 不能直接解释为比 Single Agent 的 0.1443 更准，因为 Full 只回答了 18 个案例。

在 Full 实际回答的相同 18 个案例上：

- Full：0.1337
- Single Agent + evidence：0.1061
- No evidence：0.1658

因此本轮不能声称完整多阶段流程在预测准确率上优于单 Agent + 同证据。

## Pipeline recovery

问题分析与证据评估 合并后的旧 v1 运行：

- completed：4
- insufficient_evidence：7
- failed：13
- coverage：16.7%

v2 run #1：

- completed：18
- insufficient_evidence：6
- failed：0
- coverage：75.0%

旧 13 个 F-ID hard failure 在本轮全部消失：8 个变为 completed，5 个变为正常 abstention。

## 重要架构发现

本轮 Review：

- blocked：23
- qualified：1
- passed：0

最终概率依据：

- evidence_only：18
- none：6
- full：0

也就是说，本轮没有一个概率真正以完整 world/simulation 路径作为最终依据。系统虽然支付了多阶段推演成本，但 Review 几乎总会阻断推演结果，然后转入 evidence-only fallback。

## 成本

| Arm | Model calls | Tokens | Model seconds |
| --- | ---: | ---: | ---: |
| Full | 185 | 670,815 | 468.59 |
| Single Agent + evidence | 24 | 31,384 | 28.22 |
| No evidence | 24 | 11,988 | 25.40 |

相对 Single Agent + evidence，Full 约为 7.7 倍模型调用、21.4 倍 token、16.6 倍模型耗时。

正式结果仍属于历史回看实验，并非实时盲测。


## Run 2：abstention 语义修复后的结果

在保存 EvidenceOnlyAudit 阻断原因后，我们确认部分 abstention 错把未来结算期结果当成事前证据要求。后端现已确定性过滤此类理由，同时继续保留来源质量、目标相关性、规则/参赛信息等真实事前缺口。

最终修复提交：`9aaa570`。

| Full arm | Coverage | Brier（已答） | 0.5 fallback Brier |
| --- | ---: | ---: | ---: |
| v2 Run 1 | 75.0% | **0.1337** | **0.1628** |
| v2 Run 2 | **87.5%** | 0.1578 | 0.1694 |

结论：coverage 提升没有改善预测质量；不能为了减少 abstention 继续放宽审查。

Run 2 原始与分析结果：

- `results/v2-full-run2.json`
- `results/v2-full-run2-score.json`
- `results/v2-run2-analysis.json`
- `results/v2-full-run2-meta.json`
