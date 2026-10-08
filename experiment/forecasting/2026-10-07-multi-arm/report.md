# ForecastLab v2 多臂评测分析

**日期：2026-10-07**

## 1. 这轮实验要回答什么

前一轮在 问题分析与证据评估 合并后出现明显回归：完整框架 24 个案例只有 4 个输出概率，13 个案例直接因为 F-ID contract 崩溃。

本轮先修复工程契约和评测输入，再回答三个问题：

1. F-ID hard failure 是否消失？
2. Full pipeline 的 coverage 和预测质量是否恢复？
3. Full pipeline 相比 Single Agent + 同证据，是否真的带来预测收益？

系统“不再崩”不等于“预测更准”，因此三件事必须分开评价。

## 2. 实验控制

正式结果绑定远端提交 71a95d1188df1733a7eb785ad615dea09da4816f，对应 Git tree 99339204249e2cf7e1ea8fa65eacbf0888edd71a。

使用 forecastlab-v2，24 个历史已结算 binary cases。v2 相比旧 v1 的主要作用是修复评测本身：

- F Finding 在跨阶段使用前必须通过服务器 exact quote / snapshot validation；
- historical evidence 使用 cutoff-clean v2 packs；
- C05 把预测截点移动到 Node.js 22 实际进入 LTS 之前，消除 outcome leakage；
- C01 用 Wayback capture 和固定 Git commit 作为不可变 cutoff proof；
- validator 不接受任意 retrieved_at 冒充历史可得性证明。

离线 validator 结果为 24/24 cutoff-ready。

模型为 deepseek-flash，temperature=0，同时关闭 DeepSeek thinking 路径用于结构化 Agent JSON 输出。

即使 temperature=0，也不能认为远端 LLM 完全确定性；本报告仍只是一轮运行。

## 3. 总体结果

| 方法 | Coverage | Brier（有效预测） | 弃权回退 Brier | BSS vs 0.5 |
| --- | ---: | ---: | ---: | ---: |
| Full pipeline | 75.0% | 0.1337 | 0.1628 | +0.465 |
| Single Agent + 同证据 | 100% | 0.1443 | 0.1443 | +0.423 |
| Single Agent 无证据 | 100% | 0.1881 | 0.1881 | +0.247 |
| 恒定 50% | 100% | 0.2500 | 0.2500 | 0 |

表面看，Full 的已答 Brier 0.1337 低于 Single Agent 的 0.1443。

但这个比较不公平，因为 Full 自己挑掉了 6 个案例。

## 4. 相同 18 个案例上的公平比较

只看 Full 实际回答的 18 个案例：

| 方法 | 同一 18-case Brier |
| --- | ---: |
| Full pipeline | 0.1337 |
| Single Agent + 同证据 | 0.1061 |
| Single Agent 无证据 | 0.1658 |

因此本轮 Full pipeline 在相同已答样本上没有超过 Single Agent + 同证据。

Full 放弃的 6 个案例上，Single Agent 的 Brier 为 0.2591，略高于恒定 0.5 的 0.25。这说明 abstention 确实筛出了一批相对难题，但这种选择性弃权带来的收益不足以抵消 Full 在其余 18 个案例上的预测退化。

最终：

- Full fallback Brier：0.1628
- Single Agent Brier：0.1443

所以当前完整流程在包含 coverage 惩罚的整体指标上仍落后于 Single Agent + 同证据。

## 5. F contract 修复是否成功

这部分结论明确：成功。

旧 问题分析与证据评估 合并后：

- completed：4
- insufficient_evidence：7
- failed：13

新 v2 run：

- completed：18
- insufficient_evidence：6
- failed：0

旧 13 个 hard failure 的迁移：

- 8 个变为 completed
- 5 个变为 insufficient_evidence
- 0 个仍然 hard fail

原来的典型错误包括“审查意见引用不存在：F001”“审查意见引用不存在：F001, F002”“假设引用不存在：F001”，本轮均未再出现。

同时 24/24 case 均记录 full_findings_validated=true。

因此现在的 F 可以作为中间 provenance 节点进入 World/Review，但不能绕过底层 E/quote 校验。

## 6. 更重要的新问题：Full pipeline 实际没有使用 Full probability basis

本轮 Review 状态：

| Review status | 数量 |
| --- | ---: |
| blocked | 23 |
| qualified | 1 |
| passed | 0 |

最终 probability basis：

| Basis | 数量 |
| --- | ---: |
| evidence_only | 18 |
| none | 6 |
| full | 0 |

这意味着 Full pipeline 虽然运行了 World、Actor、Simulation、Review，但没有一个最终概率真正采用完整模拟链作为概率依据。

绝大部分路径实际是：

Evidence → World / Simulation → Review 发现 unsupported / overreach → blocked → Evidence-only audit → evidence-only forecast。

因此当前 Full 更接近“昂贵的多阶段自我检查 + 最后退回 Evidence-only forecast”，而不是“多 Agent 模拟改善了最终概率”。

这是本轮最重要的架构发现。

## 7. 成本

| Arm | Calls | Tokens | Model seconds |
| --- | ---: | ---: | ---: |
| Full | 185 | 670,815 | 468.59 |
| Single Agent + evidence | 24 | 31,384 | 28.22 |
| No evidence | 24 | 11,988 | 25.40 |

Full 相对 Single Agent + evidence：

- calls 约 7.7 倍；
- tokens 约 21.4 倍；
- model time 约 16.6 倍。

当前这些额外成本没有换来更低的 matched-subset Brier，也没有换来 100% coverage。

## 8. Evidence 的贡献仍然成立

Single Agent：

- with evidence：Brier 0.1443
- without evidence：Brier 0.1881

同一模型、同一问题、temperature=0 下，有证据明显优于无证据。

所以本轮支持“Evidence 有价值”，但尚不支持“多阶段 Agent simulation 比单 Agent + 同证据更准”。

## 9. 分类结果

### Finance

- Full fallback：0.2194
- Single：0.2014
- No evidence：0.1340

该层只有 5 个案例且 outcome 偏向“是”，无证据模型的偏高概率碰巧占优，不能据此判断证据有害。

### Public

- Full fallback：0.0796
- Single：0.0357
- No evidence：0.2119

证据贡献明显，但 Single Agent 仍优于 Full。

### Sport

- Full fallback：0.1617
- Single：0.1691
- No evidence：0.1467

Full 相比 Single 略好，但样本只有 6 个。

### Tech

- Full fallback：0.1945
- Single：0.1754
- No evidence：0.2420

证据有效，Full 未超过 Single。

## 10. 六个 abstention

本轮 Full 未给概率：

- C02-star50
- C08-typescript6-release
- C10-real-madrid-ucl
- C12-wimbledon-topseed
- C18-xflare-2025
- C24-gold-q3-2026

共同特点不是程序错误，而是 evidence-only audit 最终没有批准概率。

已有 Review 诊断显示主要问题包括：

- 把预测期结果未知错误地当作需要证据的缺口；
- 背景证据与目标事件之间缺少量化桥梁；
- World/Simulation 把有限证据推成方向性判断；
- 缺少目标问题所需的关键先验或规则信息；
- 历史练习证据虽然 v2 已做 cutoff-clean 校验，但运行时仍被标记为 historical exercise / non-blind。

当前结果文件没有持久化 EvidenceOnlyAudit.blocking_reasons，因此下一步应先补这个诊断字段，再只重跑这 6 个案例。

## 11. 下一步不应该做什么

不应该直接把 blocked 改成 qualified，也不应该遇到 evidence 就强制给概率。

这样虽然 coverage 会涨，但可能破坏当前最有价值的 provenance 和 abstention safety。

更合理的顺序是：

1. 持久化 evidence-only audit 的 can_estimate / blocking_reasons；
2. 只重跑 6 个 abstention；
3. 区分真正缺少可用事前证据、Review/Prompt 把未来结果误当证据缺口、World/Simulation 自己制造 unsupported claims；
4. 优先减少 World/Simulation overreach，而不是放宽 validator；
5. 对不适合战略主体推演的问题考虑直接走 evidence-only branch，避免无效的 7 倍到 20 倍成本。

## 12. 局限

- 只有一轮正式 v2 运行；
- 虽然 temperature=0，远端模型仍可能存在非确定性；
- v2 是历史回看套件，不是严格实时盲测；
- v2 对多条历史来源做了 cutoff metadata / immutable-proof 整理，与旧 v1 输入并非完全相同；
- 因此 v1 到 v2 的改善不能单独归因于 F contract；
- 24 case 样本量仍小；
- 没有人工标注 finding 到 quote 的语义支持率；
- 问题分析 neutral vs leading evaluation 仍需单独完成。

## 13. 当前可以安全声称的结论

可以声称：

修复后，旧 13 个 F-ID hard failure 在本轮全部消失，Full coverage 从旧运行的 16.7% 恢复到 75%，且所有 证据评估 findings 均通过服务器引用校验。

可以声称：

在 v2 同一套问题上，Single Agent + evidence 的 Brier 为 0.1443，优于 no-evidence 的 0.1881，说明 evidence 对本套件有实际贡献。

可以声称：

Full pipeline 当前成本显著高于 Single Agent，并且在相同 18 个已答案例上的 Brier 为 0.1337，高于 Single Agent 的 0.1061，因此尚不能证明多阶段 simulation 提升预测准确性。

不能声称：

- Full pipeline 已经优于 Single Agent；
- coverage 的全部提升都由 问题分析与证据评估 / F-ID 修复造成；
- 当前概率已经校准；
- 结果可以直接外推到真实未来预测。


## 15. Future-outcome audit sanitation：Run 2

在 Run 1 的 abstention 诊断中，EvidenceOnlyAudit 多次把“未来结算期结果尚未发生”当成不能预测的理由，例如要求未来月末收盘价、最终冠军或决赛结果。

本轮没有放宽 Review 全局策略，而是做了确定性语义修复：

1. 明确要求 `as_of` 之后的实际结果/冠军/收盘价，不再作为事前证据 blocker；
2. 已由离线 validator 验证 cutoff provenance 的评测证据，不再仅因 `exercise / historical / non-blind` 元数据被阻断；
3. 来源权威性、口径、参赛/种子规则、与目标事件相关性等**实质性事前缺口仍然保留**；
4. 原始 audit 判断、有效 blocker 和被丢弃的非 blocker 分开保存，便于审计。

最终修复提交：

```text
9aaa5701f3afaca08b9cbb094dfb8e225318fc5c
fix: keep source-quality audit blockers
```

### 15.1 Run 2 结果

| 指标 | 正式 Run 1 | Run 2 |
| --- | ---: | ---: |
| completed | 18 | 21 |
| insufficient_evidence | 6 | 3 |
| failed | 0 | 0 |
| coverage | 75.0% | **87.5%** |
| Brier（仅已答） | **0.1337** | 0.1578 |
| Brier（abstain→0.5） | **0.1628** | 0.1694 |
| model calls | 185 | 190 |
| tokens | 670,815 | 682,477 |

Run 2 的 coverage 明显提高，但 Brier 反而变差。

因此：

> 更高 coverage 不等于更好的预测系统；当前 abstention 本身具有一定保护价值。

### 15.2 Coverage 状态变化

| Case | Run 1 | Run 2 | Run 2 概率 |
| --- | --- | --- | ---: |
| C02-star50 | abstain | completed | 0.55 |
| C08-typescript6-release | abstain | completed | 0.45 |
| C16-cop30-roadmap-adopted | completed | abstain | — |
| C18-xflare-2025 | abstain | completed | 0.70 |
| C24-gold-q3-2026 | abstain | completed | 0.50 |

这同时说明：即使 `temperature=0`，远端模型仍存在运行级非确定性。C16 在 Run 1 有概率，Run 2 却正常 abstain；C18 则相反。

### 15.3 共同回答案例的 matched comparison

Run 1 与 Run 2 共同回答 17 个案例。

| 方法 | 17-case matched Brier |
| --- | ---: |
| Full Run 1 | 0.1379 |
| Full Run 2 | 0.1394 |
| Single Agent + 同证据 | **0.1119** |

因此，即使去掉 coverage 差异，Full pipeline 仍没有超过 Single Agent + evidence。

这比单看总体 Brier 更重要，因为它控制了“Full 只回答较容易案例”的选择效应。

### 15.4 Run 2 仍然 abstain 的 3 个案例

**C10 Real Madrid UCL**

有效 blocker：

- 缺少皇马在该赛季欧冠中的参赛、晋级或竞争力证据；
- 证据主要是赛制/抽签标题性材料，不能支持冠军概率。

未来决赛结果未发生这一理由已被正确剔除。

**C12 Wimbledon top seed**

有效 blocker：

- 只有 ATP 排名；
- 缺少温网官方 1 号种子标注/种子规则信息。

未来冠军结果未发生这一理由已被正确剔除。

**C16 COP30 roadmap**

有效 blocker：

- 只有主席国倡议/磋商材料；
- resolution rule 要求 UNFCCC cover decision；
- 缺少官方决定文本或截点前草案/谈判文本。

这些都是真正的事前 evidence gap，因此继续 abstain 是合理行为。

### 15.5 Audit override

Run 2 有两个案例的模型原始 EvidenceOnlyAudit 返回 `can_estimate=false`，后端根据确定性规则纠正：

- C02-star50 → completed，p=0.55；
- C24-gold-q3-2026 → completed，p=0.50。

其中 C02 的原始 blocker 只是在要求 7 月 31 日未来行情，因此纠正合理。

C24 更能说明模型非确定性：一次 targeted rerun 中，模型额外指出“次级来源、非 LBMA 官方日价、基准口径无法交叉核验”，系统因此仍然 abstain；而完整 Run 2 中模型没有提出这条来源质量 blocker，于是后端只看到未来结果型理由并允许给出 0.50。

因此当前 audit 仍不能被视为确定性质量门。

### 15.6 Run 2 后的结论

当前可以安全声称：

1. F-ID contract hard failure 已消失，工程稳定性恢复；
2. cutoff provenance 和 future-outcome 语义已经由后端确定性规则约束；
3. evidence 对本套件有明确价值；
4. 继续降低 abstention 并没有改善 Brier；
5. Full 多阶段 pipeline 在 matched case 上仍落后于 Single Agent + evidence；
6. 当前 Full 的主要价值更像“复杂的审计/安全层”，而不是已经证明能提高预测准确率的 simulation 方法。

下一步不应继续放宽 abstention。

更值得做的是：

- 为非战略型问题增加路由，直接进入 evidence-only forecast，避免无意义 World/Actor/Simulation；
- 为适合战略主体推演的问题保留 full path；
- 单独评估 routing 是否降低 7–20 倍推理成本，同时不损失 Brier；
- 补 问题分析 neutral-vs-leading 和 证据评估 finding→quote 人工语义评估。
