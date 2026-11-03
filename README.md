# Docker Monitor

连接单个 Docker 引擎，查看容器状态和 CPU/内存，支持启停、重启、镜像检查、手动/定时更新及 Bark 通知。沿用已确认的卡片/列表界面，支持浅色、暗色与跟随系统。主要供自用和少量分享。

源码：[weidaye1122/DockerMonitor](https://github.com/weidaye1122/DockerMonitor)；镜像：[weidaye1122/docker_monitor](https://hub.docker.com/r/weidaye1122/docker_monitor)。已有正式代码版本为 `v1.0.3`，固定镜像标签为 `1.0.3`，支持 `linux/amd64`、`linux/arm64`；正式发布时同时更新固定版本和 `latest`。改动见 [CHANGELOG](CHANGELOG.md)。

## 启动

已有部署继续使用原目录、原 Compose 项目名、端口、容器名及数据挂载。首次使用仓库内 Compose 时，在本 README 所在目录操作：

```sh
# 仅首次没有 .env 时复制；填写自己的账号密码，保留已有 .env。
if [ ! -f .env ]; then cp .env.example .env; fi
docker compose pull docker-monitor
docker compose up -d docker-monitor
docker compose ps
docker compose logs --tail=50 docker-monitor
```

默认打开 <http://127.0.0.1:9090>。仓库配置只绑定本机回环地址；确需局域网访问时才设置 `MONITOR_BIND=0.0.0.0`，并配置强密码。账号默认 `admin`，空密码仅供本机开发。登录会话有效期 12 小时，退出立即失效，服务重启后需重新登录。

Docker socket 授予程序管理该引擎容器的能力，只向信任的网络和使用者开放。无需获取源码的安装方式、完整 Compose 示例和 `docker run` 命令继续见 [Docker Hub 说明](docs/dockerhub.md#安装)。

## 数据与备份

| 启动方式 | 数据实际位置 |
|---|---|
| 仓库 `compose.yaml` | 命名卷 `monitor-data` 挂到 `/data`；实际卷名带 Compose 项目前缀，以容器挂载为准 |
| Docker Hub 的 Compose 安装示例 | 部署目录 `./data` 挂到 `/data` |
| Docker run 示例 | 主机 `/opt/docker-monitor/data` 挂到 `/data` |
| 直接运行 Python | 默认代码目录 `data/monitor.sqlite3`，可由 `DATA_PATH` 指定 |

容器内数据库默认 `/data/monitor.sqlite3`，保存策略、镜像发现记录、任务/恢复计划和通知配置，可能含 Bark Key。真实 `.env`、账号数据、数据库、日志和备份均不提交。源码位置变化不能作为新建卷或迁移业务数据的理由，更新时继续使用原项目名与原挂载。

备份先等待更新/修复任务结束，再只停止本程序，复制整个数据目录，完成后启动；被监控的业务容器继续运行。下面以仓库 Compose 为例，命令在原部署目录执行；原来使用 `-p` 或额外 Compose 文件的，所有命令都沿用原参数。任一步失败先排查，不继续升级。

```sh
backup_dir="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
docker compose stop docker-monitor
docker compose cp docker-monitor:/data "$backup_dir/data"
cp compose.yaml "$backup_dir/"
if [ -f .env ]; then cp .env "$backup_dir/.env"; fi
docker compose start docker-monitor
```

`docker compose cp` 可从已停止容器的 `/data` 复制，适用于上述命名卷和目录挂载。实际使用的 Compose 覆盖文件、镜像版本/摘要也要一并记录；备份应另存一份到独立位置，并限制访问。Docker run 部署按已核对的实际容器名使用对应的 `docker stop`、`docker cp`、`docker start`。

上述示例使用原部署目录的 `backups/`。在本机工作区做人工备份时，可将 `backup_dir` 指向 `../本地资料/备份/日期时间/`；已有部署继续沿用原备份位置。

恢复时先停止本程序、另存当前数据，核对备份可读及数据库完整性，再用备份的整份 `data` 内容恢复原挂载，保留文件权限，不与当前库的 `-wal`、`-shm` 文件混合覆盖。使用备份对应的镜像和原 Compose 配置启动，检查健康、登录、策略和通知设置，确认自动更新名单没有误指向其他容器。恢复验证先在隔离环境做，保留原数据和唯一备份。

SQLite 使用 WAL，运行时直接复制单个数据库文件可能遗漏事务；需要在线备份时使用 SQLite backup API。回退容器或镜像不会撤销应用已经写入数据卷的数据库迁移，必要时连同匹配的数据备份一起恢复。

这里备份的是本程序状态，不包含被监控应用自己的数据库和卷；更新业务容器前还需核对它们各自的备份与恢复方法。

## 更新正在运行的程序

更新容器与发布镜像分别处理。先完成上述备份并确认恢复方法、原数据挂载和可回退镜像，再在原部署目录选择已验证的固定版本（或需要跟随正式版时用 `latest`）：

```sh
docker compose pull docker-monitor
docker compose up -d --no-deps docker-monitor
docker compose ps
docker compose logs --tail=50 docker-monitor
curl -fsS http://127.0.0.1:9090/healthz
```

地址按实际部署核对，已有端口和容器名不改。异常时保留日志，用旧固定版本或旧摘要重建本程序；不要删除数据卷或执行 `compose down -v`。程序不跨固定版本标签自动升级，需要人工选择新的版本标签。

## 本地开发与文件约定

代码根目录是 `DockerMonitor/`，所有相对路径及项目命令均以它为准；外层是本机工作区，不属于代码仓库。外层用 `项目说明.md` 作为本机入口，仓库旁的 `本地资料/` 保存本机材料。继续使用现有代码目录：

| 内容 | 位置与约定 |
|---|---|
| 实际代码 | `backend/`、`web/`；依赖保留在现有 requirements 文件 |
| 配置示例、构建 | `.env.example`、`Dockerfile`、`compose.yaml`、`compose.build.yaml`；真实配置放忽略的 `.env` |
| 测试与工具 | `tests/` 放可复用测试和脱敏样例，`tools/` 放辅助脚本 |
| 原型 | `design/`、`web/preview.html`，不作为日常生成入口 |
| 本机运行数据 | `data/`；Compose 命名卷独立于代码，不能当缓存 |
| 本机材料 | 讨论放 `../本地资料/讨论/`；本次记录、日志和相关截图放 `../本地资料/验证/日期-任务/` |
| 构建与发布记录 | 构建、发布的日志和元数据放 `../本地资料/验证/发布/版本号/` |
| 临时测试与缓存 | 现有 `output/integration/` 等脚本输出及 `output/cache/`；需保留的结果再归档到外层本地资料 |
| 备份与镜像归档 | 本机人工备份放 `../本地资料/备份/`；实际产生离线镜像文件时再创建 `../本地资料/镜像归档/`；已有部署的 `backups/` 继续沿用，重要备份另存到独立位置 |
| 开发环境与说明 | `.venv/` 可重建，也可复用外层已有 `.venv`；说明沿用 README、AGENTS、CHANGELOG 和 `docs/` |

截图随对应验证任务保存，不单独预留 `截图/`；镜像归档目录不提前预留，也不要求每次构建或发布都导出离线镜像。整理时完整保留已有有效资料并修正引用链接，确认无内容的多余空目录可以移除。

外层入口和本地资料不属于代码仓库，不随 Git 同步；其他机器按需创建。本机数据、配置、环境和临时输出由 `.gitignore` 排除，`.dockerignore` 同时排除它们进入构建上下文，并防止误放入仓库的本地资料被打包。保留既有数据和输出路径，测试截图、报告和镜像归档不放源码目录混存。给接手 AI 的约定见 [AGENTS.md](AGENTS.md)。

日常流程：修改 → 必要检查 → 本地构建镜像并在本地隔离环境验证，结果先保留在本地。用户要求正式发布时，再递增版本、整理日志、提交源码并推送 Git 标签，随后一次构建并同时推送固定版本和 `latest`。开发、试用和测试均在本地进行。文档整理只核对内容和链接；代码改动跑相关基本测试；涉及更新、数据保留、回退或恢复时才增加对应的本地隔离验证。

```sh
# 尚无可用虚拟环境时再创建；本机现有 ../.venv 可以继续复用。
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
# UI 预览使用内存模拟，不连接 Docker，不操作业务容器。
.venv/bin/uvicorn tools.preview:app --host 127.0.0.1 --port 9091 --no-access-log
```

预览地址 <http://127.0.0.1:9091/>，模拟状态重启即重置。日常直接改 `web/`；`tools/build_web.py` 和 `tools/notification_ui.js` 会覆盖前端，不能作为例行构建步骤。

本地镜像沿用 `docker-monitor:local`：

```sh
docker build --build-arg VERSION=dev \
  --build-arg REVISION="$(git rev-parse HEAD)" \
  -t docker-monitor:local .
```

未提交构建只作开发试用，不作为正式源码版本证据。`compose.build.yaml` 可在专用测试环境复用；不能直接用本地构建覆盖现用容器。真实 Docker 测试仅操作本次专门创建的容器，使用专用测试引擎和全新数据，运行前先看 [现有集成脚本的限制](docs/dockerhub.md#真实-docker-验证)。

## Git 与发布

Git 默认在正式发布新版本时集中保存源码历史并同步 GitHub。日常开发、测试、文档和目录整理保留为本地待提交修改，完成任务后不自动暂存、提交、推送或打标签；查看 Git 状态和差异照常进行。用户另行明确要求 Git 提交或同步检查点时，按其要求执行。

日常可以在 `main` 本地开发，不强制 PR 或多人审批，较大改动需要隔离时再用分支。发布时审查本次版本包含的全部待发布改动，只提交已核对的发布范围，保留其他未完成或无关工作、旧版本和历史。尚未提交可以是日常任务的正常交付状态。

CHANGELOG 的“未发布”记录待发布改动，文档维护不单独升版本。正式发布时由 AI 核对 Git、CHANGELOG 和 Docker Hub 已有版本，负责递增版本号，默认递增修订号（如 `1.0.3` → `1.0.4`），用户另有指定时按要求处理；普通本地试用用 `dev`。对应关系是：`CHANGELOG X.Y.Z` → Git 标签 `vX.Y.Z` / 确切提交 → 镜像 `weidaye1122/docker_monitor:X.Y.Z`；Dockerfile 已支持 `VERSION`、`REVISION` 标签。本地验证完成后，用同一次 amd64、arm64 构建同时发布 `X.Y.Z` 和 `latest`，保留旧固定版本。上传后核对两个标签和平台清单，确认发布结果。

沿用 [现有 Docker Hub 文档中的手动发布步骤](docs/dockerhub.md#维护者发布)，不新增 GitHub 自动镜像发布、多仓库同步或定时任务。发布目标是 Docker Hub；更新正在运行的容器另行处理。

## 本地及项目 Docker 清理

- 开发结束清理本任务产生且已用完的临时文件、测试容器、网络和测试卷，先按本次标记、精确 ID 与挂载核对归属；失败测试先保存证据，再核对残留。
- 平时按需查看磁盘占用。旧测试镜像、归档、日志确认用途后逐项清理，保留在用镜像和可回退固定版本；镜像还带其他标签或被引用时保留，不强制删除。
- 共享构建缓存通常不能证明属于本项目；无法区分就保留，仅在专用构建器归属明确、已不用时整理其缓存。
- 数据卷、挂载目录、数据库、配置、恢复副本和唯一备份不是缓存。只可删除已核实属于本次测试、不被其他容器使用的临时卷。
- 不执行整机 `docker system prune`、镜像/卷全局清理、批量删容器或 `compose down -v`；不拿现有业务容器测试清理。

## 功能与保护边界

默认关闭自动更新。检测 Cron 与更新计划分开，时区 `Asia/Shanghai`，五位格式 `分 时 日 月 周`；可选择固定时点更新或发现后延迟更新。同一目标摘要不会重复计时，停止、异常、受保护或有任务的容器跳过，失败后至少间隔 15 分钟重试。

本程序及 `docker-monitor.protected=true` 容器受保护。更新保留环境、命令、端口、重启策略、网络及已有挂载，匿名卷复用；固定目标镜像，经启动/健康检查后才删除本任务旧容器，不删除卷或顺带清镜像。失败尝试恢复原容器及网络；任务与恢复计划持久化，清理按新旧容器身份核对。

支持普通 Docker/Compose 容器原位更新，不重写 Compose。Swarm、共享容器命名空间及其依赖、`links`、`volumes_from`、`AutoRemove` 不适用；固定摘要和无仓库记录的本地镜像不能跟踪标签。私有仓库沿用 Docker SDK 登录配置，不提交凭据。当前不含多主机、日志浏览和通用删容器功能。详细行为与来源见 [原有说明及历史证据](docs/operations.md)。

## HTTPS 反代与穿透隧道

反代和隧道转发整个站点（含 API、静态资源）、Cookie 和页面自定义请求头即可；无需配置 `PUBLIC_URL` 或放开 CORS。支持外部 HTTPS、非标准端口、IPv6、改写 Host 和缺少代理头。当前仅支持域名根路径，不支持 `/docker-monitor/` 子路径。

`TRUSTED_PROXY_IPS` 仅用于可信真实客户端地址和登录限流，不是登录前提。HTTPS Cookie、CSRF、Nginx 配置和 `--no-proxy-headers` 说明见 [反代详细配置](docs/dockerhub.md#反向代理与穿透隧道)。

## 验证记录

旧 README 中早期临时测试的操作、镜像 ID、验证结论和未覆盖项保留在 [历史部署与验证](docs/operations.md#历史部署与验证)，不作为当前测试或部署目标。当前验证以本地检查及发布记录为准，模拟测试与真实场景验证分别说明。
