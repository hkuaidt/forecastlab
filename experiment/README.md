# ForecastLab 实验目录

本目录汇总各轮评测实验。**差异部分**（每轮的套件、证据包、结果、报告）放在各自子文件夹；
**公共部分**（本说明、离线校验器）放在本层。

## 问题、证据与全流程补充实验

下列目录保存原始评测结果和对应报告；可复用执行工具位于根目录的 `eval/`。命令默认从仓库根目录运行。

| 范围 | 实验 |
| --- | --- |
| 问题分析稳健性 | [中性与引导性问题对照](question-framing/2026-10-07-robustness/report.md) |
| 证据质量 | [语义审计](evidence-quality/2026-10-07-semantic/report.md)、[真实检索](evidence-quality/2026-10-07-live-retrieval/report.md) |
| 引文边界 | [第一轮](evidence-quality/2026-10-08-boundary-v1/report.md)、[第二轮](evidence-quality/2026-10-08-boundary-v2/report.md) |
| 语义蕴含 | [基准实验](evidence-quality/2026-10-08-entailment-benchmark/report.md)、[判定器评测](evidence-quality/2026-10-08-entailment-judge/report.md) |
| 完整预测流程 | [覆盖率修复评测](forecasting/2026-10-06-coverage/report.md)、[多臂评测](forecasting/2026-10-07-multi-arm/report.md)、[真实 HTTP E2E](forecasting/2026-10-07-live-e2e/report.md) |
| 软件验证 | [历史验证记录](validation/2026-09-30/README.md)、[本地模型结果](validation/local-model/)、[本次清理验证](validation/2026-10-09-cleanup/verification.json) |

原始 JSON 和日志保持生成时内容，包括当时的角色简称、提示版本和路径；当前工具及文档链接使用整理后的路径。v1 重复的 27 个数据文件已统一到 `v1-2026-10-04/`，两份不同的补充说明分别保留为 [说明](v1-2026-10-04/integration-readme.md) 和 [报告](v1-2026-10-04/integration-report.md)。固定输出、模拟审阅者和真实模型结果仍沿用各报告中的明确标注，不能混作真实质量结论。

## 背景（协作者速览）

**被评测的系统**：ForecastLab，一条"证据优先"的二元事件预测流水线，一次运行分六个阶段——
规范化问题与检索词 → 整理证据（带逐字引用）→ 构建世界状态（主体、假设）→ 主体行动推演两轮 →
审查（找漏洞并决定概率依据）→ 报告（给出是/否概率）。

**评测方式**：准备一批结果已经确定的二元问题，让系统站在每个问题的**信息截止日**（`as_of`）
"只看当天及之前的资料"给出概率，再与真实结果比对，用 Brier 与覆盖率打分。三个评测臂：
`full`（完整六阶段）、`single_agent`（问题 + 同一份证据，一次调用）、`no_evidence`（不给证据）。

**为什么分三轮**：

- **v1**：24 个案例中有相当一部分结果发生在模型训练数据截止（约 2025-05）之前，
  模型可能"背出"答案，评测存在泄漏 → 只作为早期基线保留。
- **v2**：把 `as_of` 全部推到截止之后（≥ 2025-07-01），并配平正负例，得到一套无泄漏的 20 案例套件。
- **v3v4**：在 v2 套件上深入回答四个问题——证据包做厚有没有用、让最终概率使用"世界建模 + 推演"有没有用、
  流水线为什么几乎不走全依据、运行方差有多大。

**v3v4 的一句话结论**：加厚证据没有可测收益；全依据不比仅证据更准；把全依据通道关死的审查规则是
有测试保护的设计契约而非缺陷；而运行方差大到足以推翻前两类"改善"。详细问题描述与实测数据见
[`v3v4-evidence-and-gates-2026-10-08/README.md`](v3v4-evidence-and-gates-2026-10-08/README.md)
与其中的 `report.md`。

## 结构

```text
experiment/
├─ README.md                       # 本文件：各轮总览
├─ validate_suite.py               # 通用离线校验器
├─ v1-2026-10-04/                  # 第一轮：24 案例（含历史事件，存在训练数据泄漏）
├─ v2-postcutoff-2026-10-07/       # 第二轮：20 案例（全部在训练数据截止之后）
└─ v3v4-evidence-and-gates-2026-10-08/   # 第三、四轮合并：证据质量 · 影子全依据 · 闸门 · 温度
   ├─ README.md  report.md
   ├─ suite/forecastlab-v3v4.json
   ├─ suite/cases/*.json
   └─ results/*.json
```

## 各轮对比

| | v1-2026-10-04 | v2-postcutoff-2026-10-07 | v3v4-evidence-and-gates-2026-10-08 |
| --- | --- | --- | --- |
| 案例数 | 24 | 20（10 正 + 10 负） | 同 v2 |
| 证据条目 | 49 | 46 | 67（加厚） |
| 覆盖窗口 | 2024-01 ~ 2026-06 | 全部 ≥ 2025-07-01 | 全部 ≥ 2025-07-01 |
| 与训练数据（截止约 2025-05） | **部分重叠 → 有泄漏** | 全部在其之后 | 全部在其之后 |
| 新增机制 | — | 正负配平、无泄漏套件 | 加厚证据、影子全依据、温度固定、闸门 A/B/C/D 对照 |
| 用途 | 早期基线、流程验证 | 无泄漏的可信评测 | 回答"证据、全依据、闸门、方差"四个问题 |

## v3v4 结果（2026-10-08，六轮 × 20 案例）

| 轮次 | 设置 | full 覆盖率 | full Brier（弃权回退） | full BSS | 以 full 依据出概率 |
| --- | --- | --- | --- | --- | --- |
| v3 基线 | 加厚 + 影子（温度默认） | 0.70 | 0.161 | +0.507 | 1 / 20 |
| v4-A | + `temperature=0` | 0.70 | 0.165 | +0.486 | 0 / 20 |
| v4-B | A + ①②③ | 0.80 | 0.148 | +0.508 | **15 / 20** |
| v4-C | A + ②（当前代码） | 0.70 | 0.160 | +0.516 | 0 / 20 |
| v4-D | C 的薄证据包对照 | — | — | — | — |
| v5 当前代码 | 移除 `temperature=0` 后复跑（= 当前代码） | 0.70 | 0.158 | +0.526 | 2 / 20 |

四条结论：

- **加厚证据没有可测收益**：固定温度下 `single_agent` 薄包 0.1130 优于厚包 0.1217（对照臂仅漂移 0.002）。
- **全依据不比仅证据更准**：影子成对 0.1181 vs 0.1143；通道打开后 full 0.148 vs single 0.122 仍持平。
- **闸门是设计契约**：② 毫无影响；真正的闸门 `future_gap → evidence_only` 有测试保护，故 ①③ 已回退。
- **方差主导**：`temperature=0` 改善但未消除（该改动后已移除，v5 复跑确认结论不变）；
  `no_evidence` 臂在 C 轮（固定温度）0.135 与 v5 轮（默认温度）0.201 之间的差距即漂移量级，
  小于约 0.04 的差异不可解读。

完整报告（含 `temperature` 说明、环节诊断、逐案例表）见
[`v3v4-evidence-and-gates-2026-10-08/report.md`](v3v4-evidence-and-gates-2026-10-08/report.md)。

## v2 结果（2026-10-07，20 案例）

| 臂 | 覆盖率 | Brier（有效） | Brier（弃权按 0.5） | BSS vs 0.5 |
| --- | --- | --- | --- | --- |
| full（六阶段框架） | 0.75 | 0.148 | 0.174 | +0.406 |
| single_agent + 同证据 | 1.00 | 0.162 | 0.162 | +0.350 |
| no_evidence（仅模型知识） | 1.00 | 0.168 | 0.168 | +0.327 |

`no_evidence` 与两个带证据的臂接近，原因是套件中相当一部分事件可由截止前的公开日程推断。
完整分析见 `v2-postcutoff-2026-10-07/report.md`；评分口径（覆盖率 / Brier / 弃权回退 Brier / BSS）
见该文件夹 README 的"评分口径与含义"一节。

## 代码改动汇总

| 文件 | 改动 | 状态 |
| --- | --- | --- |
| `backend/app/llm.py` | 对 `deepseek*` 关闭 thinking；`evidence`/`review`/`forecast` 的 `max_tokens` 提到 8000 | 保留 |
| `backend/app/llm.py` | 固定 `temperature=0` | **已移除**（实验期对照手段） |
| `backend/app/graph.py` | `finding_evidence_map` / `resolve_refs`：`F` 编号映射回证据编号，坏引用丢弃而不中断整轮 | 保留 |
| `backend/app/graph.py` + `config.py` | 影子全依据预测，由 `FORECASTLAB_SHADOW=1` 控制，默认关闭 | 保留 |
| `backend/app/schemas.py`、`eval/run_suite.py` | `shadow_forecast` 字段与 `shadow_full_p` 列 | 保留 |
| `backend/app/graph.py` | ② `unsupported_claims` 改记 medium 级、不再自动 blocked | 保留 |
| `backend/app/graph.py` | ①③ `world.summary` 的 future_gap 检测与复审触发 | **已回退**（违反 `test_backtest.py` 固化的设计契约） |

## 离线校验

```powershell
cd D:\GCB_Study\0code\forecastlab
python experiment/validate_suite.py     # 校验本目录下所有套件
```

校验内容：`outcome ∈ {0,1}`、`resolve_by > as_of`、证据包存在且每条 `published_at` ≤ `as_of`、
同包内 `source_url` 不重复、每包 ≥ 2 条证据；对声明了 `frozen.cutoff_basis` 的套件额外要求
`as_of ≥ 2025-07-01`。

## 运行

```powershell
$env:FORECASTLAB_SHADOW='1'    # 影子预测默认关闭，需要时开启

conda run --no-capture-output -n forecastlab python eval/run_suite.py `
  experiment/v3v4-evidence-and-gates-2026-10-08/suite/forecastlab-v3v4.json `
  --out data/eval/v3v4.json

conda run --no-capture-output -n forecastlab python eval/score_table.py `
  data/eval/v3v4.json --group-by category
```

> 说明：v1 的套件同时存在于仓库根目录的 `eval/suites/forecastlab-v1.json`（评测代码的规范位置），
> `v1-2026-10-04/suite/` 是当时的提交快照。
