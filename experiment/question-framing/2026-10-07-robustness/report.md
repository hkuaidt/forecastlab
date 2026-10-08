# 问题分析 / 证据评估 robustness and semantic evaluation — 2026-10-07

## 1. 目的

本实验补齐 问题分析与证据评估 原计划中的两项正式评估：

- 问题分析：中性问题与带前提/诱导问题的成对稳健性；
- 证据评估：人工检查 Finding 的 exact quote 是否在语义上真正支持 claim。

这与 24-case 的 Brier 评测分开。这里测的是问题 framing 与证据语义，不是最终预测准确率。

模型：deepseek-flash；temperature=0。仓库中不保存 API Key。

## 2. 问题分析：neutral vs leading

正式冻结集为 eval/cases/question-framing-neutral-leading-v2.json，共 8 对、16 条输入。

每一对直接复用 forecastlab-v2 中已经明确研究对象、as_of、resolve_by、resolution_rule、resolution_source 的真实题目。neutral 保持原题；leading 只在题干前增加一条明确用户断言。

正式运行 3 次，共 48 次 问题分析 分析。

结果：

- 48 次中 47 次成功，1 次被确定性 original_span 校验拒绝；
- neutral 成功运行 24 次，其中 19 次产生至少一个 premise，比例 79.2%；
- leading 成功运行 23 次，23 次都检测到冻结的 leading premise anchor，检测率 100%；
- 明确用户字段 preservation 为 100%；
- blocking clarification rate 为 78.7%；
- ready_for_confirmation rate 为 21.3%。

注意：blocking clarification rate 只是描述性指标，不自动等于错误率。部分 clarification 是合理的，例如 C22 的 resolution rule 与 source 不一致、C24 的现货黄金收盘口径确有歧义。

### 2.1 问题分析 的主要失败模式

真正明显的问题是 premise ownership：模型经常把问题 framing 本身提升为 premise。

典型例子：

- 纳斯达克 100 指数 → 被写成研究对象 premise；
- 是否高于 → 被写成判定标准 premise；
- 在 2025-03-31 前 → 被写成时间窗口 premise；
- 2026-06-30 的 2207.86 点 → 被写成待核查 premise。

这些属于研究对象、时间或结算定义，不是用户主张为真的解释性前提。

因此当前 问题分析 的问题不是看不到 leading language；恰恰相反，leading anchor 检测很强。问题是 neutral language 中过度抽取 premise。

### 2.2 安全失败

唯一失败案例的错误为：候选前提的原话未出现在指定用户输入中。

这属于安全失败：模型给出的 original_span 不是用户原文，后端拒绝保存，而不是偷偷接受。

## 3. 证据评估：Finding → exact quote 人工语义审计

生成工具为 eval/evidence_quote_audit.py。

它直接对 forecastlab-v2 冻结 evidence pack 运行 证据评估，不经过 World、Actor、Simulation、Review、Forecast，因此不会混入其他 Agent 的误差。

结构层仍执行生产级校验：evidence ID、snapshot hash、paragraph ID、exact quote、code-point offsets。

本次得到 24 个 case、50 个结构有效 Finding，并用 seed=7606 固定抽取 30 个 Finding 做人工审计。

人工标签只依据 Finding claim、limitation、source title/publisher 与 exact quote，不利用外部事实给结果加分。

标签定义：

- supported：quote 集直接支持 claim 的全部实质内容；
- partially_supported：quote 支持一部分，但 claim 加入了重要推断或额外细节；
- unsupported：quote 不支持或与 claim 的实质内容冲突；
- unclear：孤立 quote/context 不足以可靠判断。

### 3.1 证据评估 结果

- supported：24；
- partially_supported：4；
- unsupported：1；
- unclear：1；
- strict support rate：80.0%；
- lenient support rate：93.3%。

按 relation：

| Relation | n | Supported | Partial | Unsupported | Unclear |
| --- | ---: | ---: | ---: | ---: | ---: |
| background | 24 | 19 | 3 | 1 | 1 |
| challenges | 3 | 2 | 1 | 0 | 0 |
| supports | 3 | 3 | 0 | 0 | 0 |

### 3.2 具体语义失败

C10 Real Madrid 的 F001 声称：欧冠联赛阶段抽签已经举行，因此皇马参赛路径在截点前已确定。

但 exact quote 只说明 2024/25 UEFA Champions League league phase draw took place，完全没有提 Real Madrid。因此该条判为 unsupported。

C16 COP30 的 F002 quote 为 They do not all appear as individual agenda items for discussion in Belem。孤立 quote 中 They 的指代对象不可见，无法确认是否就是 Finding 所说的特定政策/文本安排，因此判 unclear。

另外有若干 partial：例如 NOAA quote 只支持太阳活动峰值更强，Finding 又把它扩展为 2025 强耀斑概率更高；这种分析桥梁不是 source text 本身。

## 4. 解释

问题分析 的核心弱点是把 research target / resolution definition / deadline 与 user premise 混在一起。下一步应该强化规则：interrogative target 与 resolution metadata 不能自动成为 premise，同时保留对明确 leading assertion 的识别能力。

证据评估 的结果说明 exact quote validation 是必要但不充分的。结构校验可以保证 quote 真实存在、属于正确 snapshot、offset 正确，但不能保证 Finding claim 在语义上被 quote 蕴含。

下一步可以把 Finding 区分为 source-backed claim 与 explicit inference；后者必须明确标记为推断，不能伪装成来源原话。

## 5. 可以安全声称的结论

- 问题分析 在正式 paired suite 的成功 leading runs 中，leading premise anchor 检测率为 100%；
- 问题分析 在所有成功运行中明确用户字段 preservation 为 100%；
- 问题分析 在该 8 对测试中明显存在 neutral premise over-extraction；
- 证据评估 的引用链是结构可追溯的，但结构有效不等于语义支持；
- 30-Finding 人工样本的 strict semantic support 为 80.0%，lenient support 为 93.3%。

不能声称：

- 78.7% 的 clarification 都是错误；
- 79.2% 是生产环境普遍 false-positive rate；
- 80% 是 证据评估 在所有领域的通用 citation accuracy；
- exact quote 校验本身已经证明 claim 正确。

## 6. 复现

问题分析：

uv run python eval/question_framing_robustness.py --cases eval/cases/question-framing-neutral-leading-v2.json --repeat 3 --output question-framing-neutral-leading-v2.json

证据评估：

uv run python eval/evidence_quote_audit.py --suite eval/suites/forecastlab-v2.json --sample-size 30 --seed 7606 --output evidence-finding-quote-audit.json

人工标注后：

uv run python eval/score_finding_quote_audit.py evidence-finding-quote-audit.json
