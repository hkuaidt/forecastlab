# Group 8 服务器部署与验收

本说明针对 `/home/group8/work/forecastlab-main`，应用只监听 `127.0.0.1:18765`。电脑连接 VPN 后，通过 SSH 本地端口转发访问工作台。旧 checkout 可留作代码回退，但不能恢复用户已要求删除的历史研究或旧专题资产。

正式数据目录由服务器私有 `.env` 中的 `FORECASTLAB_DATA_DIR` 指定，目前沿用 `/home/group8/work/forecastlab/data`。保持目录配置与保留旧记录是两回事；切换 checkout 不应意外初始化另一套正式数据，也不应把旧备份写回当前数据目录。

## 资源与进程边界

只允许使用物理 GPU 4、5、6、7：四张 MTT S4000，每卡 48 GiB，合计 192 GiB。当前采用四个 Qwen3-8B 副本，每卡一个 TP=1 实例，使用 BF16；不再采用旧文档中的单实例 TP=4 方案。每个容器都必须显式设置 `MTHREADS_VISIBLE_DEVICES` 为自己使用的那一张物理卡，禁止 `all`，禁止在物理 0–3 上分配显存或启动计算。

单卡容器内部通常看到逻辑卡 0，不能把它当作物理卡 0。启动或恢复后须用 UUID、PCI 地址及容器可见设备核对物理 4–7 的映射，不能沿用旧 TP=4 容器的逻辑 0–3 对照表。

只操作 group8 的文件、环境和自有进程/容器。不修改共享驱动、GRUB、BIOS、IOMMU、内核参数或全局 Docker 配置，不重启整机，也不影响其他账号任务。模型权重优先从官方 ModelScope 国内源直连下载并显式禁用代理；直连不可用时，使用其他下载链路需按用户授权处理。

## 当前服务拓扑

```text
受控 SSH 隧道
  → ForecastLab API + 前端：127.0.0.1:18765
  → 模型路由：127.0.0.1:18048/v1
      → 127.0.0.1:18054/v1
      → 127.0.0.1:18055/v1
      → 127.0.0.1:18056/v1
      → 127.0.0.1:18057/v1

四个 upstream 分别对应物理 GPU 4–7 上的四个 TP=1 副本。
```

对应用保持 `QWEN_BASE_URL=http://127.0.0.1:18048/v1`、`QWEN_MODEL=qwen3-8b`。当前模型上下文窗口为 16384；以 `/v1/models` 和实际 worker 配置核对。应用的 `FORECASTLAB_CONTEXT_WINDOW` 必须与服务一致。输入、系统提示和预留输出都占用窗口；思考开关、各阶段输出上限、超时、调用与时间预算均以当前部署配置为准，不把旧测量当作现配置保证。

同一轮最多四个主体读取同一个父状态，并行请求四个副本；环境在行动收齐后统一推进。应用仍然单进程、单活跃研究，新建、继续和报告修复共用运行锁。四个副本不表示四项研究可同时执行，也不能通过多开 API worker 绕过此限制。

## 应用控制

在正式 checkout 查看状态：

```bash
cd /home/group8/work/forecastlab-main
bash deploy/server-control.sh status
```

需要启动自有实例时执行 `bash deploy/server-control.sh start`，停止时执行 `bash deploy/server-control.sh stop`；不要将启停命令连在一次验收中执行。

默认 PID、日志为 `.forecastlab/server.pid` 和 `.forecastlab/server.log`，由 Git 忽略。脚本先检查端口，启动后确认新进程拥有监听端口并能响应健康接口；失败会明确退出，不覆盖其他实例的 PID 记录。停止时核对本人 UID、checkout、启动参数和端口，不依据一个裸 PID 杀进程。

应用控制脚本目前请求 `/api/health` 并检查应用响应；启动成功不代表所有模型 worker、搜索密钥或额度可用，依赖状态还需检查响应中的 `readiness`。后端代码或环境变更需要受控重启；纯静态前端发布无需重启 API。

`FORECASTLAB_PORT`、`FORECASTLAB_PIDFILE`、`FORECASTLAB_LOGFILE` 可覆盖默认值。相对文件路径按 checkout 解析，start/status/stop 必须使用同一组设置。独立预览还必须指定临时数据目录，不能与正式数据混用：

```bash
cd /home/group8/work/forecastlab-main
preview_data="$(mktemp -d "$PWD/.forecastlab/preview-data.XXXXXX")"
export FORECASTLAB_PORT=18767
export FORECASTLAB_PIDFILE="$PWD/.forecastlab/preview.pid"
export FORECASTLAB_LOGFILE="$PWD/.forecastlab/preview.log"
export FORECASTLAB_DATA_DIR="$preview_data"
export FORECASTLAB_FRONTEND_DIR="$PWD/frontend/dist-preview"
# 先确认 18767 空闲，且 dist-preview 已构建。
bash deploy/server-control.sh start
bash deploy/server-control.sh status
bash deploy/server-control.sh stop
unset FORECASTLAB_PORT FORECASTLAB_PIDFILE FORECASTLAB_LOGFILE
unset FORECASTLAB_DATA_DIR FORECASTLAB_FRONTEND_DIR
```

上述示例不会自动删除临时数据；核对绝对路径与内容后再按需要清理。只需验证启动脚本时，优先使用已有隔离 smoke，它自动使用随机空闲端口、临时 PID/log/data，不调用模型、不操作正式 18765：

```bash
cd /home/group8/work/forecastlab-main
bash -n deploy/server-control.sh
.venv/bin/python deploy/test_server_control.py
```

## 四副本模型路由

`deploy/model-router-control.sh` 只管理 group8、当前 checkout、明确启动参数的自有路由进程，固定 loopback 绑定并使用一个 uvicorn worker。默认配置、PID 和日志分别为：

- `.forecastlab/model-router.json`
- `.forecastlab/model-router.pid`
- `.forecastlab/model-router.log`

配置使用本地 loopback upstream 列表，例如：

```json
{
  "upstreams": [
    "http://127.0.0.1:18054/v1",
    "http://127.0.0.1:18055/v1",
    "http://127.0.0.1:18056/v1",
    "http://127.0.0.1:18057/v1"
  ],
  "queue_limit": 32,
  "queue_timeout_seconds": 120,
  "read_timeout_seconds": 600
}
```

配置变更应先写同目录临时文件，校验 JSON 与 upstream 后原子替换，后续请求会读取新配置。不要直接截断正在使用的配置文件。配置和日志使用私有权限，不保存提示词或密钥。

```bash
cd /home/group8/work/forecastlab-main
bash deploy/model-router-control.sh status
```

需要启停路由时分别使用 `bash deploy/model-router-control.sh start` 或 `bash deploy/model-router-control.sh stop`。它不负责启动或停止 GPU 容器。

每个 upstream 同时最多承接一个生成请求，空闲优先；繁忙时使用有界等待队列。models、tokenize 和健康查询不占生成槽位。路由转发 JSON、流式结果、usage 与模型扩展字段；客户端取消或连接异常释放槽位。只有建立连接失败可尝试其他 worker，已经开始发送结果的生成不会自动重放。

`FORECASTLAB_ROUTER_CONFIG`、`FORECASTLAB_ROUTER_PIDFILE`、`FORECASTLAB_ROUTER_LOGFILE`、`FORECASTLAB_ROUTER_PORT` 可用于独立实例；生产应用地址和所有启停命令必须保持一致。路由 `/health` 在至少一个 worker 健康时可成功，因此部署验收还应核对所有计划内 worker 的健康与 `active_generations`，不能只检查 HTTP 200。

## 搜索与正文获取

当前配置 Brave Search，并保留未配置 Brave 时使用 Tavily 的路径。搜索 API 可使用独立 `FORECASTLAB_SEARCH_PROXY`，本地模型调用不走搜索代理。网页正文抓取采用校验目标地址的公网直连，不使用代理绕过目标 IP 检查。

选中来源能取得正文时保存正文与抓取元数据；不能取得正文时可保留搜索摘要及失败原因。页面必须如实区分 `body` 与 `snippet`，保留日期未知、正文缺失、截断和历史回看限制。来源阅读器展示已登记快照的完整保存文本；不得用模型补写文本冒充原文。

API 密钥只保存在服务器私有 `.env`，仓库保留空值示例。配置存在不表示密钥、额度或每个网站可用；健康检查的“网络可达、待验证”应按其含义阅读，不记作真实检索成功。

## 前端发布与只读验收

前端当前由 `FORECASTLAB_FRONTEND_DIR` 指定，默认是当前 checkout 的 `frontend/dist`。发布前应核对实际读取目录。先构建到独立目录并完成相关验证，避免构建工具清空正在服务的目录：

```bash
cd /home/group8/work/forecastlab-main/frontend
npm run build -- --outDir dist-preview
```

发布时先将新版本静态资源放入正式目录，再原子替换 `index.html`，确保新入口引用的资源已存在；保留仍可能被已打开页面使用的旧哈希资源，后续再按明确范围清理。不要重新复制用户已要求删除的旧研究 HTML/JSON 专题文件，不再以旧 `run_eb86...` 为固定发布步骤。纯前端发布不重启正在执行研究的后端。

可用以下只读请求核对应用存活、依赖就绪、模型名与窗口，不会发起模型生成：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:18765/api/live
curl --noproxy '*' -fsS http://127.0.0.1:18765/api/health
curl --noproxy '*' -fsS http://127.0.0.1:18048/health
curl --noproxy '*' -fsS http://127.0.0.1:18048/v1/models
```

`model_configured` / `search_configured` 表示配置；`model_ready` / `search_ready` 和 `readiness` 表示探测结果。`search_ready=null` 表示尚不能确认搜索服务完全可用，可能只有网络探测成功。单次健康通过不能证明研究能完成、正文能取得或概率可靠。

真实端到端验收应同时核对运行终态、报告内容与合法情景概率、原文引用、桌面/窄屏显示、取消与继续的实际行为，并记录版本和运行 ID。失败案例保留其真实状态，不以已有的画布、来源或模拟结果冒充完成报告。测试数量、耗时与成功案例以当次验收记录为准，不复用旧配置的数字。

历史单卡、旧 TP=4、关闭思考或其他模型的测量只说明对应条件，不能用于证明当前四个 TP=1 副本更快或与其他模型等效。应用与模型按各自自有脚本管理，未新增整机启动服务。
