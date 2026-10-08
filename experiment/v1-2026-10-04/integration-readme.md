# ForecastLab 实验提交包（2026-10-04）

本文件夹保存本次实验的**报告、套件、证据包与结果**，是可独立打包提交的快照。实验代码不复制到这里，原因见第 4 节。

## 1. 本轮做了什么

1. 把评测案例从 2 个扩到 24 个，覆盖科技 7、体育 6、公共 6、财经 5；结算结果为 14 个"是"、10 个"否"。
2. 补齐 C03–C20 共 18 个冻结证据包，每个案例 2–4 条信息截至日之前的来源。
3. 统一判定口径为**实际动作口径**：开售、出货、客户部署、正式发布到 npm、实际比赛结果、官方文本实际通过、会议实际举行才算数，发布会与新闻稿不作为结算依据。
4. 替换全部模糊定义："新一代旗舰模型""下一代 GPU 架构""某主流框架""逐步退出化石燃料""创同期新高"等改为可客观结算的问题。
5. 运行 24 案例 × 3 臂对照实验，产出结果表与评分汇总。
6. `main` 中原有文件未做任何修改，本实验涉及的全部改动均为新增文件。
7. 合并 main 最新提交（PR #1，问题分析与证据评估 问题定义与证据研究）后，用新流程重跑同一套件，产出第二组结果并保留对照。

## 2. 文件夹内容

| 路径 | 内容 |
| --- | --- |
| `report.md` | 实验报告（引言、方法、细节、结果与分析、结论） |
| `suite/forecastlab-v1.json` | 24 案例实验套件：问题、信息截至、结算规则、结果、证据引用 |
| `suite/cases/` | 证据包与案例定义，共 49 个 JSON |
| `results/v1-2026-10-04.json` | 原始结果表：每案例三臂概率、调用次数、token、耗时、错误 |
| `results/v1-2026-10-04-score.json` | 评分汇总：覆盖率、Brier、弃权回退 Brier、两套 BSS、分类分层 |
| `results/v1-after-question-evidence.json` | 合并 main（问题分析与证据评估）后重跑的原始结果表 |
| `results/v1-after-question-evidence-score.json` | 第二次运行的评分汇总 |
| `README.md` | 本文件 |

C01、C02 的证据包沿用仓库 `examples/` 下已有文件，未在本文件夹内重复存放。

## 3. 涉及的新增文件（提交清单）

本次改动全部为新增文件，`main` 中原有文件未修改。涉及路径如下：

| 路径 | 说明 |
| --- | --- |
| `experiment/v1-2026-10-04/` | 本提交包：报告、套件、证据包、结果、README |
| `eval/suites/forecastlab-v1.json` | 24 案例实验套件 |
| `eval/cases/` | 案例定义与证据包（49 个 JSON） |
| `eval/run_suite.py` | 套件运行器：执行三臂并写出结果表 |
| `eval/single_agent.py` | 单 Agent 预测器：供"有证据""无证据"两臂共用 |
| `eval/score_table.py` | 评分器：覆盖率、Brier、BSS、分层 |
| `eval/validate_cases.py` | 案例与证据的离线校验 |
| `backend/tests/test_eval_score.py` | 评分与套件解析的离线单元测试 |

生成物（不提交，可用 `data/eval/v1-2026-10-04.json` 重新生成）：`data/` 下的运行结果、运行快照 SQLite 与阶段快照。不要提交 `.env`、`data/` 与 `local_docs/` 调研笔记。

## 4. 如何复现

实验代码保留在仓库原路径，不随本文件夹复制。原因是 `eval/run_suite.py` 与 `eval/single_agent.py` 通过脚本自身位置解析仓库根目录和 `backend/` 包（`Path(__file__).resolve().parents[1] / "backend"`）；把这些脚本复制到其他目录后，导入会失败，命令无法直接运行。因此复现时必须在仓库根目录 `forecastlab/` 下、使用原路径执行：

```powershell
cd D:\GCB_Study\0code\forecastlab

conda run --no-capture-output -n forecastlab python eval/run_suite.py eval/suites/forecastlab-v1.json --out data/eval/v1-2026-10-04.json --dry-run

conda run --no-capture-output -n forecastlab python eval/run_suite.py eval/suites/forecastlab-v1.json --out data/eval/v1-2026-10-04.json

conda run --no-capture-output -n forecastlab python eval/score_table.py data/eval/v1-2026-10-04.json --group-by category
```

离线校验（不调用模型）：

```powershell
conda run --no-capture-output -n forecastlab python -m pytest -q backend/tests/test_eval_score.py
python eval/validate_cases.py
```

本文件夹中的 `suite/`、`results/` 与仓库路径的对应关系：`suite/forecastlab-v1.json` ↔ `eval/suites/forecastlab-v1.json`，`suite/cases/` ↔ `eval/cases/`，`results/` ↔ `data/eval/`。要用本文件夹的数据复现，把 `suite/` 与 `results/` 放回上述位置即可，代码仍在仓库原路径。

## 5. 关键结果

| 臂 | 覆盖率 | Brier（已答） | 弃权回退 Brier | BSS vs 0.5 |
| --- | --- | --- | --- | --- |
| 恒定 50%（参考） | 1.0 | 0.2500 | 0.2500 | 0 |
| 完整框架 | 0.417（10/24） | 0.2427 | 0.2470 | +0.029 |
| 单 Agent + 同证据 | 1.0 | 0.1791 | 0.1791 | +0.284 |
| 单 Agent 无证据 | 1.0 | 0.2582 | 0.2582 | −0.033 |

运行代价：完整框架 169 次调用、391,451 token、441.1 秒；两个单 Agent 臂各 24 次调用。总墙钟约 8.5 分钟。

结论要点：完整框架当前的主要短板是覆盖率（41.7%），弃权回退后相对 50% 参考线的优势接近消失；证据包带来明确收益（单 Agent Brier 从 0.2582 降到 0.1791）；无证据臂接近随机。

第二次运行（合并 问题分析与证据评估 后，2026-10-04 14:43–14:51 UTC）：

| 臂 | 覆盖率 | Brier（已答） | 弃权回退 Brier | BSS vs 0.5 |
| --- | --- | --- | --- | --- |
| 恒定 50%（参考） | 1.0 | 0.2500 | 0.2500 | 0 |
| 完整框架 | 0.167（4/24） | 0.3344 | 0.2641 | −0.337 |
| 单 Agent + 同证据 | 1.0 | 0.1736 | 0.1736 | +0.306 |
| 单 Agent 无证据 | 1.0 | 0.2311 | 0.2311 | +0.076 |

两次运行对比：

| 指标 | 合并前（bb4d339） | 合并后（问题分析与证据评估） |
| --- | --- | --- |
| 完整框架覆盖率 | 41.7%（10/24） | 16.7%（4/24） |
| 完整框架弃权回退 Brier | 0.2470 | 0.2641 |
| 完整框架 BSS vs 0.5 | +0.029 | −0.337 |
| 单 Agent + 同证据 Brier | 0.1791 | 0.1736 |
| 单 Agent 无证据 Brier | 0.2582 | 0.2311 |
| 总模型调用 / token | 217 / 430,305 | 196 / 501,618 |

合并 问题分析与证据评估 后，完整框架的弃权从 14/24 增加到 20/24，分数由略高于参考线转为明显低于参考线；单 Agent 两臂变化很小。两轮都是单次运行，温度未固定，因此该差异包含流程改动与运行波动两部分，不能单独归因。

## 6. 数据口径与已知缺口

| 项目 | 说明 |
| --- | --- |
| 判定口径 | 实际动作，不使用声明或预告 |
| 证据日期 | C03–C20 部分来源页面未标注发布日，`published_at` 留空 |
| 次级来源 | C06、C07、C10、C11、C12、C14、C16、C18、C20、C24 使用媒体或行情站结算，已在案例 `flags` 标注 |
| 回看偏差 | 全部案例为历史事件，无证据臂只能探测、不能根除 |
| 运行轮次 | 单轮；温度沿用接口默认值，未做重复性实验 |
| 人工核查 | 引用支持率未标注 |
| 代码版本 | 第一次运行基于 `bb4d339`；第二次运行合并了 main 的 问题分析与证据评估（`3c56e50`） |

## 7. 与仓库主干的关系

- 分支：`test/add_eval_sample`，基于提交 `bb4d339`（与 `main` 相同）。
- `main` 中原有文件零改动；本实验涉及的全部产物均为新增文件。
- 第二次运行前已将 `origin/main`（含 PR #1 问题分析与证据评估）合并进本分支，合并提交 `f3e5c62`；两轮结果都保留在 `results/` 下以便对照。
- 提交命令：

```powershell
cd D:\GCB_Study\0code\forecastlab
git add experiment/v1-2026-10-04
git add eval/cases eval/suites eval/run_suite.py eval/single_agent.py eval/score_table.py eval/validate_cases.py
git add backend/tests/test_eval_score.py
git commit -m "Add 24-case multi-arm evaluation package"
```
