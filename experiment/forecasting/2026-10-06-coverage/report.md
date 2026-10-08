# 完整流程覆盖率回归：F Finding 引用契约修复

日期：2026-10-06
基线：`hkuaidt/forecastlab:main@0d2045d`
开发分支：`YOUessi/forecastlab:fix/full-pipeline-coverage`

## 1. 为什么做这次修复

主分支在合并 问题分析与证据评估 后，24-case 多臂评测的完整流程覆盖率从 41.7%（10/24）降到 16.7%（4/24）。原实验结果保存在：

- `experiment/v1-2026-10-04/results/v1-after-question-evidence.json`
- `experiment/v1-2026-10-04/results/v1-after-question-evidence-score.json`

对第二次运行的 24 个案例重新分类后：

| 状态 | 数量 |
| --- | ---: |
| completed | 4 |
| failed | 13 |
| insufficient_evidence | 7 |

这次修复**只处理 13 个 hard failure**，不把 7 个 `insufficient_evidence` 自动视为错误，也不为了提高 coverage 放宽证据标准。

## 2. 13 个 hard failure 的实际分布

13 个失败全部与 证据评估 的 Evidence Finding ID（`Fxxx`）跨阶段传递有关：

| 错误 | 数量 |
| --- | ---: |
| `审查意见引用不存在：F001` | 7 |
| `审查意见引用不存在：F001, F002` | 3 |
| `审查意见引用不存在：F002` | 1 |
| `假设引用不存在：F001` | 2 |
| 合计 | 13 |

因此这不是“模型突然变差”的证据，而是一个确定的数据契约不一致。

## 3. 根因

证据评估 引入了结构化 Finding：

```text
P（用户前提）
↓
F（Evidence Finding）
↓
E（外部证据）
↓
exact quote / snapshot
```

但是下游原有 validator 仍只认识：

```text
E / H / A / M / S
```

具体有两处断点。

### 3.1 Review → F

Review Agent 会读取 `evidence_assessment.findings`，因此模型自然可能输出：

```json
{
  "affected_ids": ["F001"]
}
```

但 Review validator 的合法 ID 集合没有 `F001`，所以直接抛：

```text
ValueError: 审查意见引用不存在：F001
```

### 3.2 H → F

World Agent 会读取同一份 Evidence Finding，并可能形成：

```text
H001（建模假设）
└── parent_ids: [F001]
      └── citation: E002
```

旧 validator 只允许 assumption 的 parent 为 E/H，因此抛：

```text
ValueError: 假设引用不存在：F001
```

## 4. 修复后的正式契约

本次没有把 F 升级成“外部事实”。契约是：

### F 可以做什么

- Review 的 `affected_ids` 可以引用 F，用来定位“哪条 证据评估 finding 被审查”；
- H 的 `parent_ids` 可以引用 F，表示建模假设通过一条结构化 finding 追溯到外部证据；
- UI 中点击 F 可以看到 finding 的 claim / relation / limitation，并继续点击其底层 E 精确原文。

### F 不能做什么

- F 不是外部证据；
- `world.evidence_refs` 仍只能是 E；
- `ActorProfile.visible_evidence_ids` 仍只能是 E；
- Forecast 的 supporting/opposing claim 仍只能引用 E/H/M/S；
- P（用户前提）仍不能伪装成外部证据。

因此最终链路变成：

```text
Review issue
    ↓
   F001
    ↓
   E002
    ↓
snapshot hash + paragraph + exact quote
```

或者：

```text
H001
 ↓
F001
 ↓
E002
```

## 5. Finding 本身仍需通过后端验证

新增 `validated_finding_ids()`。

一个 F 只有在下面条件满足时才进入下游合法 ID 集：

1. Finding ID 在当前 assessment 内唯一；
2. Finding 至少包含 schema 要求的 citation；
3. 每个 citation 的 `evidence_id` 都属于当前运行实际存在的 E。

所以本次不是“看到 F 就一律放行”。

## 6. 前端行为

Review 中的 `F001` 现在是可检查引用：

1. 点击 `F001`；
2. 打开“证据发现”详情；
3. 显示 finding claim、relation、目标 premise 和 limitation；
4. 显示底层 E 引用；
5. 点击 E；
6. 回到服务器保存的 source snapshot，并按 Unicode code-point offset 高亮 exact quote。

这样后端允许 F 后，前端不会出现“未找到引用”。

## 7. 新增回归测试

### 后端

审查引用已验证发现的回归用例

复现：

```text
Review affected_ids = [F001]
```

修复前稳定失败：

```text
ValueError: 审查意见引用不存在：F001
```

修复后通过。

世界模型假设引用可溯源发现的回归用例

复现：

```text
H001.parent_ids = [F001]
F001 → E002
```

修复前稳定失败：

```text
ValueError: 假设引用不存在：F001
```

修复后通过。

### 浏览器

`test_review_finding_reference_opens_traceable_finding`

验证：

```text
Review F001
→ Finding drawer
→ E001
→ exact quote highlight
```

## 8. 当前回归结果

在本地最新 main 快照上：

- Python/backend：**115 passed**
- Chromium browser：**16 passed**
- TypeScript/Vite production build：**passed**
- `git diff --check`：**passed**

原有两个依赖 warning 仍存在，本次没有把 warning 误报为零。

## 9. 评测诊断也同步增强

旧 `eval/run_suite.py` 只保存：

- probability
- status
- calls / tokens / seconds
- error string

因此对于 `insufficient_evidence`，事后看不到“为什么弃权”。

本次新增以下结果字段，不改变 Brier / coverage 评分口径：

- `full_failed_stage`
- `full_review_status`
- `full_probability_basis`
- `full_review_issues`
- `full_unsupported_claims`
- `full_missing_evidence`
- `full_evidence_findings`
- `full_evidence_gaps`
- `full_rejected_findings`

下一轮 24-case 可以直接分析每个 abstention 的真实原因。

## 10. 现在不能声称什么

当前开发机没有配置 Qwen / DeepSeek API key，所以**尚未用真实模型重跑修复后的 24-case**。

因此目前只能声称：

> 已确定并修复导致旧结果中 13/13 hard failure 的 F-ID contract mismatch，自动化回归全部通过。

不能声称：

- after-fix coverage 已提升到某个数字；
- Brier 已改善；
- 7 个 `insufficient_evidence` 都是误阻断；
- 完整框架已经超过 single-agent baseline。

这些必须在同一冻结套件、同一模型配置下重新运行后才能写。

## 11. 下一步

拿到原评测使用的模型配置后：

```bash
uv run python eval/run_suite.py \
  eval/suites/forecastlab-v1.json \
  --only full \
  --out data/eval/v1-after-f-contract-fix.json
```

先只重跑 full arm，避免重复支付 single-agent 对照成本。

重点看：

1. 13 个原 hard failure 是否全部消失；
2. 它们转为 completed 还是 insufficient_evidence；
3. 7 个原 insufficient_evidence 的 `full_review_issues` / `full_missing_evidence`；
4. 是否存在真正应该保留的 abstention；
5. 再决定是否调整 `blocked / qualified / evidence_only` policy。

只有完成这一步后，才进入第二阶段的 blocking policy 调整。

## 12. 第二个发现：24-case 的历史证据日期覆盖不完整

修 F-ID contract 后，对 24-case 的冻结证据包做了不调用模型的静态检查。

结果：

| 证据日期状态 | 案例数 |
| --- | ---: |
| 所有 evidence 都有 `published_at`，且不晚于 `as_of` | 9 |
| 部分 evidence 缺少 `published_at` | 6 |
| 全部 evidence 缺少 `published_at` | 9 |
| 合计 | 24 |

旧运行中的 7 个 `insufficient_evidence`：

- 6 个案例的 evidence 是 `0/N` publication-date coverage；
- 1 个案例只有部分 evidence 有 `published_at`；
- 0 个属于 strict cutoff-ready。

这不等于“缺日期必然导致弃权”。如果完整 world/review 流程本身通过，系统仍可能给出 full probability；日期 gate 主要影响 review 被阻断后的 `evidence_only` fallback。

但这说明当前实验存在一个必须先处理的数据质量问题：

> 对历史回测来说，如果无法证明资料在 `as_of` 前已经公开，就不能把它当成严格的 cutoff evidence。

因此目前不应为了提高 coverage 直接放宽 `blocked / evidence_only` policy。优先级应是：

1. 为历史 evidence 补可核查的 `published_at`；
2. 无法补日期的来源明确标为 historical-unverified；
3. 必要时替换成有日期的一手/权威来源；
4. 再用同一模型重跑 full arm；
5. 最后才判断剩余 abstention 是否属于 policy 过严。

## 13. 原 `validate_cases.py` 实际没有验证 24-case

检查中还发现，旧脚本 `eval/validate_cases.py` 扫描的是 `eval/cases/C*.json`，并排除 `*-evidence.json`。

但当前 `eval/cases/` 中实际只有 22 个 `*-evidence.json`，case 定义集中在 `eval/suites/forecastlab-v1.json`。所以旧命令会输出：

```text
total cases: 0
```

也就是说它并没有验证实际的 24-case suite。

本分支已重写 validator，使其默认直接读取 `eval/suites/forecastlab-v1.json`。

当前离线结果：

```text
READY:         9
NEEDS REVIEW: 15
BROKEN:        0
TOTAL:        24
```

其中 `NEEDS REVIEW` 目前主要表示 `published_at` 覆盖不完整，而不是 JSON 损坏。

validator 现在检查：

- 24-case 是否真正加载；
- outcome / question time 是否有效；
- evidence_file 是否存在并可解析；
- 已知 `published_at / updated_at` 是否晚于 cutoff；
- source URL 是否重复；
- publication-date coverage；
- strict cutoff-ready case count。

缺少 `published_at` 当前是 warning，不直接使 validator 非零退出；明确晚于 cutoff、证据包损坏等才是 hard error。

## 14. 评测公平性的解释

当前 full pipeline 与 single-agent + evidence 都读取同一个 evidence pack，但完整框架会进一步执行 provenance/cutoff 审查，而 single-agent baseline 不会自动因为日期不可核实而弃权。

这可以被解释为“完整 workflow 的一部分”，但报告中不能把二者的 coverage/Brier 差异简单归因于多 Agent 推演本身。

更准确的拆分应是：

```text
证据内容质量
+
历史 cutoff provenance 质量
+
多阶段 workflow
+
review / abstention policy
```

当前 24-case 结果同时混合了这些因素。

因此正式报告至少要把 publication-date coverage 作为 evaluation limitation；若能把 15 个 case 的日期补齐，再重跑会得到更干净的 workflow 对照。


## Strict F trust boundary added after the first contract repair

The initial F-ID repair made downstream World/Review aware of 证据评估 finding IDs. A second pass tightened the trust boundary so this does not become a provenance bypass.

### Server-controlled validation

`EvidenceAssessment.findings_validated` defaults to `false`.

A model cannot grant itself this status. The legacy evidence path forcibly resets it to `false`. Only `assess_evidence()` sets it to `true`, after validating the finding against:

- the current evidence ID;
- a server-owned snapshot hash;
- a supplied paragraph ID;
- the exact quote in that paragraph;
- server-computed code-point offsets.

World/Review may reference `Fxxx` only when this server-controlled flag is true.

### Import / reuse / evaluation alignment

Real imported evidence now always goes through `import_evidence()`, so the server owns the snapshot and passages even for legacy-direct runs.

Reuse is wrapped in a `RetrievalResult` and reassessed under the current run.

The 24-case full arm now follows the same `import_evidence -> assess_evidence` path instead of creating un-snapshotted evidence with `normalize_import()`.

This matters because the previous post-问题分析与证据评估 evaluation could deserialize F-shaped model output on the legacy evidence path without the new exact quote/snapshot validation.

### Security regression

A new negative test explicitly lets a legacy fake model return:

```text
findings_validated = true
F001 -> forged snapshot / quote
```

The pipeline must still reject it with:

```text
证据发现未经过原文校验
```

### Validation note

This strict pass does not change the Brier formula, Review blocking policy, evidence-only audit policy, or probability validation.

The 24-case suite still requires a real model-key rerun before any new coverage percentage can be reported.


## 15. Evaluation suite v2

The original v1 experiment is preserved unchanged for auditability. A new suite is introduced at:

```text
eval/suites/forecastlab-v2.json
eval/cases-v2/
```

The goal is not to improve scores by changing outcomes. It repairs the evaluation inputs so the full pipeline is tested on evidence with defensible cutoff provenance.

### Changes from v1

1. C05 moves its `as_of` from 2024-10-31 to 2024-10-20 because Node.js 22 actually entered LTS on 2024-10-29. The v1 cutoff therefore leaked the outcome.
2. C05 evidence is replaced with pre-outcome Node.js/OpenJS material available by 2024-10-20.
3. Cases whose v1 packs lacked dated sources are replaced with dated pre-cutoff primary/authoritative sources where available.
4. C01 uses two immutable pre-cutoff artifacts instead of trusting an arbitrary retrieved timestamp:
   - a Wayback capture whose timestamp is encoded in the URL;
   - a fixed python/peps Git commit whose SHA is encoded in the blob URL.
5. C01 excerpts are verbatim source text so 证据评估 exact-quote validation is meaningful.

### Cutoff validation rule

The v2 validator does **not** accept an arbitrary `retrieved_at` field as proof that historical evidence existed before the cutoff.

An evidence item is cutoff-ready only if at least one of these is true:

- `published_at <= as_of`; or
- an explicitly declared immutable `cutoff_proof` validates against the source URL identity.

Currently supported immutable proof types:

- Wayback capture timestamp;
- fixed Git commit SHA.

A mismatched or unverifiable proof is a hard validation problem.

### v2 offline result

```text
READY:          24
NEEDS REVIEW:    0
BROKEN:          0
cutoff-ready: 24/24
```

The validator test suite also checks that arbitrary `retrieved_at` is rejected as cutoff proof.

## 16. Fixed model temperature

The previous 24-case report explicitly listed the provider-default temperature as a reproducibility limitation.

This branch now uses:

```text
FORECASTLAB_MODEL_TEMPERATURE=0
```

The value defaults to 0 and must be in [0, 2].

The temperature is:

- explicitly sent with each model request;
- included in the request fingerprint;
- printed by `run_suite.py --dry-run`;
- saved in the evaluation `frozen` metadata.

This does not make LLM execution perfectly deterministic, but it removes one known uncontrolled sampling variable.

## 17. Real API smoke evidence before the full rerun

A targeted DeepSeek API smoke run was executed after the F-contract repair.

Representative v1 cases that previously failed on F references now behave as follows:

```text
C02  old: Review -> F001 hard failure
     new: completed, P(yes)=0.50, findings_validated=true

C07  old: Review -> F001/F002 hard failure
     new: completed, P(yes)=0.20, findings_validated=true

C20  old: Assumption.parent_ids -> F001 hard failure
     new: reaches Review/Forecast and abstains as insufficient_evidence

C22  old: Assumption.parent_ids -> F001 hard failure
     new: completed, P(yes)=0.58, findings_validated=true
```

The important conclusion is limited: these targeted runs show the F-contract hard failure is no longer reproduced on real API calls.

They do **not** yet establish a new 24-case coverage or Brier score.

The next result to report must come from a full `forecastlab-v2` run on a committed revision.
