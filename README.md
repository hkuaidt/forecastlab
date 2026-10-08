# ForecastLab

ForecastLab 是基于证据溯源和多主体推演的情景研究工作台。它将研究问题依次变成可确认的问题边界、保存的来源与证据发现、初始局势、主体行动、条件性的状态变化，以及带具名情景概率的研究报告。

当前产品入口为：新建研究 → 问题分析与确认 → 联网取证 → 世界建模 → 两轮主体行动与环境推进 → 审查 → 情景报告。画布显示已有结果，点击节点可阅读全文、依据与限制，也可打开独立阅读页。来源事实、模型假设和模拟结果分别保存，不把推演内容当成外部事实。

概率是模型基于现有证据与假设给出的主观分配，未经校准，不是来源中的实测频率。多个主体使用同一模型，也不等于多个独立专家。真实研究需要模型服务；在线检索还需要 Brave 或 Tavily。后端保留教学 fixture 和旧版兼容接口，但它们不是正式工作台的新建入口，也不能用于证明真实模型质量。

## 快速启动

环境：macOS/Linux、Python 3.12、`uv`、Node.js 20.19+/22.12+、npm。

```bash
uv sync --locked
cd frontend && npm ci && npm run build && cd ..
uv run uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。首次运行会在配置的数据目录中创建 SQLite 和阶段快照。也可使用 `bash scripts/start-local.sh`，其默认地址为 <http://127.0.0.1:8765>。前端开发模式为 `cd frontend && npm run dev`，Vite 将 `/api` 代理到 8000 端口。

Group 8 服务器的正式 checkout、18765 端口、四卡副本路由及操作边界见 [服务器部署说明](docs/server-deployment.md)。该服务器只允许 group8 范围内变更，不使用通用管理员安装步骤。

## 创建真实研究

1. 将 `.env.example` 复制为 `.env`，填写 `QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL`。项目调用 OpenAI 兼容接口；也保留 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`，两组同时配置时优先使用 `QWEN_*`。
2. 配置 `BRAVE_SEARCH_API_KEY` 或 `TAVILY_API_KEY`。已配置 Brave 时优先使用 Brave；未配置 Brave 时使用 Tavily。`FORECASTLAB_SEARCH_PROXY` 用于搜索 API，不作为本地模型网关。
3. 在“新建研究”输入开放情景问题。信息截至时间默认取提交分析时的当前时间，也可指定历史时间。阅读模型的问题理解，回答必要澄清，并决定哪些候选前提需要核查、作为指定条件或不采用。
4. 确认问题后开始联网推演。工作台按阶段展示已保存结果；右键拖动画布、滚轮缩放、左键选择节点，触屏可拖动与点按。阅读面板保留完整的已保存内容，引用可展开到依据和来源原文。
5. 查看具名情景、各自概率、定义、触发条件、理由和引用。报告和原始记录可导出；“后续研究”创建关联父运行的新记录，重新确认问题并独立取证。

模型名称和参数以实际服务为准。代码中的默认值不是服务器性能测量：`FORECASTLAB_MAX_CALLS` 默认 18、`FORECASTLAB_MAX_SECONDS` 默认 300，部署可覆盖；思考模式、输出上限、超时和上下文窗口也应与实际模型匹配。运行详情保留阶段耗时、模型调用和失败原因。

## 证据、原文与摘要降级

在线检索先获得候选来源，再按相关性、日期和来源分组规则筛选。Brave 结果缺少正文时，后端会尝试读取选中网页并提取正文；Tavily 返回的正文也会保存。正文抓取使用受限的公网直连读取，不访问私有地址，不绕过验证码、登录或付费访问限制。

正文读取超时、被拒绝、格式不支持或内容不足时，来源可保留为搜索摘要，并记录抓取结果与限制；不会把摘要标成完整正文。搜索代理可用不表示每个原文网站都可直连。正文/摘要数量、检索失败、被拒绝候选、冲突与日期限制可在页面及导出记录中查看。部分来源成功不等于所有检索任务成功。

来源窗口读取登记快照的完整已保存文本，而不只读取列表用的 `excerpt`。保存时仍可能受内容大小等边界限制，相关截断标记会保留；“完整已保存文本”不意味着抓到了网站的全部内容。快照哈希与 Unicode 引文位置用于核对和高亮原文。证据发现中的判断、引句和适用限制分开展示，结构校验通过不代表所有语义推断都已经得到独立人工核实。

来源编号为 `E`，证据发现为 `F`，模型假设为 `H`，主体行动为 `M`，模拟结果为 `S`。来源分组只表示去重或可能相关，不能证明各来源彼此独立。发布日期未知、历史回看和截点后取证等限制不因报告生成成功而消失。

## 工作流、概率与并发边界

```text
QuestionSpec + 已确认的问题理解
  → Evidence[] + EvidenceAssessment
  → WorldState + ActorProfile[]（Round 0）
  → ActorAction × N → SimulationStep S1
  → ActorAction × N → SimulationStep S2
  → Review → Forecast + scenario_details

N 按实际主体数量确定，最多 4 个。
```

同一轮各主体读取同一个父状态，最多四个主体调用并行；收齐行动后，环境统一推进下一状态。模拟的假设、行动和状态变化保持自己的类型与引用，不转化为外部证据。

有来源的开放情景报告要求至少两个具名情景，概率在 0–1 之间、合计为 1。`scenario_details` 与概率名称对应，保存情景边界、触发条件、概率理由和 E/H/S 依据。审查意见作为限制披露，不能被读成已经证实的来源结论。无来源、模型失败或无法生成合法报告时仍可能没有概率；有画布或已有模拟结果不等于报告成功。

单个 API 进程同一时间只接受一个活跃研究。新建、继续和报告修复共享该运行锁，已有研究运行时返回 409。Group 8 的物理卡 4、5、6、7 分别运行 TP=1 模型副本，用于同一研究中的主体并行；这不表示支持四项独立研究同时运行。当前没有持久化任务队列、账号权限、多用户隔离或全网持续监控。

## 运行状态与恢复

| 状态或操作 | 含义与行为 |
| --- | --- |
| `queued` / `running` | 已接收或正在执行，页面轮询完整运行详情。 |
| `completed` | 已生成报告；仍需阅读资料限制，不能据此判断概率已经校准。 |
| `failed` / `interrupted` | 执行失败或服务中断；已保存阶段与错误可查看，无完整报告时可继续。 |
| `cancelled` | 用户停止运行；停止请求可能先返回 `cancel_requested=true`，待轮询确认终态。无完整报告时可继续已保存阶段。 |
| `partial` / `insufficient_evidence` / `scenario_only` | 部分结果、资料不足或无概率的兼容结果，具体以报告内容与状态为准。 |
| 继续运行 | 使用原 `run_id`，从未完成阶段继续；保留已经成功保存的阶段。 |
| 重新生成报告或概率 | 在 `report_repair.available=true` 时提供；重验来源与轨迹，创建带 `parent_run_id` 的子运行。重验失败返回 422，原记录保留。 |
| 后续研究 | 创建关联父运行的新研究，重新确认问题、时间和取证。 |

历史目录按摘要分页加载；选择一项后读取完整记录。运行中断和用户编辑等待不应被当作模型持续推理的证据，耗时与吞吐应结合页面计时口径和逐请求记录阅读。

## API

启动后访问 `/docs` 查看当前 OpenAPI。主要接口如下：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/questions/analyze` | 分析问题、提出澄清、识别候选前提 |
| GET | `/api/questions/{draft_id}` | 读取草稿、版本和确认记录 |
| POST | `/api/questions/{draft_id}/confirm` | 确认问题与候选前提的处理方式 |
| POST | `/api/questions/parse` | 旧版字段检查接口 |
| POST | `/api/runs` | 通过 `confirmation_id` 创建研究；兼容旧版直接输入 |
| POST | `/api/runs/{id}/cancel` | 请求停止；返回运行详情 |
| POST | `/api/runs/{id}/resume` | 继续失败、中断、已停止或部分完成的运行，返回 202 |
| POST | `/api/runs/{id}/repair-report` | 重验依据后创建报告修复子运行，返回 202；重验失败返回 422 |
| GET | `/api/runs?summary=true&limit=20&offset=0` | 轻量历史摘要数组；默认 `summary=false` 保持完整记录兼容行为 |
| GET | `/api/runs/{id}` | 完整阶段、运行记录与结果 |
| GET | `/api/runs/{id}/evidence` | 来源详情 |
| GET | `/api/runs/{id}/evidence-assessment` | 证据发现、冲突、缺口和检索日志 |
| GET | `/api/runs/{id}/evidence/{evidence_id}/passages` | 登记快照原文、段落和引用位置 |
| GET | `/api/runs/{id}/export?format=html\|json` | 报告或原始记录导出 |
| GET | `/api/health` | 应用与依赖配置、可用性和说明；不返回密钥 |
| GET | `/api/live` | 仅检查应用存活 |
| POST | `/api/runs/{id}/settlement` | 兼容二元预测的结果结算 |
| GET | `/api/settlements/summary` | 兼容二元预测的结算数量与 Brier 汇总 |

主入口提交 `confirmation_id`、`evidence_mode="online"`、`evidence=[]`，以及可选 `parent_run_id`。旧版直接传 `question` 和 `evidence_mode`（`import`、`online`、`reuse`、`demo`）的接口继续保留。`reuse` 需要有效父运行且不得把教学资料作为真实证据；它不等于当前工作台“后续研究”的独立取证流程。

历史列表 `limit` 默认 50、允许 1–200，`offset` 从 0 开始。健康检查中的 `model_configured` / `search_configured` 只表示配置存在，不能替代 `model_ready` / `search_ready`。搜索网络可达但密钥与额度尚待真实取证验证时，`search_ready` 可以是 `null`，原因见 `readiness`。

## 保留的二元预测与导入接口

当前工作台不提供二元结算表单或 JSON 导入控件，相关后端能力用于兼容旧记录、研究与自动化调用。二元结算保留事前概率，另存实际结果、来源与观测值，计算 `(P(是) - 实际是的取值)^2`；无有效概率的记录不计入 Brier。少量案例不能证明校准或准确率优势。

导入证据可使用 JSON 数组，或包含 `evidence` 数组的对象。最小条目：

```json
[
  {
    "title": "来源标题",
    "source_url": "https://example.org/actual-source",
    "publisher": "发布方",
    "published_at": "2026-09-01T00:00:00Z",
    "excerpt": "从真实来源保存、支持当前判断的原文片段。"
  }
]
```

示例网址与文字必须替换为实际材料。后端分配 E 编号、保存时间和内容哈希。历史盲回测需要截点前冻结的快照；事后取得但可证明发表于截点前的材料属于有回看限制的历史练习，不能仅凭发布日期声称当时已取得。市场预测的历史方法和局限见 [科创 50 历史回看](docs/market-postmortem-2026-07.md)。

## 部署

通用 Docker 部署：

```bash
docker compose up -d --build
docker compose ps
```

Compose 默认只绑定 `127.0.0.1:8000`，数据保存在宿主机 `data/`。项目没有认证和多用户隔离，远程访问应使用受控 SSH 隧道或带认证的网关。`.env`、数据库、密钥和用户来源快照不得加入仓库。停止容器可用 `docker compose down`，它不会主动删除 `data/`。

Group 8 使用自有 checkout 的应用控制脚本和模型路由，步骤与资源限制以 [服务器部署说明](docs/server-deployment.md) 为准。应用默认单进程运行；增加 uvicorn worker 会绕开单进程运行锁，不能作为未经设计的多用户扩容方式。

## 测试与历史研究

```bash
uv sync --locked --group browser
uv run pytest -q
(cd frontend && npm ci && npm run build)
uv run --group browser python -m playwright install chromium
uv run --group browser pytest frontend/tests -q
```

自动化回归覆盖数据契约、来源与引文、概率约束、取消与恢复、报告修复、摘要分页、画布交互和导出。常规测试使用隔离数据和模拟服务，不将真实研究或付费推理作为默认测试。测试数量随版本变化，应引用本次命令输出、代码版本及环境，不在 README 固定旧数量。真实模型质量验证需另行记录问题、提示词、模型、证据、预算、失败/拒答和人工审阅过程。

2026-10-07 的 DeepSeek、Agent 1/2 与 Tavily 冻结实验保留为历史研究，不是当前本地 Qwen3-8B 部署或新版工作台的成功率承诺：

- [ForecastLab-v2 多臂评测](experiment-2026-10-07-v2/report.md)
- [Agent 1/2 语义稳健性](experiment-2026-10-07-agent12-semantic/report.md)
- [Tavily Live Retrieval](experiment-2026-10-07-tavily/report.md)
- [Live Full E2E](experiment-2026-10-07-live-e2e/report.md)

这些实验没有证明多阶段 World/Actor/Simulation 比同证据单 Agent 更准确；情景组织、证据检查和预测准确率是不同指标。教学数据 `examples/classroom-demo.json` 和固定 fixture 不用于真实质量评估。

仓库保留 `eval/baseline.py`、`eval/score.py` 用于同问题同证据的基线和 Brier/覆盖率评估。Agent 2 语义蕴含研究使用 `eval/benchmarks/agent2-entailment-hard-v1.json` 与 `eval/benchmarks/agent2-entailment-post-boundary-v1.json`；可选独立 Judge、NLI 和 cascade 属于研究链，不是已经启用的生产准确性保证。阈值只用 dev 选择，最终 test 不反复调参。

`experiment/` 中的 v1/v2/v3v4 与影子 full-basis 研究继续保留，`FORECASTLAB_SHADOW=0` 默认关闭。影子结果不得替代正式预测。整合记录见 [上游同步说明](docs/upstream-main-merge-2026-10-08.md)。

## 目录与交接

```text
backend/app/       数据契约、来源入口、状态图、模型调用、API、SQLite
backend/tests/     契约和隔离回归
frontend/src/      React 研究工作台
frontend/tests/    浏览器交互与呈现回归
deploy/           应用控制、模型路由和部署支持
examples/         明确标注的教学材料
eval/             研究基线、审计与评分脚本
docs/             工程、接口、实验与部署记录
```

交接材料：[Agent 1–2 实现](docs/agent12/implementation-report.md) · [下游兼容](docs/agent12/integration.md) · [API](docs/agent12/api.md) · [验证](docs/agent12/validation.md) · [局限](docs/agent12/limitations.md) · [LLM 使用](docs/agent12/llm-usage.md)。这些带日期的记录描述各自版本，当前接口以代码和 `/docs` 为准。

源码包由 `scripts/package_agent12.py` 从已提交版本生成，排除运行数据与凭证。Codex 参与了代码、测试、页面与文档起草；正式汇报应保留实际 LLM 使用和人工核查记录，不以参考产品的演示能力代替本项目已实现能力。
