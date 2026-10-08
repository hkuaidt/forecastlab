# ForecastLab

基于证据溯源与多主体推演的预测研究工作台。研究流程为：**定义问题 → 确认前提 → 检索与评估证据 → 构建世界模型 → 情景推演 → 审查 → 预测报告**。

支持二元事件预测和情景分析、来源原文与逐字引文检查、研究历史、阶段恢复及 HTML/JSON 报告导出。二元预测可以记录实际结果并计算 Brier 分数。项目用于课程研究，预测概率尚未经过充分校准；固定教学材料仅用于验证软件流程。

## 本地运行

需要 Python 3.12、uv、Node.js 20.19+ 或 22.12+、npm。

```bash
uv sync --locked
cp .env.example .env
cd frontend
npm ci
npm run build
cd ..
uv run uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。也可运行 `bash scripts/start-local.sh`，默认端口为 8765；修改前端后使用 `REBUILD=1 bash scripts/start-local.sh`。

真实分析需要在 `.env` 中设置模型服务：

- Qwen 或 OpenAI 兼容服务：`QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL`。
- DeepSeek：`DEEPSEEK_API_KEY`，可选 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。同时配置时优先使用 Qwen。
- 在线检索：`BRAVE_SEARCH_API_KEY` 或 `TAVILY_API_KEY`；也可通过 API 导入证据。

未配置服务时，真实请求会明确报错。固定教学案例由 `/api/examples` 提供，可通过问题确认和运行 API 回放；无密钥流程检查也可使用下方 fixture 评测命令。

前端开发：在 `frontend/` 运行 `npm run dev`，API 默认代理到本机 8000 端口。后端修改后需要重启。

## 仓库结构

| 目录 | 用途 |
| --- | --- |
| `backend/` | API、预测流程、证据处理、持久化及单元测试 |
| `frontend/` | 研究工作台和浏览器测试 |
| `eval/` | 可复用评测工具、冻结套件与基准数据 |
| `experiment/` | 实验报告、原始结果和测试验证记录 |
| `examples/` | 证据导入及固定教学示例 |
| `docs/` | 工作流、API、部署说明及已有研究材料 |
| `scripts/`、`deploy/` | 本地启动、源码打包及部署配置 |

研究方法、各轮实验及其局限见 [实验索引](experiment/README.md)。原始实验结果保留生成时的模型、提示版本与路径记录；历史标识不代表当前模块名称。

## 验证

```bash
uv sync --locked --group browser
uv run pytest -q
uv run python experiment/validate_suite.py
uv run python eval/validate_cases.py
cd frontend && npm ci && npm run build && cd ..
uv run --group browser playwright install chromium
uv run --group browser pytest frontend/tests -q
uv run python eval/question_evidence.py --mode fixture \
  --cases examples/question-evidence/neutral-leading-pairs.json \
  --output fixture-eval.json
```

默认测试和 fixture 评测无需真实模型或搜索密钥。真实模型评测需要明确选择 live 模式并配置服务，可能产生费用。

## 部署与数据

Docker：在项目根目录配置 `.env` 后运行 `docker compose up -d --build`。默认仅监听 `127.0.0.1:8000`；服务目前没有用户认证和数据隔离，对外使用前需配置带认证的网关。服务器配置见 [部署说明](docs/server-deployment.md)。

运行数据保存在 `data/`，包括 SQLite、阶段结果与证据快照。`.env`、密钥、运行数据库及浏览器截图不提交 Git。源码打包工具 `scripts/package_source.py` 仅收集已提交的允许文件。

## 文档

- [完整工作流](docs/Agent工作流完整说明.md)
- [问题确认与证据 API](docs/question-evidence-api.md)；服务启动后的 `/docs` 提供完整 OpenAPI
- [已知限制](docs/limitations.md)
- [LLM 辅助使用说明](docs/llm-usage.md)
- [实验与验证记录](experiment/README.md)
