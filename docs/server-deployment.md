# Group 8 服务器部署与验收

2026-10-08 核对状态：修复版已通过独立端口真实端到端、旧记录续跑及正式页面验收，正式端口 `127.0.0.1:18765` 现由 `/home/group8/work/forecastlab-main` 提供服务。旧目录保留用于回退。修复版服务器 `.env` 应保留 `FORECASTLAB_DATA_DIR=/home/group8/work/forecastlab/data`，继续使用原有数据；不要因切换 checkout 而初始化另一套正式数据。电脑连接 VPN 并建立 SSH 本地端口转发后，正式访问地址仍为 `http://127.0.0.1:18765`。

修复版在当前 checkout 下执行 `bash deploy/server-control.sh start|stop|status`，默认只监听 `127.0.0.1:18765`，PID 和日志分别保存在 `.forecastlab/server.pid`、`.forecastlab/server.log`，已由 Git 忽略。它不再写父目录的共享 `forecastlab-server.pid/log`；旧记录和旧脚本只用于管理旧实例。旧实例尚未释放 18765 时，新脚本会明确报端口占用，不会报告启动成功或覆盖 PID。启动成功要求新进程持有监听端口且 `/api/health` 返回成功；停止时核对账号、checkout、启动参数和端口。

端口或文件路径可通过环境变量覆盖，`start`、`status`、`stop` 必须使用同一组设置。以下仅演示独立实例的配置，端口须自行选择空闲值；相对文件路径以 checkout 为基准。

```bash
cd /home/group8/work/forecastlab-main
export FORECASTLAB_PORT=18767
export FORECASTLAB_PIDFILE="$PWD/.forecastlab/preview.pid"
export FORECASTLAB_LOGFILE="$PWD/.forecastlab/preview.log"
bash deploy/server-control.sh start
bash deploy/server-control.sh status
bash deploy/server-control.sh stop
unset FORECASTLAB_PORT FORECASTLAB_PIDFILE FORECASTLAB_LOGFILE
```

部署前可运行隔离 smoke；它使用随机空闲端口、临时 PID/log/data，只检查应用启动和健康，不调用模型或 GPU，不操作正式 18765 实例。覆盖启动、重复启动、状态、停止、端口占用、启动失败及拒绝操作不匹配 PID。

```bash
cd /home/group8/work/forecastlab-main
bash -n deploy/server-control.sh
.venv/bin/python deploy/test_server_control.py
```

模型容器为 `group8-qwen-tp4`，镜像为 `registry.mthreads.com/presale/devtech/vllm_musa:s4000_4.3.5_d0708`。Qwen3-8B 使用 BF16，没有低比特量化；TP=4，物理 MTT S4000 卡 4、5、6、7，每卡 48 GiB，合计 192 GiB。容器必须显式设置 `MTHREADS_VISIBLE_DEVICES=4,5,6,7`，禁止 `all`，禁止使用物理 0–3。容器逻辑 0–3 对应物理 4–7，须以 UUID 和 PCI 地址核对，不能仅凭逻辑编号判断。

| 物理卡 | 容器逻辑卡 | PCI 地址 | UUID |
| --- | --- | --- | --- |
| 4 | 0 | 0000:32:00.0 | 60a07963-2021-8080-e5c6-e362c0fe1736 |
| 5 | 1 | 0000:38:00.0 | 3f329bba-6704-90bc-8b7f-413bc549bc5a |
| 6 | 2 | 0000:3b:00.0 | 9206a77d-4a39-2906-6666-fc8c3104d888 |
| 7 | 3 | 0000:3c:00.0 | bffccee4-bc15-2f0c-69ca-5a645e33a190 |

模型接口为 `http://127.0.0.1:18048/v1`，模型名 `qwen3-8b`，上下文上限 16384；2026-10-08 已只读核对 `/health` 和 `/v1/models`，这些值仍有效。应用实际配置：`FORECASTLAB_ENABLE_THINKING=true`、单次输出上限 4096、模型超时 600 秒、运行预算 48 次调用及 7200 秒。服务器本地 vLLM 使用 `FORECASTLAB_LOCAL_JSON_MODE=prompt`；结构化输出仍由 Pydantic、来源哈希、引文位置和阶段引用校验，思考开关不能绕过这些校验。世界和主体引用错误允许两次有记录的修复，失败保留错误和原响应，不自动替换或接受不存在的证据。

Brave Search 已配置并完成真实检索。检索使用独立 `FORECASTLAB_SEARCH_PROXY`，本地模型调用不走检索代理。API 密钥只保存在服务器 `.env`；仓库只有空值示例。权重优先从官方 ModelScope 国内源直连下载并显式禁用代理。只修改 group8 的文件、环境和自有容器，不修改共享驱动、GRUB、内核或全局 Docker 配置，不重启整机。

界面是空白起始的研究工作台，保留六阶段树状画布、缩放折叠、研究目录和可选阅读面板。旧教学示例不再作为用户入口或默认恢复数据。问题确认、限定时间、来源正文、前提与证据关系、被拒绝的发现及局限均可查看。引用高亮需同时满足快照哈希和 Unicode 位置对应原文；不匹配时明确显示失败。

`run_eb86e9084513` 是截至 2026-10-07、推演到 2027 年底 AI 对数学和科学影响的真实运行。开放情景问题不强行分配二元概率。专题补充资料来自保存的官方来源快照；模型段落逐段核对后才可导出。`eval/research_supplement.py`、`repair_research.py`、`refine_research.py` 仅生成候选；`publish_research.py` 要求审查记录，核对正文与原始响应或逐段事实校订记录一致，排除私有提示、全文快照和凭据后发布。日期未知和截点后取证的材料不能证明截点前可用，因此该案例不是盲回测。

`local-model-benchmark.json`、`local-model-full-smoke.json` 和 `local-model-agent-eval-revised.json` 保留之前单卡、关闭思考或旧配置的测量。它们不是当前四卡思考模式的性能数字，也不能据此宣称四卡更快或与其他大模型等效。原案例验收见 `validation-2026-10-08.md`；本次 main 修复及部署验收见 `main-stability-20261008.md`。

前端生产构建会清空 `dist`。重新部署时先构建，再对已审查专题运行 `PYTHONPATH=backend .venv/bin/python eval/publish_research.py --run-id RUN_ID`。应用与模型按自有脚本启动，服务器重启后须重新启动并复核 GPU 映射；未新增整机启动服务。
