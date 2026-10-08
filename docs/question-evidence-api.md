# 问题定义与证据评估 API

启动后 `/docs` 提供以实际 Pydantic 类型生成的 OpenAPI。以下路径均相对于本机服务。

## 1. 分析问题

`POST /api/questions/analyze`

```json
{
  "question": {
    "question": "这个项目完成测试了，所以能够按时发布吗？",
    "as_of": "2026-09-30T08:00:00Z",
    "mode": "binary",
    "resolve_by": null,
    "resolution_rule": "",
    "user_assumptions": []
  },
  "operation_id": "client-generated-unique-id",
  "answers": []
}
```

返回 `QuestionFraming`，包括 `draft_id/revision/raw_question/proposed_spec/clarifications/premises/alternative_directions/retrieval_plan/status/analysis_record`。未指定的关键条件不能靠后端随意补齐；模型候选还要经过字段和原话对应校验。

继续分析时同时传 `draft_id` 和 `expected_revision`；`answers` 形如 `[{"clarification_id":"C001","answer":"以可下载正式版为准"}]`。用户改了对象、截止日期或规则时，直接在 `question` 中提交修改。新提交使用新 `operation_id`；完全相同的超时重发使用原 ID，成功结果保持幂等。相同 ID 换内容返回 409。

真实请求需要服务端模型配置。教学入口是 `/api/examples` 返回的 `question_evidence_demo`：传指定 `demo_case_id` 和完整固定问题才可使用。任意问题不能靠加该字段获得伪造固定回答。

## 2. 读取草稿

`GET /api/questions/{draft_id}` 返回：

```text
framing: 最新不可覆盖的草稿版本
confirmation: 该版本的确认记录或 null
preparation_records: 准备请求记录（含失败、未知 token 和中断状态）
```

浏览器只在本地保存 draft_id，刷新后从后端读取实际状态，不缓存整份确认权限。

## 3. 确认

`POST /api/questions/{draft_id}/confirm`

```json
{
  "expected_revision": 2,
  "decisions": [
    {"premise_id":"P001","user_review":"retained","treatment":"to_verify"},
    {"premise_id":"P002","user_review":"rejected","treatment":"to_verify"}
  ]
}
```

每项当前前提必须有且只有一个决定。保留并核查使用 `retained + to_verify`；指定条件使用 `retained + scenario_condition`；否认使用 `rejected`。尚有阻断性澄清时不接受确认。确认本身不调用模型，不接受 `question/status/confirmed_at` 等客户端越权字段。

返回 `QuestionConfirmation`，包括 `confirmation_id/question/framing/revision/confirmed_at/decision_hash`。相同决定顺序变化不产生第二条确认；改变决定必须重新分析保存新版本。

## 4. 创建运行

新版 `POST /api/runs`：

```json
{
  "confirmation_id": "confirm_server_generated",
  "evidence_mode": "import",
  "evidence": [
    {"title":"实际资料标题","source_url":"https://example.org/replace-with-real-source",
     "publisher":"实际发布者","published_at":"2026-09-29T00:00:00Z",
     "excerpt":"从实际来源保存的逐字资料。"}
  ]
}
```

上面网址、标题、时间和内容只是字段说明，必须替换为真实来源。`online` 不需 evidence，但需 Tavily 配置。`reuse` 必须带 `parent_run_id`，只沿用来源并重算发现。`demo` 只能用于固定教学确认。

响应为 202 和 `run_id/status`。轮询 `GET /api/runs/{run_id}`。不能同时传 `confirmation_id` 和 `question`；新建时确认必须仍是最新版本。历史兼容请求可以单独传 `question`，结果标记 `question_origin=legacy_direct`，不执行新版确认过程。

## 5. 查看发现和原文

`GET /api/runs/{run_id}/evidence` 保留来源数组。

`GET /api/runs/{run_id}/evidence-assessment` 返回逐项发现、结构化冲突、缺口、检索日志、排除记录和未通过校验的候选。`rejected_findings` 是调试审计材料，不可当有效证据。

`GET /api/runs/{run_id}/evidence/{evidence_id}/passages` 仅通过运行所属 E 编号加载已登记快照；不接受任意文件路径。返回 `text/snapshot_hash/content_truncated/passages`。每段带 `paragraph_id/start/end/text/snapshot_hash`。字符偏移为 Unicode 码点，需核对保存的 hash 和逐字 quote 再高亮。

旧来源没有新快照时返回 422，不伪造原文位置，也不读取旧记录随意提供的系统文件。

`GET /api/runs/{run_id}/export?format=html|json` 增加确认来源、前提、发现与局限。JSON 导出去掉 `snapshot_path/declared_snapshot_path`，不是服务器文件读取授权。模型密钥不在返回对象中。

## 状态码与约束

| 状态码 | 含义 |
|---|---|
| 404 | 草稿、确认、运行或证据不存在 |
| 409 | 草稿版本/操作冲突、旧确认、已有运行正在执行 |
| 422 | 无效字段、确认缺项、模式混用、不可验证快照 |
| 429 | 问题准备请求预算耗尽 |
| 502 | 模型输出或调用失败，保留请求审计信息 |
| 503 | 真实模型/搜索配置缺失，不返回模拟成功 |

新契约拒绝未知权限字段。日期必须包含时区。分析最多十项澄清、十二条候选前提、六条替代方向、三个检索任务；query 最多四百字符。只有编号和原文对应通过并不意味着内容推论正确，还需真实模型和人工核查。
